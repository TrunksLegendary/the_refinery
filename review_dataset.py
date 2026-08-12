#!/usr/bin/env python3
"""
review_dataset.py

Third step in the pipeline: opens a local web UI to browse and hand-edit a
ShareGPT-format LoRA dataset (the output of chat_to_lora.py / clean_dataset.py),
and validates the schema before any save.

    python chat_to_lora.py export.zip -o raw_dataset.json
    python clean_dataset.py raw_dataset.json -o clean_dataset.json
    python review_dataset.py clean_dataset.json

No external dependencies — uses only the Python standard library, so nothing
needs to be pip-installed. It starts a local server (default
http://127.0.0.1:8765) and opens it in your default browser. Edit
conversations/turns, click Save; the dataset is validated against the
ShareGPT/LoRA schema (alternating human/gpt turns, non-empty text, etc.)
before anything is written to disk. Invalid data is rejected with a list of
specific errors instead of being saved.

Usage:
    python review_dataset.py INPUT.json [-o OUTPUT.json] [--port 8765] [--in-place] [--no-browser]

Options:
    -o, --output PATH   Where to save edits (default: INPUT.reviewed.json)
    --in-place           Save edits back to INPUT.json instead (a .bak backup
                          of the previous contents is kept alongside it)
    --port N             Port to serve on (default: 8765)
    --no-browser          Don't automatically open a browser tab
"""

import argparse
import json
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VALID_ROLES = ("system", "human", "gpt")

# --------------------------------------------------------------------------
# Validation — the ShareGPT/LoRA schema rules
# --------------------------------------------------------------------------

def validate_dataset(data) -> tuple[list[str], list[str], dict]:
    """Return (errors, warnings, stats). Non-empty `errors` means the dataset
    must NOT be saved as-is."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, list):
        errors.append("Top-level JSON must be an array of conversation objects.")
        return errors, warnings, {}

    total_turns = 0
    for i, conv in enumerate(data):
        label = f"conversation #{i}"
        if isinstance(conv, dict) and conv.get("id"):
            label += f" (id={conv['id']!r})"

        if not isinstance(conv, dict):
            errors.append(f"{label}: entry is not an object.")
            continue

        turns = conv.get("conversations")
        if not isinstance(turns, list) or len(turns) == 0:
            errors.append(f"{label}: missing or empty 'conversations' list.")
            continue

        prev_role = None
        human_gpt_count = 0
        first_non_system_role = None

        for j, turn in enumerate(turns):
            tlabel = f"{label}, turn {j}"
            if not isinstance(turn, dict):
                errors.append(f"{tlabel}: not an object.")
                continue

            role = turn.get("from")
            value = turn.get("value")

            if role not in VALID_ROLES:
                errors.append(f"{tlabel}: invalid 'from' value {role!r} (must be one of {VALID_ROLES}).")
                continue
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{tlabel}: empty or missing 'value' text.")
                continue

            if role == "system":
                if j != 0:
                    errors.append(f"{tlabel}: 'system' turn must be the first turn if present.")
                continue

            if first_non_system_role is None:
                first_non_system_role = role
            if role == prev_role:
                errors.append(f"{tlabel}: two consecutive '{role}' turns in a row — roles must alternate.")
            prev_role = role
            human_gpt_count += 1
            total_turns += 1

        if first_non_system_role is not None and first_non_system_role != "human":
            errors.append(f"{label}: must start with a 'human' turn (after an optional leading system turn).")

        if human_gpt_count < 2:
            warnings.append(f"{label}: only {human_gpt_count} human/gpt turn(s) — very short example.")
        if prev_role == "human":
            warnings.append(f"{label}: ends on a 'human' turn with no reply — trainers typically expect the last turn to be 'gpt'.")

    stats = {"conversations": len(data), "total_turns": total_turns}
    return errors, warnings, stats


# --------------------------------------------------------------------------
# Frontend (single static page, no external assets/CDNs required)
# --------------------------------------------------------------------------

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LoRA Dataset Reviewer</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: #14161a; color: #e6e6e6; }
  header { display: flex; align-items: center; gap: 16px; padding: 10px 16px; background: #1c1f26; border-bottom: 1px solid #2c2f38; position: sticky; top: 0; z-index: 5; }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; color: #fff; }
  header .meta { font-size: 12px; color: #9aa0ac; }
  header .spacer { flex: 1; }
  .badge { font-size: 12px; padding: 3px 8px; border-radius: 10px; font-weight: 600; }
  .badge.ok { background: #163d2b; color: #5fd58a; }
  .badge.err { background: #4a1c1c; color: #ff8080; }
  .badge.warn { background: #4a3a12; color: #ffcf6b; }
  button { cursor: pointer; border: none; border-radius: 6px; padding: 7px 14px; font-size: 13px; font-weight: 600; background: #2c2f38; color: #e6e6e6; }
  button:hover { background: #383c47; }
  button.primary { background: #3d6dfb; color: #fff; }
  button.primary:hover { background: #2f5adf; }
  button.danger { background: #4a1c1c; color: #ff8f8f; }
  button.danger:hover { background: #5c2323; }
  button:disabled { opacity: .5; cursor: default; }
  .layout { display: flex; height: calc(100vh - 52px); }
  .sidebar { width: 320px; min-width: 320px; border-right: 1px solid #2c2f38; display: flex; flex-direction: column; }
  .sidebar .search { padding: 10px; border-bottom: 1px solid #2c2f38; }
  .sidebar input[type=text] { width: 100%; padding: 7px 9px; border-radius: 6px; border: 1px solid #333743; background: #1c1f26; color: #e6e6e6; font-size: 13px; }
  .conv-list { overflow-y: auto; flex: 1; }
  .conv-item { padding: 10px 12px; border-bottom: 1px solid #22252c; cursor: pointer; }
  .conv-item:hover { background: #1c1f26; }
  .conv-item.active { background: #22283a; border-left: 3px solid #3d6dfb; }
  .conv-item .t { font-size: 13px; color: #e6e6e6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .conv-item .s { font-size: 11px; color: #8a8f9c; margin-top: 2px; }
  .conv-item.has-error .t { color: #ff8f8f; }
  .sidebar-footer { padding: 10px; border-top: 1px solid #2c2f38; }
  .main { flex: 1; overflow-y: auto; padding: 20px 28px; }
  .conv-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .conv-header input[type=text] { flex: 1; font-size: 16px; font-weight: 600; background: transparent; border: none; border-bottom: 1px solid #2c2f38; color: #fff; padding: 4px 2px; }
  .turn { border: 1px solid #2c2f38; border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
  .turn-head { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: #1c1f26; }
  .role-pill { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; padding: 3px 8px; border-radius: 5px; }
  .role-human { background: #163049; color: #6ab6ff; }
  .role-gpt { background: #163d2b; color: #5fd58a; }
  .role-system { background: #3a2f4a; color: #c79bff; }
  select { background: #1c1f26; color: #e6e6e6; border: 1px solid #333743; border-radius: 5px; font-size: 12px; padding: 2px 4px; }
  .turn-head .spacer { flex: 1; }
  .turn-head button { padding: 3px 8px; font-size: 11px; }
  textarea { width: 100%; border: none; background: #14161a; color: #e6e6e6; padding: 10px; font-size: 13px; line-height: 1.5; font-family: inherit; resize: vertical; min-height: 44px; }
  .add-turn-row { text-align: center; margin: 14px 0; }
  .empty-state { color: #8a8f9c; padding: 60px 20px; text-align: center; font-size: 14px; }
  .panel { position: fixed; right: 0; top: 52px; bottom: 0; width: 380px; background: #1c1f26; border-left: 1px solid #2c2f38; transform: translateX(100%); transition: transform .15s ease; overflow-y: auto; padding: 16px; z-index: 10; }
  .panel.open { transform: translateX(0); }
  .panel h2 { font-size: 14px; margin: 0 0 10px; }
  .panel .item { font-size: 12px; padding: 8px; border-radius: 6px; margin-bottom: 6px; cursor: pointer; }
  .panel .item.error { background: #2a1414; color: #ff9d9d; }
  .panel .item.warning { background: #2a2210; color: #ffd98a; }
  .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #1c1f26; border: 1px solid #333743; padding: 10px 18px; border-radius: 8px; font-size: 13px; z-index: 20; display: none; }
  .toast.show { display: block; }
  .toast.success { border-color: #2c6b46; color: #7fe3a5; }
  .toast.error { border-color: #7a2c2c; color: #ff9d9d; }
</style>
</head>
<body>
<header>
  <h1>LoRA Dataset Reviewer</h1>
  <div class="meta" id="fileMeta">loading…</div>
  <div class="spacer"></div>
  <div class="badge" id="statusBadge" style="display:none"></div>
  <button id="issuesBtn" style="display:none">Issues</button>
  <button id="addConvBtn">+ New conversation</button>
  <button class="primary" id="saveBtn">Save</button>
</header>
<div class="layout">
  <div class="sidebar">
    <div class="search"><input type="text" id="searchBox" placeholder="Search conversations…"></div>
    <div class="conv-list" id="convList"></div>
    <div class="sidebar-footer" id="sidebarFooter"></div>
  </div>
  <div class="main" id="main"><div class="empty-state">Select a conversation on the left.</div></div>
</div>
<div class="panel" id="issuesPanel">
  <h2>Validation issues</h2>
  <div id="issuesBody"></div>
</div>
<div class="toast" id="toast"></div>

<script>
let dataset = [];
let currentIndex = -1;
let filterText = "";
let lastValidation = null;

async function load() {
  const res = await fetch('/api/dataset');
  const data = await res.json();
  dataset = data.conversations;
  document.getElementById('fileMeta').textContent = data.filename + ' — ' + dataset.length + ' conversation(s)';
  renderList();
}

function matchesFilter(conv) {
  if (!filterText) return true;
  const hay = (conv.title || '') + ' ' + (conv.id || '') + ' ' +
    conv.conversations.map(t => t.value).join(' ');
  return hay.toLowerCase().includes(filterText.toLowerCase());
}

function errorIndexSet() {
  const s = new Set();
  if (lastValidation) {
    for (const e of lastValidation.errors) {
      const m = e.match(/^conversation #(\\d+)/);
      if (m) s.add(parseInt(m[1], 10));
    }
  }
  return s;
}

function renderList() {
  const list = document.getElementById('convList');
  list.innerHTML = '';
  const errIdx = errorIndexSet();
  dataset.forEach((conv, i) => {
    if (!matchesFilter(conv)) return;
    const div = document.createElement('div');
    div.className = 'conv-item' + (i === currentIndex ? ' active' : '') + (errIdx.has(i) ? ' has-error' : '');
    const turnCount = (conv.conversations || []).length;
    div.innerHTML = '<div class="t">' + escapeHtml(conv.title || '(untitled)') + '</div>' +
      '<div class="s">' + turnCount + ' turn(s)' + (conv.source ? ' · ' + escapeHtml(conv.source) : '') + '</div>';
    div.onclick = () => { currentIndex = i; renderList(); renderMain(); };
    list.appendChild(div);
  });
  document.getElementById('sidebarFooter').textContent = dataset.length + ' total';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderMain() {
  const main = document.getElementById('main');
  if (currentIndex < 0 || !dataset[currentIndex]) {
    main.innerHTML = '<div class="empty-state">Select a conversation on the left.</div>';
    return;
  }
  const conv = dataset[currentIndex];
  main.innerHTML = '';

  const head = document.createElement('div');
  head.className = 'conv-header';
  const titleInput = document.createElement('input');
  titleInput.type = 'text';
  titleInput.value = conv.title || '';
  titleInput.oninput = () => { conv.title = titleInput.value; renderList(); };
  const delConvBtn = document.createElement('button');
  delConvBtn.className = 'danger';
  delConvBtn.textContent = 'Delete conversation';
  delConvBtn.onclick = () => {
    if (!confirm('Delete this entire conversation?')) return;
    dataset.splice(currentIndex, 1);
    currentIndex = -1;
    renderList(); renderMain();
  };
  head.appendChild(titleInput);
  head.appendChild(delConvBtn);
  main.appendChild(head);

  (conv.conversations || []).forEach((turn, j) => {
    main.appendChild(renderTurn(conv, j));
  });

  const addRow = document.createElement('div');
  addRow.className = 'add-turn-row';
  const addBtn = document.createElement('button');
  const roles = (conv.conversations || []).map(t => t.from);
  const nextRole = roles.length && roles[roles.length - 1] === 'human' ? 'gpt' : 'human';
  addBtn.textContent = '+ Add ' + nextRole + ' turn';
  addBtn.onclick = () => {
    conv.conversations.push({from: nextRole, value: ''});
    renderMain();
  };
  addRow.appendChild(addBtn);
  main.appendChild(addRow);
}

function renderTurn(conv, j) {
  const turn = conv.conversations[j];
  const wrap = document.createElement('div');
  wrap.className = 'turn';

  const headRow = document.createElement('div');
  headRow.className = 'turn-head';

  const pill = document.createElement('span');
  pill.className = 'role-pill role-' + turn.from;
  pill.textContent = turn.from;
  headRow.appendChild(pill);

  const select = document.createElement('select');
  ['system', 'human', 'gpt'].forEach(r => {
    const opt = document.createElement('option');
    opt.value = r; opt.textContent = r;
    if (r === turn.from) opt.selected = true;
    select.appendChild(opt);
  });
  select.onchange = () => {
    turn.from = select.value;
    renderMain();
  };
  headRow.appendChild(select);

  const spacer = document.createElement('div');
  spacer.className = 'spacer';
  headRow.appendChild(spacer);

  const delBtn = document.createElement('button');
  delBtn.textContent = 'Delete turn';
  delBtn.onclick = () => { conv.conversations.splice(j, 1); renderMain(); };
  headRow.appendChild(delBtn);

  wrap.appendChild(headRow);

  const textarea = document.createElement('textarea');
  textarea.value = turn.value || '';
  textarea.rows = Math.min(20, Math.max(2, Math.ceil((turn.value || '').length / 80)));
  textarea.oninput = () => { turn.value = textarea.value; };
  wrap.appendChild(textarea);

  return wrap;
}

document.getElementById('searchBox').addEventListener('input', (e) => {
  filterText = e.target.value;
  renderList();
});

document.getElementById('addConvBtn').addEventListener('click', () => {
  dataset.push({id: 'new_' + Date.now(), source: 'manual', title: 'New conversation',
    conversations: [{from: 'human', value: ''}, {from: 'gpt', value: ''}]});
  currentIndex = dataset.length - 1;
  renderList(); renderMain();
});

function showToast(msg, kind) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + kind;
  setTimeout(() => { t.className = 'toast'; }, 4500);
}

function renderIssues() {
  const badge = document.getElementById('statusBadge');
  const issuesBtn = document.getElementById('issuesBtn');
  if (!lastValidation) { badge.style.display = 'none'; issuesBtn.style.display = 'none'; return; }
  const { errors, warnings } = lastValidation;
  badge.style.display = 'inline-block';
  issuesBtn.style.display = errors.length || warnings.length ? 'inline-block' : 'none';
  if (errors.length) {
    badge.className = 'badge err';
    badge.textContent = errors.length + ' error(s)';
  } else if (warnings.length) {
    badge.className = 'badge warn';
    badge.textContent = warnings.length + ' warning(s)';
  } else {
    badge.className = 'badge ok';
    badge.textContent = 'Valid';
  }
  const body = document.getElementById('issuesBody');
  body.innerHTML = '';
  errors.forEach(e => {
    const d = document.createElement('div');
    d.className = 'item error';
    d.textContent = e;
    body.appendChild(d);
  });
  warnings.forEach(w => {
    const d = document.createElement('div');
    d.className = 'item warning';
    d.textContent = w;
    body.appendChild(d);
  });
  renderList();
}

document.getElementById('issuesBtn').addEventListener('click', () => {
  document.getElementById('issuesPanel').classList.toggle('open');
});

document.getElementById('saveBtn').addEventListener('click', async () => {
  const saveBtn = document.getElementById('saveBtn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Validating…';
  try {
    const res = await fetch('/api/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({conversations: dataset})
    });
    const result = await res.json();
    lastValidation = {errors: result.errors || [], warnings: result.warnings || []};
    renderIssues();
    if (result.saved) {
      showToast('Saved to ' + result.path, 'success');
    } else {
      showToast('Not saved — ' + result.errors.length + ' validation error(s). Click "Issues" for details.', 'error');
      document.getElementById('issuesPanel').classList.add('open');
    }
  } catch (err) {
    showToast('Save failed: ' + err, 'error');
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save';
  }
});

load();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------

STATE = {"input_path": None, "output_path": None, "conversations": [], "in_place": False}


def load_input(path: Path) -> list:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "conversations" in data and isinstance(data["conversations"], list):
        # tolerate a single {"conversations": [...]} wrapper at top level
        maybe = data["conversations"]
        if maybe and isinstance(maybe[0], dict) and "conversations" in maybe[0]:
            return maybe
    if not isinstance(data, list):
        raise ValueError("Input file must contain a JSON array of conversation objects.")
    return data


def save_output(conversations: list, output_path: Path):
    if output_path.exists():
        backup = output_path.with_suffix(output_path.suffix + ".bak")
        shutil.copyfile(output_path, backup)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(conversations, f, ensure_ascii=False, indent=2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send_html(INDEX_HTML)
        elif self.path == "/api/dataset":
            self._send_json({
                "filename": STATE["input_path"].name,
                "conversations": STATE["conversations"],
            })
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        try:
            payload = self._read_json_body()
        except Exception as e:
            self._send_json({"error": f"invalid JSON body: {e}"}, status=400)
            return

        conversations = payload.get("conversations", [])

        if self.path == "/api/validate":
            errors, warnings, stats = validate_dataset(conversations)
            self._send_json({"valid": not errors, "errors": errors, "warnings": warnings, "stats": stats})
            return

        if self.path == "/api/save":
            errors, warnings, stats = validate_dataset(conversations)
            if errors:
                self._send_json({"saved": False, "errors": errors, "warnings": warnings, "stats": stats})
                return
            try:
                save_output(conversations, STATE["output_path"])
            except Exception as e:
                self._send_json({"saved": False, "errors": [f"write failed: {e}"], "warnings": warnings})
                return
            STATE["conversations"] = conversations
            self._send_json({
                "saved": True,
                "path": str(STATE["output_path"]),
                "errors": [],
                "warnings": warnings,
                "stats": stats,
            })
            return

        self._send_json({"error": "not found"}, status=404)


def main():
    ap = argparse.ArgumentParser(description="Review and hand-edit a ShareGPT LoRA dataset in a local web UI.")
    ap.add_argument("input", help="ShareGPT JSON file to review (output of chat_to_lora.py / clean_dataset.py)")
    ap.add_argument("-o", "--output", default=None, help="where to save edits (default: INPUT.reviewed.json)")
    ap.add_argument("--in-place", action="store_true", help="save edits back to the input file (keeps a .bak backup)")
    ap.add_argument("--port", type=int, default=8765, help="port to serve on (default: 8765)")
    ap.add_argument("--no-browser", action="store_true", help="don't automatically open a browser tab")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.in_place:
        output_path = input_path
    elif args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_name(input_path.stem + ".reviewed" + input_path.suffix)

    try:
        conversations = load_input(input_path)
    except Exception as e:
        print(f"Could not load {input_path}: {e}", file=sys.stderr)
        sys.exit(1)

    errors, warnings, stats = validate_dataset(conversations)
    print(f"Loaded {stats.get('conversations', len(conversations))} conversation(s) from {input_path}")
    if errors:
        print(f"  ! {len(errors)} existing validation error(s) — you'll need to fix these before saving.")
    if warnings:
        print(f"  ! {len(warnings)} warning(s).")

    STATE["input_path"] = input_path
    STATE["output_path"] = output_path
    STATE["conversations"] = conversations
    STATE["in_place"] = args.in_place

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Serving dataset reviewer at {url}")
    print(f"Edits will be saved to: {output_path}")
    print("Press Ctrl+C to stop.")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
