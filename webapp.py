#!/usr/bin/env python3
"""
webapp.py — Refinery

One combined local web app for the whole pipeline:

    Extract  ->  Clean  ->  Review & Edit  ->  Export

It's a wizard-style UI wrapped around chat_to_lora.py, clean_dataset.py, and
review_dataset.py — imported as modules, not reimplemented, so the logic
(and any future bugfixes) stays in one place. Each of those three scripts
still works fine standalone from the command line if you'd rather script the
pipeline; this app is a friendlier way to drive the same steps interactively
in one place, with a live preview and edit view before anything is saved.

Usage:
    python webapp.py [--port 8765] [--no-browser]

No external dependencies — pure Python standard library on the backend, and
the frontend has no CDN/build step either, so it works fully offline.
"""

import argparse
import json
import sys
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat_to_lora          # noqa: E402
import clean_dataset          # noqa: E402
import review_dataset         # noqa: E402
import export_formats         # noqa: E402


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Refinery</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #08090b;
    --surface: #0e1012;
    --surface-2: #16181b;
    --surface-3: #1d1f23;
    --border: rgba(255,255,255,.08);
    --border-strong: rgba(255,255,255,.14);
    --text: #f2f3f5;
    --text-dim: #8b8d97;
    --text-faint: #5c5e68;
    --accent: #5e6ad2;
    --accent-text: #eef0ff;
    --accent-dim: rgba(94,106,210,.16);
    --success: #4cc38a;
    --success-bg: rgba(76,195,138,.12);
    --warn: #e2a33d;
    --warn-bg: rgba(226,163,61,.12);
    --error: #e5534b;
    --error-bg: rgba(229,83,75,.13);
    --radius: 10px;
    --radius-sm: 7px;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html { font-feature-settings: "tnum" 1, "cv11" 1; }
  body {
    margin: 0; min-height: 100vh;
    font-family: -apple-system, "Segoe UI", Inter, Roboto, Arial, sans-serif;
    background: radial-gradient(1000px 420px at 50% -12%, rgba(94,106,210,.08), transparent 65%), var(--bg);
    color: var(--text);
    -webkit-font-smoothing: antialiased;
  }
  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background-color: rgba(255,255,255,.14); border-radius: 8px; border: 2px solid var(--bg); background-clip: padding-box; }
  ::-webkit-scrollbar-thumb:hover { background-color: rgba(255,255,255,.22); }
  ::selection { background: var(--accent-dim); color: var(--accent-text); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  header { display: flex; align-items: center; gap: 14px; padding: 14px 28px; border-bottom: 1px solid var(--border); background: rgba(10,11,13,.7); backdrop-filter: blur(8px); position: sticky; top: 0; z-index: 15; }
  .brand { display: flex; align-items: center; gap: 9px; font-weight: 650; font-size: 14.5px; letter-spacing: -.01em; }
  .brand .logo { width: 22px; height: 22px; border-radius: 6px; background: var(--accent); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 800; font-family: var(--mono); }
  header .subtitle { color: var(--text-faint); font-size: 12.5px; padding-left: 12px; border-left: 1px solid var(--border); }
  header .spacer { flex: 1; }
  .container { max-width: 1180px; margin: 0 auto; padding: 26px 24px 60px; }

  .stepper { display: flex; align-items: center; margin-bottom: 32px; padding: 4px; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
  .step { display: flex; align-items: center; gap: 9px; cursor: pointer; padding: 9px 16px; border-radius: 9px; opacity: .45; transition: opacity .15s, background .15s; flex: 1; }
  .step:hover { opacity: .7; }
  .step.reachable { opacity: .65; }
  .step.active { opacity: 1; background: var(--surface-3); }
  .step .num { width: 20px; height: 20px; border-radius: 5px; background: var(--surface-3); border: 1px solid var(--border-strong); display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; font-family: var(--mono); color: var(--text-dim); transition: all .15s; flex-shrink: 0; }
  .step.active .num { background: var(--accent); color: #fff; border-color: var(--accent); }
  .step.done .num { background: var(--success-bg); color: var(--success); border-color: transparent; }
  .step .label { font-size: 12.5px; font-weight: 600; color: var(--text-dim); letter-spacing: -.01em; }
  .step.active .label { color: var(--text); }
  .step.done .label { color: var(--success); }
  .stepline { width: 20px; height: 1px; background: var(--border); margin: 0 2px; flex-shrink: 0; }

  .panel { display: none; }
  .panel.visible { display: block; animation: fadein .15s ease; }
  @keyframes fadein { from { opacity: 0; transform: translateY(3px);} to { opacity: 1; transform: none; } }

  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px 22px; margin-bottom: 16px; box-shadow: inset 0 1px 0 rgba(255,255,255,.02); }
  .eyebrow { font-size: 10.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); margin-bottom: 6px; font-family: var(--mono); }
  .card h2 { margin: 0 0 6px; font-size: 16px; font-weight: 650; letter-spacing: -.01em; }
  .card .hint { color: var(--text-dim); font-size: 12.5px; margin: 0 0 16px; line-height: 1.55; }

  textarea, input[type=text], input[type=number], select {
    width: 100%; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm);
    color: var(--text); padding: 8px 10px; font-size: 13px; font-family: inherit;
  }
  textarea { font-family: var(--mono); resize: vertical; line-height: 1.5; }
  textarea:focus, input:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
  label { font-size: 12.5px; color: var(--text-dim); }

  .opts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px 20px; margin: 16px 0; }
  .opts-grid label { font-size: 12.5px; color: var(--text-dim); display: flex; flex-direction: column; gap: 6px; }
  .opts-grid label.checkbox { flex-direction: row; align-items: center; gap: 8px; font-size: 13px; }
  .opts-grid input[type=checkbox] { width: 15px; height: 15px; accent-color: var(--accent); }

  button { cursor: pointer; border-radius: var(--radius-sm); padding: 8px 15px; font-size: 12.5px; font-weight: 600; background: var(--surface-3); color: var(--text); border: 1px solid var(--border-strong); transition: background .12s, border-color .12s, opacity .12s; letter-spacing: -.005em; }
  button:hover { background: #24262b; border-color: rgba(255,255,255,.2); }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button.primary:hover { background: #6c77dc; border-color: #6c77dc; }
  button.ghost { background: transparent; border: 1px solid var(--border-strong); }
  button.ghost:hover { background: var(--surface-2); }
  button.danger { background: var(--error-bg); color: var(--error); border-color: transparent; }
  button.danger:hover { background: rgba(229,83,75,.22); }
  button.small { padding: 5px 10px; font-size: 11.5px; }
  button:disabled { opacity: .4; cursor: default; }

  .result { margin-top: 16px; }
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-top: 14px; }
  .stat-card { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 13px; }
  .stat-card .n { font-size: 20px; font-weight: 700; font-family: var(--mono); letter-spacing: -.02em; }
  .stat-card .l { font-size: 11px; color: var(--text-dim); margin-top: 3px; letter-spacing: -.005em; }
  .stat-card.good .n { color: var(--success); }
  .stat-card.bad .n { color: var(--error); }

  .banner { border-radius: var(--radius-sm); padding: 10px 13px; font-size: 12.5px; margin-top: 12px; line-height: 1.5; }
  .banner.error { background: var(--error-bg); color: #ff9d97; }
  .banner.success { background: var(--success-bg); color: var(--success); }
  .banner.warn { background: var(--warn-bg); color: var(--warn); }
  .banner ul { margin: 6px 0 0; padding-left: 18px; }

  .continue-row { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }

  /* Folder scan */
  .folder-scan { margin: 12px 0 4px; padding: 13px; background: var(--surface-2); border: 1px dashed var(--border-strong); border-radius: var(--radius); }
  .folder-scan-row { display: flex; gap: 8px; }
  .folder-scan-row input { flex: 1; font-family: var(--mono); font-size: 12.5px; }
  .scan-results { margin-top: 12px; }
  .scan-empty { font-size: 12px; color: var(--text-faint); padding: 4px 2px; }
  .scan-item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: var(--radius-sm); background: var(--surface); border: 1px solid var(--border); margin-bottom: 6px; }
  .scan-item input[type=checkbox] { width: 15px; height: 15px; accent-color: var(--accent); flex-shrink: 0; }
  .scan-item .info { flex: 1; min-width: 0; }
  .scan-item .path { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--mono); }
  .scan-item .meta { font-size: 10.5px; color: var(--text-faint); margin-top: 2px; }
  .fmt-pill { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; padding: 2px 6px; border-radius: 4px; flex-shrink: 0; font-family: var(--mono); }
  .fmt-pill.chatgpt { background: rgba(106,182,255,.14); color: #7fc2ff; }
  .fmt-pill.claude { background: rgba(199,155,255,.14); color: #cba6ff; }
  .fmt-pill.unrecognized { background: var(--error-bg); color: var(--error); }
  .scan-actions { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }

  /* Review step */
  .review-layout { display: flex; gap: 14px; height: 620px; }
  .review-sidebar { width: 290px; min-width: 290px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); display: flex; flex-direction: column; overflow: hidden; }
  .review-sidebar .search { padding: 10px; border-bottom: 1px solid var(--border); }
  .conv-list { overflow-y: auto; flex: 1; }
  .conv-item { padding: 9px 12px; border-bottom: 1px solid var(--border); cursor: pointer; border-left: 2px solid transparent; }
  .conv-item:hover { background: var(--surface-2); }
  .conv-item.active { background: var(--accent-dim); border-left-color: var(--accent); }
  .conv-item .t { font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .conv-item .s { font-size: 10.5px; color: var(--text-faint); margin-top: 2px; font-family: var(--mono); }
  .conv-item.has-error .t { color: var(--error); }
  .review-main { flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow-y: auto; padding: 18px 20px; }
  .conv-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .conv-header input[type=text] { flex: 1; font-size: 14px; font-weight: 600; }
  .turn { border: 1px solid var(--border); border-radius: var(--radius-sm); margin-bottom: 9px; overflow: hidden; }
  .turn-head { display: flex; align-items: center; gap: 8px; padding: 6px 9px; background: var(--surface-2); }
  .role-pill { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; padding: 3px 7px; border-radius: 4px; font-family: var(--mono); }
  .role-human { background: rgba(106,182,255,.14); color: #7fc2ff; }
  .role-gpt { background: rgba(76,195,138,.14); color: #6fdba8; }
  .role-system { background: rgba(199,155,255,.14); color: #cba6ff; }
  select { width: auto; padding: 3px 6px; font-size: 11.5px; border-radius: 5px; }
  .turn-head .spacer { flex: 1; }
  .turn-head button { padding: 3px 9px; font-size: 11px; }
  .turn textarea { border: none; border-radius: 0; background: var(--surface); padding: 10px; font-family: inherit; min-height: 44px; }
  .add-turn-row { text-align: center; margin: 14px 0 4px; }
  .empty-state { color: var(--text-faint); padding: 60px 20px; text-align: center; font-size: 13px; }
  .issues-box { margin-top: 12px; max-height: 160px; overflow-y: auto; }
  .issues-box .item { font-size: 11.5px; padding: 7px 10px; border-radius: var(--radius-sm); margin-bottom: 5px; font-family: var(--mono); }
  .issues-box .item.error { background: var(--error-bg); color: #ff9d97; }
  .issues-box .item.warning { background: var(--warn-bg); color: #f0c179; }

  /* Merge step */
  .dup-group { border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 12px; overflow: hidden; }
  .dup-group-head { padding: 8px 12px; background: var(--surface-2); font-size: 11.5px; color: var(--text-dim); font-family: var(--mono); }
  .dup-item { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-top: 1px solid var(--border); }
  .dup-item input[type=checkbox] { width: 15px; height: 15px; accent-color: var(--accent); flex-shrink: 0; }
  .dup-item .info { flex: 1; min-width: 0; }
  .dup-item .t { font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dup-item .m { font-size: 10.5px; color: var(--text-faint); margin-top: 2px; font-family: var(--mono); }
  .dup-item.recommended .t { color: var(--success); }
  .file-tag { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: var(--surface-3); color: var(--text-dim); font-family: var(--mono); flex-shrink: 0; }

  .toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); background: var(--surface-3); border: 1px solid var(--border-strong); padding: 10px 18px; border-radius: var(--radius-sm); font-size: 12.5px; z-index: 30; display: none; box-shadow: 0 12px 30px rgba(0,0,0,.5); }
  .toast.show { display: block; }
  .toast.success { border-color: var(--success); color: var(--success); }
  .toast.error { border-color: var(--error); color: #ff9d97; }
</style>
</head>
<body>
<header>
  <div class="brand"><span class="logo">R</span> Refinery</div>
  <div class="subtitle">Crude export &rarr; refined training data</div>
  <div class="spacer"></div>
  <button class="ghost" id="resetBtn">Start over</button>
</header>

<div class="container">
  <nav class="stepper" id="stepper">
    <div class="step" data-step="extract"><span class="num">1</span><span class="label">Extract</span></div>
    <div class="stepline"></div>
    <div class="step" data-step="clean"><span class="num">2</span><span class="label">Clean</span></div>
    <div class="stepline"></div>
    <div class="step" data-step="merge"><span class="num">3</span><span class="label">Merge</span></div>
    <div class="stepline"></div>
    <div class="step" data-step="review"><span class="num">4</span><span class="label">Review</span></div>
    <div class="stepline"></div>
    <div class="step" data-step="export"><span class="num">5</span><span class="label">Export</span></div>
  </nav>

  <!-- STEP 1: EXTRACT -->
  <div class="panel" id="panel-extract">
    <div class="card">
      <div class="eyebrow">Step 1</div>
      <h2>Extract from your export</h2>
      <p class="hint">Paste the path to your ChatGPT or Claude.ai export &mdash; a <code>conversations.json</code> file, the export <code>.zip</code>, or a folder. One path per line to combine multiple exports; format is auto-detected.</p>
      <textarea id="inputPaths" rows="3" placeholder="C:\Users\you\Downloads\conversations.json"></textarea>

      <div class="folder-scan">
        <div class="folder-scan-row">
          <input type="text" id="folderScanPath" placeholder="Or paste a folder to scan, e.g. C:\Users\you\Downloads">
          <button id="browseFolderBtn">Browse&hellip;</button>
          <button id="scanFolderBtn">Scan folder</button>
        </div>
        <div id="folderScanResults"></div>
      </div>

      <div class="opts-grid">
        <label class="checkbox"><input type="checkbox" id="optIncludeSystem"> Include system prompts</label>
        <label class="checkbox"><input type="checkbox" id="optIncludeTool"> Include tool/function messages</label>
        <label>Min turns to keep<input type="number" id="optMinTurnsExtract" value="2" min="0"></label>
        <label>Max chars per message<input type="number" id="optMaxChars" placeholder="no limit" min="1"></label>
      </div>
      <button class="primary" id="runExtractBtn">Run extraction</button>
      <div class="result" id="extractResult"></div>
    </div>
  </div>

  <!-- STEP 2: CLEAN -->
  <div class="panel" id="panel-clean">
    <div class="card">
      <div class="eyebrow">Step 2</div>
      <h2>Clean &amp; optimize</h2>
      <p class="hint">Dedupe, strip boilerplate disclaimers, and filter out low-content conversations. You can skip this and go straight to Review if you'd rather.</p>
      <div class="opts-grid">
        <label>Min turns<input type="number" id="optMinTurnsClean" value="2" min="0"></label>
        <label>Min words<input type="number" id="optMinWords" value="8" min="0"></label>
        <label>Near-duplicate threshold (0&ndash;1, 0=off)<input type="number" id="optNearDup" value="0.9" step="0.05" min="0" max="1"></label>
        <label>Max total characters<input type="number" id="optMaxTotalChars" placeholder="no limit" min="1"></label>
        <label class="checkbox"><input type="checkbox" id="optDedupExact" checked> Remove exact duplicates</label>
        <label class="checkbox"><input type="checkbox" id="optStripBoilerplate" checked> Strip canned AI disclaimers</label>
        <label class="checkbox"><input type="checkbox" id="optDropRefusals"> Drop refusal-only replies</label>
      </div>
      <div style="display:flex; gap:10px;">
        <button class="primary" id="runCleanBtn">Run cleaning</button>
        <button class="ghost" id="skipCleanBtn">Skip &rarr; go to Merge</button>
      </div>
      <div class="result" id="cleanResult"></div>
    </div>
  </div>

  <!-- STEP 3: MERGE -->
  <div class="panel" id="panel-merge">
    <div class="card">
      <div class="eyebrow">Step 3</div>
      <h2>Merge with other refined datasets</h2>
      <p class="hint">Already have other cleaned ShareGPT JSON files &mdash; from a previous run, or another export entirely? Add their paths below to check for duplicate or overlapping conversations across files before combining everything into one dataset. Your current working set from Extract/Clean (if any) is included automatically. This step is optional &mdash; skip it to just continue with what you've got.</p>
      <textarea id="mergePaths" rows="3" placeholder="C:\path\to\another_clean_dataset.json"></textarea>
      <div style="display:flex; gap:10px; margin-top:12px;">
        <button class="primary" id="loadMergeFilesBtn">Load &amp; analyze overlap</button>
        <button class="ghost" id="skipMergeBtn">Skip &rarr; go to Review</button>
      </div>
      <div class="result" id="mergeLoadResult"></div>
    </div>
    <div class="card" id="mergeAnalysisCard" style="display:none;">
      <div class="eyebrow">Overlap report</div>
      <h2 id="mergeSummary" style="margin-bottom:12px;"></h2>
      <p class="hint">Each group below is a set of exact or near-duplicate conversations, possibly from different files. The recommended copy to keep is pre-checked (longest/most content); adjust as you like &mdash; unchecked copies are dropped when you apply.</p>
      <div id="mergeGroups"></div>
      <div class="continue-row">
        <button class="primary" id="applyMergeBtn">Apply &amp; continue to Review &rarr;</button>
      </div>
    </div>
  </div>

  <!-- STEP 4: REVIEW -->
  <div class="panel" id="panel-review">
    <div class="card" style="margin-bottom:12px; display:flex; align-items:center; gap:14px;">
      <div>
        <div class="eyebrow" style="margin-bottom:2px;">Step 4</div>
        <h2 style="margin:0;">Review &amp; edit</h2>
      </div>
      <div style="flex:1"></div>
      <span id="reviewBadge" class="banner" style="display:none; margin:0; padding:6px 12px;"></span>
      <button class="small" id="checkValidityBtn">Check validity</button>
    </div>
    <div class="review-layout">
      <div class="review-sidebar">
        <div class="search"><input type="text" id="searchBox" placeholder="Search conversations&hellip;"></div>
        <div class="conv-list" id="convList"></div>
      </div>
      <div class="review-main" id="reviewMain"><div class="empty-state">Select a conversation on the left.</div></div>
    </div>
    <div class="card issues-box" id="issuesCard" style="display:none;">
      <div id="issuesBody"></div>
    </div>
    <div class="continue-row">
      <button class="ghost" id="addConvBtn">+ New conversation</button>
      <button class="primary" id="toExportBtn">Continue to Export &rarr;</button>
    </div>
  </div>

  <!-- STEP 5: EXPORT -->
  <div class="panel" id="panel-export">
    <div class="card">
      <div class="eyebrow">Step 5</div>
      <h2>Validate &amp; export</h2>
      <p class="hint">The dataset is checked against the LoRA/ShareGPT schema before anything is written &mdash; if there are errors, nothing gets saved.</p>
      <label>Export format
        <select id="exportFormat"></select>
      </label>
      <p class="hint" id="formatDescription" style="margin-top:8px;"></p>
      <label>Output file path
        <input type="text" id="outputPath" placeholder="C:\path\to\lora_dataset.json">
      </label>
      <div class="opts-grid">
        <label>Train/val split ratio<input type="number" id="optSplit" step="0.05" min="0" max="1" placeholder="blank = no split"></label>
        <label class="checkbox"><input type="checkbox" id="optJsonl"> Write JSON Lines (.jsonl)</label>
      </div>
      <button class="primary" id="saveBtn">Validate &amp; Save</button>
      <div class="result" id="saveResult"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let dataset = [];
let currentIndex = -1;
let filterText = '';
let lastValidation = null;
let hasExtracted = false, hasCleaned = false, hasMerged = false, reviewed = false;
let suggestedDir = '';
let currentStep = 'extract';

function el(id) { return document.getElementById(id); }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showToast(msg, kind) {
  const t = el('toast'); t.textContent = msg; t.className = 'toast show ' + kind;
  setTimeout(() => { t.className = 'toast'; }, 4500);
}

// ---------- stepper / navigation ----------
function reachable(step) {
  if (step === 'extract') return true;
  if (step === 'clean') return hasExtracted;
  if (step === 'merge') return true;
  if (step === 'review') return dataset.length > 0;
  if (step === 'export') return dataset.length > 0;
  return false;
}
function goToStep(step) {
  if (!reachable(step)) { showToast('Run the previous step first.', 'error'); return; }
  currentStep = step;
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('visible'));
  el('panel-' + step).classList.add('visible');
  document.querySelectorAll('.step').forEach(s => {
    const st = s.dataset.step;
    s.classList.toggle('active', st === step);
    s.classList.toggle('reachable', reachable(st));
    s.classList.toggle('done',
      (st === 'extract' && hasExtracted) ||
      (st === 'clean' && hasCleaned) ||
      (st === 'merge' && hasMerged) ||
      (st === 'review' && reviewed) ||
      false);
  });
  if (step === 'review') { renderList(); renderMain(); }
}
document.querySelectorAll('.step').forEach(s => s.addEventListener('click', () => goToStep(s.dataset.step)));
el('resetBtn').addEventListener('click', () => {
  if (!confirm('Start over? This clears the current dataset (nothing on disk is touched).')) return;
  dataset = []; currentIndex = -1; hasExtracted = hasCleaned = hasMerged = reviewed = false; lastValidation = null;
  el('extractResult').innerHTML = ''; el('cleanResult').innerHTML = ''; el('saveResult').innerHTML = '';
  el('mergeLoadResult').innerHTML = ''; el('mergeAnalysisCard').style.display = 'none';
  goToStep('extract');
});

function confirmDiscardIfReviewed() {
  if (!reviewed) return true;
  return confirm('You have manual edits from the Review step. Re-running this step will discard them. Continue?');
}

// ---------- STEP 1: extract ----------
el('runExtractBtn').addEventListener('click', async () => {
  if (!confirmDiscardIfReviewed()) return;
  const inputs = el('inputPaths').value.split('\n').map(s => s.trim()).filter(Boolean);
  if (!inputs.length) { showToast('Enter at least one file/zip/folder path.', 'error'); return; }
  const options = {
    include_system: el('optIncludeSystem').checked,
    include_tool: el('optIncludeTool').checked,
    min_turns: parseInt(el('optMinTurnsExtract').value || '2', 10),
    max_chars: el('optMaxChars').value ? parseInt(el('optMaxChars').value, 10) : null,
  };
  const btn = el('runExtractBtn'); btn.disabled = true; btn.textContent = 'Extracting\u2026';
  try {
    const res = await fetch('/api/extract', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({inputs, options})});
    const result = await res.json();
    if (result.error) { showToast(result.error, 'error'); return; }
    dataset = result.dataset;
    hasExtracted = true; hasCleaned = false; reviewed = false;
    suggestedDir = result.suggested_dir || '';
    if (!el('outputPath').value) {
      const sep = suggestedDir.includes('\\') ? '\\' : '/';
      el('outputPath').value = suggestedDir ? (suggestedDir + sep + 'lora_dataset.json') : 'lora_dataset.json';
    }
    renderExtractResult(result);
    showToast('Extracted ' + dataset.length + ' conversation(s).', 'success');
    goToStep('extract');
  } catch (e) {
    showToast('Extraction failed: ' + e, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Run extraction';
  }
});

// ---------- folder scan ----------
let lastScanResults = [];

async function runScan(folder) {
  if (!folder) { showToast('Enter a folder path to scan.', 'error'); return; }
  const btn = el('scanFolderBtn'); btn.disabled = true; btn.textContent = 'Scanning…';
  try {
    const res = await fetch('/api/scan-folder', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path: folder})});
    const result = await res.json();
    if (!result.exists) { showToast('That path does not exist.', 'error'); el('folderScanResults').innerHTML = ''; return; }
    if (!result.is_dir) { showToast('That path is a file, not a folder — add it directly to the box above instead.', 'error'); el('folderScanResults').innerHTML = ''; return; }
    lastScanResults = result.candidates || [];
    renderScanResults();
  } catch (e) {
    showToast('Scan failed: ' + e, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Scan folder';
  }
}

el('browseFolderBtn').addEventListener('click', async () => {
  const btn = el('browseFolderBtn'); btn.disabled = true; btn.textContent = 'Waiting for dialog…';
  try {
    const res = await fetch('/api/browse-folder', {method:'POST', headers:{'Content-Type':'application/json'}, body: '{}'});
    const result = await res.json();
    if (result.error) { showToast(result.error, 'error'); return; }
    if (!result.path) return; // user cancelled the dialog
    el('folderScanPath').value = result.path;
    runScan(result.path);
  } catch (e) {
    showToast('Could not open folder dialog: ' + e, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Browse…';
  }
});

el('scanFolderBtn').addEventListener('click', () => runScan(el('folderScanPath').value.trim()));

function renderScanResults() {
  const box = el('folderScanResults');
  if (!lastScanResults.length) {
    box.innerHTML = '<div class="scan-results"><div class="scan-empty">No .json or .zip files found in that folder.</div></div>';
    return;
  }
  let html = '<div class="scan-results">';
  lastScanResults.forEach((c, i) => {
    const recognized = c.format === 'chatgpt' || c.format === 'claude';
    const pillClass = recognized ? c.format : 'unrecognized';
    const pillLabel = recognized ? c.format : 'unrecognized';
    const meta = recognized
      ? (c.conversation_count != null ? c.conversation_count + ' conversation(s)' : '') + (c.detail ? ' · ' + escapeHtml(c.detail) : '')
      : (c.detail ? escapeHtml(c.detail) : 'no ChatGPT/Claude conversations detected') + (c.error ? ' — ' + escapeHtml(c.error) : '');
    html += '<label class="scan-item">' +
      '<input type="checkbox" data-idx="' + i + '" ' + (recognized ? 'checked' : '') + '>' +
      '<span class="fmt-pill ' + pillClass + '">' + escapeHtml(pillLabel) + '</span>' +
      '<span class="info"><div class="path">' + escapeHtml(c.name) + '</div><div class="meta">' + meta + ' · ' + formatBytes(c.size) + '</div></span>' +
      '</label>';
  });
  html += '<div class="scan-actions"><span class="scan-empty">' + lastScanResults.length + ' file(s) found</span>' +
    '<button class="primary small" id="addSelectedBtn">Add selected to input list</button></div></div>';
  box.innerHTML = html;
  el('addSelectedBtn').addEventListener('click', addSelectedScanResults);
}

function addSelectedScanResults() {
  const checked = Array.from(document.querySelectorAll('#folderScanResults input[type=checkbox]:checked'))
    .map(cb => lastScanResults[parseInt(cb.dataset.idx, 10)].path);
  if (!checked.length) { showToast('No files selected.', 'error'); return; }
  const existing = el('inputPaths').value.split('\n').map(s => s.trim()).filter(Boolean);
  const merged = Array.from(new Set([...existing, ...checked]));
  el('inputPaths').value = merged.join('\n');
  showToast('Added ' + checked.length + ' file(s) to the input list.', 'success');
}

function formatBytes(n) {
  if (n == null) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024*1024) return (n/1024).toFixed(1) + ' KB';
  return (n/1024/1024).toFixed(1) + ' MB';
}

function renderExtractResult(result) {
  const s = result.stats || {};
  let html = '';
  if (result.missing && result.missing.length) {
    html += '<div class="banner error">Path(s) not found: ' + result.missing.map(escapeHtml).join(', ') + '</div>';
  }
  html += '<div class="stat-grid">' +
    statCard(s.conversations_seen, 'Seen', '') +
    statCard(s.conversations_kept, 'Kept', 'good') +
    statCard(s.conversations_dropped_empty, 'Dropped (empty)', '') +
    statCard(s.conversations_dropped_short, 'Dropped (too short)', '') +
    statCard(s.unrecognized_files, 'Unrecognized files', s.unrecognized_files ? 'bad' : '') +
    '</div>';
  html += '<div class="continue-row"><button class="primary" onclick="goToStep(\'clean\')">Continue to Clean &rarr;</button></div>';
  el('extractResult').innerHTML = html;
}

function statCard(n, label, cls) {
  return '<div class="stat-card ' + (cls||'') + '"><div class="n">' + (n === undefined ? '&mdash;' : n) + '</div><div class="l">' + escapeHtml(label) + '</div></div>';
}

// ---------- STEP 2: clean ----------
el('runCleanBtn').addEventListener('click', async () => {
  if (!hasExtracted) { showToast('Run extraction first.', 'error'); return; }
  if (!confirmDiscardIfReviewed()) return;
  const options = {
    min_turns: parseInt(el('optMinTurnsClean').value || '2', 10),
    min_words: parseInt(el('optMinWords').value || '8', 10),
    near_dup_threshold: parseFloat(el('optNearDup').value || '0.9'),
    max_total_chars: el('optMaxTotalChars').value ? parseInt(el('optMaxTotalChars').value, 10) : null,
    dedup_exact: el('optDedupExact').checked,
    strip_boilerplate: el('optStripBoilerplate').checked,
    drop_refusals: el('optDropRefusals').checked,
  };
  const btn = el('runCleanBtn'); btn.disabled = true; btn.textContent = 'Cleaning\u2026';
  try {
    const res = await fetch('/api/clean', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({conversations: dataset, options})});
    const result = await res.json();
    dataset = result.dataset;
    hasCleaned = true; reviewed = false;
    renderCleanResult(result);
    showToast('Cleaned down to ' + dataset.length + ' conversation(s).', 'success');
    goToStep('clean');
  } catch (e) {
    showToast('Cleaning failed: ' + e, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Run cleaning';
  }
});
el('skipCleanBtn').addEventListener('click', () => {
  if (!hasExtracted) { showToast('Run extraction first.', 'error'); return; }
  goToStep('merge');
});

function renderCleanResult(result) {
  const s = result.stats || {};
  let html = '<div class="stat-grid">' +
    statCard(s.input_conversations, 'Input', '') +
    statCard(s.removed_exact_duplicates, 'Exact dupes removed', '') +
    statCard(s.removed_near_duplicates, 'Near-dupes removed', '') +
    statCard(s.dropped_too_short_turns, 'Dropped (too few turns)', '') +
    statCard(s.dropped_too_few_words, 'Dropped (too few words)', '') +
    statCard(s.dropped_refusal_only, 'Dropped (refusal-only)', '') +
    statCard(s.output_conversations, 'Output', 'good') +
    '</div>';
  html += '<div class="continue-row"><button class="primary" onclick="goToStep(\'merge\')">Continue to Merge &rarr;</button></div>';
  el('cleanResult').innerHTML = html;
}

// ---------- STEP 3: merge ----------
let mergeGroups = [];
let mergePool = [];

el('loadMergeFilesBtn').addEventListener('click', async () => {
  const paths = el('mergePaths').value.split('\n').map(s => s.trim()).filter(Boolean);
  const btn = el('loadMergeFilesBtn'); btn.disabled = true; btn.textContent = 'Loading…';
  el('mergeAnalysisCard').style.display = 'none';
  try {
    // Start with the current working dataset (if any), tagged as "(current)"
    // so it's included in the cross-file comparison.
    mergePool = dataset.map(c => ({...c, source_file: c.source_file || '(current working set)'}));
    let loadResultHtml = '';

    if (paths.length) {
      const res = await fetch('/api/load-files', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({paths})});
      const result = await res.json();
      mergePool = mergePool.concat(result.conversations || []);
      if (result.errors && result.errors.length) {
        loadResultHtml += '<div class="banner error"><b>' + result.errors.length + ' problem(s):</b><ul>' +
          result.errors.slice(0,10).map(e => '<li>' + escapeHtml(e) + '</li>').join('') + '</ul></div>';
      }
      if (result.file_stats && result.file_stats.length) {
        loadResultHtml += '<div class="stat-grid">' +
          result.file_stats.map(f => statCard(f.conversations, f.name, '')).join('') +
          '</div>';
      }
    }

    if (!mergePool.length) {
      showToast('Nothing to analyze — run Extract first or add file paths above.', 'error');
      el('mergeLoadResult').innerHTML = loadResultHtml;
      return;
    }

    const analyzeRes = await fetch('/api/analyze-overlap', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({conversations: mergePool, near_dup_threshold: 0.9})});
    const analysis = await analyzeRes.json();
    mergeGroups = analysis.groups || [];
    loadResultHtml += '<div class="banner ' + (mergeGroups.length ? 'warn' : 'success') + '">' +
      mergePool.length + ' total conversation(s) pooled — ' + analysis.duplicate_groups + ' duplicate group(s) found, ' +
      analysis.conversations_flagged + ' conversation(s) flagged as redundant.</div>';
    el('mergeLoadResult').innerHTML = loadResultHtml;

    renderMergeGroups();
    el('mergeSummary').textContent = mergeGroups.length
      ? mergeGroups.length + ' duplicate group(s) across ' + mergePool.length + ' conversations'
      : 'No duplicate or near-duplicate conversations found — nothing to trim.';
    el('mergeAnalysisCard').style.display = 'block';
  } catch (e) {
    showToast('Merge analysis failed: ' + e, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Load & analyze overlap';
  }
});

function renderMergeGroups() {
  const box = el('mergeGroups');
  box.innerHTML = '';
  mergeGroups.forEach((g, gi) => {
    const div = document.createElement('div');
    div.className = 'dup-group';
    const head = document.createElement('div');
    head.className = 'dup-group-head';
    head.textContent = 'Group ' + (gi + 1) + ' — ' + g.items.length + ' copies';
    div.appendChild(head);
    g.items.forEach(item => {
      const row = document.createElement('label');
      row.className = 'dup-item' + (item.index === g.recommended_keep ? ' recommended' : '');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = item.index === g.recommended_keep;
      cb.dataset.groupIdx = gi;
      cb.dataset.poolIdx = item.index;
      row.appendChild(cb);
      const tag = document.createElement('span');
      tag.className = 'file-tag';
      tag.textContent = item.source_file;
      row.appendChild(tag);
      const info = document.createElement('span');
      info.className = 'info';
      info.innerHTML = '<div class="t">' + escapeHtml(item.title) + '</div><div class="m">' + item.turns + ' turn(s)' + (item.index === g.recommended_keep ? ' · recommended keep' : '') + '</div>';
      row.appendChild(info);
      div.appendChild(row);
    });
    box.appendChild(div);
  });
}

el('applyMergeBtn').addEventListener('click', () => {
  const keepChecked = new Set();
  document.querySelectorAll('#mergeGroups input[type=checkbox]:checked').forEach(cb => keepChecked.add(parseInt(cb.dataset.poolIdx, 10)));
  const inAnyGroup = new Set();
  mergeGroups.forEach(g => g.items.forEach(item => inAnyGroup.add(item.index)));
  dataset = mergePool.filter((c, i) => !inAnyGroup.has(i) || keepChecked.has(i));
  hasMerged = true; reviewed = false; currentIndex = -1;
  showToast('Merged into ' + dataset.length + ' conversation(s).', 'success');
  goToStep('review');
});

el('skipMergeBtn').addEventListener('click', () => {
  if (!dataset.length) { showToast('Nothing to work with — run Extract first, or load files above.', 'error'); return; }
  goToStep('review');
});

// ---------- STEP 4: review (list/editor) ----------
function matchesFilter(conv) {
  if (!filterText) return true;
  const hay = (conv.title || '') + ' ' + (conv.id || '') + ' ' + (conv.conversations||[]).map(t => t.value).join(' ');
  return hay.toLowerCase().includes(filterText.toLowerCase());
}
function errorIndexSet() {
  const s = new Set();
  if (lastValidation) {
    for (const e of lastValidation.errors) {
      const m = e.match(/^conversation #(\d+)/);
      if (m) s.add(parseInt(m[1], 10));
    }
  }
  return s;
}
function renderList() {
  const list = el('convList'); list.innerHTML = '';
  const errIdx = errorIndexSet();
  dataset.forEach((conv, i) => {
    if (!matchesFilter(conv)) return;
    const div = document.createElement('div');
    div.className = 'conv-item' + (i === currentIndex ? ' active' : '') + (errIdx.has(i) ? ' has-error' : '');
    const turnCount = (conv.conversations || []).length;
    div.innerHTML = '<div class="t">' + escapeHtml(conv.title || '(untitled)') + '</div>' +
      '<div class="s">' + turnCount + ' turn(s)' + (conv.source ? ' &middot; ' + escapeHtml(conv.source) : '') + '</div>';
    div.onclick = () => { currentIndex = i; renderList(); renderMain(); };
    list.appendChild(div);
  });
}
function renderMain() {
  const main = el('reviewMain');
  if (currentIndex < 0 || !dataset[currentIndex]) {
    main.innerHTML = '<div class="empty-state">Select a conversation on the left.</div>';
    return;
  }
  const conv = dataset[currentIndex];
  main.innerHTML = '';
  const head = document.createElement('div');
  head.className = 'conv-header';
  const titleInput = document.createElement('input');
  titleInput.type = 'text'; titleInput.value = conv.title || '';
  titleInput.oninput = () => { conv.title = titleInput.value; renderList(); };
  const delConvBtn = document.createElement('button');
  delConvBtn.className = 'danger'; delConvBtn.textContent = 'Delete conversation';
  delConvBtn.onclick = () => {
    if (!confirm('Delete this entire conversation?')) return;
    dataset.splice(currentIndex, 1); currentIndex = -1; renderList(); renderMain();
  };
  head.appendChild(titleInput); head.appendChild(delConvBtn);
  main.appendChild(head);
  (conv.conversations || []).forEach((turn, j) => main.appendChild(renderTurn(conv, j)));
  const addRow = document.createElement('div'); addRow.className = 'add-turn-row';
  const addBtn = document.createElement('button');
  const roles = (conv.conversations || []).map(t => t.from);
  const nextRole = roles.length && roles[roles.length - 1] === 'human' ? 'gpt' : 'human';
  addBtn.textContent = '+ Add ' + nextRole + ' turn';
  addBtn.onclick = () => { conv.conversations.push({from: nextRole, value: ''}); renderMain(); };
  addRow.appendChild(addBtn); main.appendChild(addRow);
}
function renderTurn(conv, j) {
  const turn = conv.conversations[j];
  const wrap = document.createElement('div'); wrap.className = 'turn';
  const headRow = document.createElement('div'); headRow.className = 'turn-head';
  const pill = document.createElement('span'); pill.className = 'role-pill role-' + turn.from; pill.textContent = turn.from;
  headRow.appendChild(pill);
  const select = document.createElement('select');
  ['system','human','gpt'].forEach(r => {
    const opt = document.createElement('option'); opt.value = r; opt.textContent = r;
    if (r === turn.from) opt.selected = true;
    select.appendChild(opt);
  });
  select.onchange = () => { turn.from = select.value; renderMain(); };
  headRow.appendChild(select);
  const spacer = document.createElement('div'); spacer.className = 'spacer'; headRow.appendChild(spacer);
  const delBtn = document.createElement('button'); delBtn.textContent = 'Delete turn';
  delBtn.onclick = () => { conv.conversations.splice(j, 1); renderMain(); };
  headRow.appendChild(delBtn);
  wrap.appendChild(headRow);
  const textarea = document.createElement('textarea');
  textarea.value = turn.value || '';
  textarea.rows = Math.min(20, Math.max(2, Math.ceil((turn.value || '').length / 80)));
  textarea.oninput = () => { turn.value = textarea.value; reviewed = true; };
  wrap.appendChild(textarea);
  return wrap;
}
el('searchBox').addEventListener('input', e => { filterText = e.target.value; renderList(); });
el('addConvBtn').addEventListener('click', () => {
  dataset.push({id: 'new_' + Date.now(), source: 'manual', title: 'New conversation',
    conversations: [{from:'human', value:''}, {from:'gpt', value:''}]});
  currentIndex = dataset.length - 1; reviewed = true;
  renderList(); renderMain();
});
el('toExportBtn').addEventListener('click', () => goToStep('export'));

async function runValidate() {
  const res = await fetch('/api/validate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({conversations: dataset})});
  const result = await res.json();
  lastValidation = result;
  renderValidationBadge();
  return result;
}
function renderValidationBadge() {
  const badge = el('reviewBadge');
  if (!lastValidation) { badge.style.display = 'none'; return; }
  const { errors, warnings } = lastValidation;
  badge.style.display = 'inline-block';
  if (errors.length) { badge.className = 'banner error'; badge.textContent = errors.length + ' error(s)'; }
  else if (warnings.length) { badge.className = 'banner warn'; badge.textContent = warnings.length + ' warning(s)'; }
  else { badge.className = 'banner success'; badge.textContent = 'Valid'; }
  const card = el('issuesCard'); const body = el('issuesBody'); body.innerHTML = '';
  if (errors.length || warnings.length) {
    card.style.display = 'block';
    errors.forEach(e => { const d = document.createElement('div'); d.className = 'item error'; d.textContent = e; body.appendChild(d); });
    warnings.forEach(w => { const d = document.createElement('div'); d.className = 'item warning'; d.textContent = w; body.appendChild(d); });
  } else {
    card.style.display = 'none';
  }
  renderList();
}
el('checkValidityBtn').addEventListener('click', runValidate);

// ---------- export formats ----------
let exportFormats = {};
async function loadFormats() {
  try {
    const res = await fetch('/api/formats');
    exportFormats = await res.json();
  } catch (e) {
    exportFormats = {sharegpt: {label: 'ShareGPT (from/value)', description: '', available: true}};
  }
  const select = el('exportFormat');
  select.innerHTML = '';
  Object.keys(exportFormats).forEach(id => {
    const fmt = exportFormats[id];
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = fmt.label + (fmt.available ? '' : ' \u2014 coming soon');
    opt.disabled = !fmt.available;
    select.appendChild(opt);
  });
  const firstAvailable = Object.keys(exportFormats).find(id => exportFormats[id].available);
  if (firstAvailable) select.value = firstAvailable;
  updateFormatDescription();
}
function updateFormatDescription() {
  const fmt = exportFormats[el('exportFormat').value];
  el('formatDescription').textContent = fmt ? fmt.description : '';
}
el('exportFormat').addEventListener('change', updateFormatDescription);

// ---------- STEP 4: export ----------
el('saveBtn').addEventListener('click', async () => {
  const outputPath = el('outputPath').value.trim();
  if (!outputPath) { showToast('Enter an output file path.', 'error'); return; }
  const split = el('optSplit').value ? parseFloat(el('optSplit').value) : null;
  const jsonl = el('optJsonl').checked;
  const format = el('exportFormat').value;
  const btn = el('saveBtn'); btn.disabled = true; btn.textContent = 'Validating\u2026';
  try {
    const res = await fetch('/api/save', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({conversations: dataset, output_path: outputPath, split, jsonl, format})});
    const result = await res.json();
    lastValidation = {errors: result.errors || [], warnings: result.warnings || []};
    let html = '';
    if (result.saved) {
      html = '<div class="banner success">Saved (' + escapeHtml(exportFormats[format] ? exportFormats[format].label : format) + ') to: ' + result.paths.map(escapeHtml).join(', ') + '</div>';
      if (result.warnings && result.warnings.length) {
        html += '<div class="banner warn"><b>' + result.warnings.length + ' warning(s):</b><ul>' + result.warnings.slice(0,8).map(w=>'<li>'+escapeHtml(w)+'</li>').join('') + '</ul></div>';
      }
      showToast('Saved successfully.', 'success');
    } else {
      html = '<div class="banner error"><b>Not saved &mdash; ' + result.errors.length + ' validation error(s):</b><ul>' +
        result.errors.slice(0,12).map(e=>'<li>'+escapeHtml(e)+'</li>').join('') + '</ul></div>';
      html += '<div style="margin-top:8px;"><button onclick="goToStep(\'review\')">Go fix in Review</button></div>';
      showToast('Save blocked by validation errors.', 'error');
    }
    el('saveResult').innerHTML = html;
    renderValidationBadge();
  } catch (e) {
    showToast('Save failed: ' + e, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Validate & Save';
  }
});

loadFormats();
goToStep('extract');
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------

MAX_SCAN_RESULTS = 200
MAX_SCAN_PARSE_BYTES = 150 * 1024 * 1024  # skip full-parse preview above 150MB, just report size


def _peek_json_file(path: Path) -> dict:
    entry = {"path": str(path), "name": path.name, "size": path.stat().st_size,
             "type": "json", "format": None, "conversation_count": None, "detail": None}
    if entry["size"] > MAX_SCAN_PARSE_BYTES:
        entry["detail"] = "file too large to preview — will still be parsed during extraction"
        return entry
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        fmt = chat_to_lora.detect_format(data)
        entry["format"] = fmt
        if fmt:
            entry["conversation_count"] = len(chat_to_lora.normalize_to_list(data))
    except Exception as e:
        entry["error"] = str(e)
    return entry


def _peek_zip_file(path: Path) -> dict:
    entry = {"path": str(path), "name": path.name, "size": path.stat().st_size,
             "type": "zip", "format": None, "conversation_count": None, "detail": None}
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".json")]
            names.sort(key=lambda n: (Path(n).name != "conversations.json", n))
            for n in names:
                try:
                    with zf.open(n) as f:
                        data = json.load(f)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                fmt = chat_to_lora.detect_format(data)
                if fmt:
                    entry["format"] = fmt
                    entry["conversation_count"] = len(chat_to_lora.normalize_to_list(data))
                    entry["detail"] = n
                    break
            else:
                if names:
                    entry["detail"] = f"{len(names)} json file(s) inside, none recognized"
                else:
                    entry["detail"] = "no .json files inside this zip"
    except Exception as e:
        entry["error"] = str(e)
    return entry


def scan_folder_for_exports(folder: Path) -> list:
    files = sorted(list(folder.rglob("*.json")) + list(folder.rglob("*.zip")))
    results = []
    for fp in files[:MAX_SCAN_RESULTS]:
        if fp.suffix.lower() == ".json":
            results.append(_peek_json_file(fp))
        else:
            results.append(_peek_zip_file(fp))
    # recognized files first, then unrecognized, then errors
    results.sort(key=lambda e: (e.get("format") not in ("chatgpt", "claude"), e["name"].lower()))
    return results


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

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
        elif self.path == "/api/formats":
            formats = {
                fid: {"label": f["label"], "description": f["description"], "available": f["available"]}
                for fid, f in export_formats.available_formats().items()
            }
            self._send_json(formats)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        try:
            payload = self._read_json_body()
        except Exception as e:
            self._send_json({"error": f"invalid JSON body: {e}"}, status=400)
            return

        try:
            if self.path == "/api/extract":
                self._handle_extract(payload)
            elif self.path == "/api/clean":
                self._handle_clean(payload)
            elif self.path == "/api/validate":
                self._handle_validate(payload)
            elif self.path == "/api/save":
                self._handle_save(payload)
            elif self.path == "/api/scan-folder":
                self._handle_scan_folder(payload)
            elif self.path == "/api/browse-folder":
                self._handle_browse_folder(payload)
            elif self.path == "/api/load-files":
                self._handle_load_files(payload)
            elif self.path == "/api/analyze-overlap":
                self._handle_analyze_overlap(payload)
            else:
                self._send_json({"error": "not found"}, status=404)
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _handle_extract(self, payload):
        inputs = payload.get("inputs", [])
        options = payload.get("options", {})
        missing = [p for p in inputs if not Path(p).exists()]
        present = [p for p in inputs if p not in missing]

        dataset, stats = chat_to_lora.convert(
            present,
            min_turns=int(options.get("min_turns", 2) or 0),
            include_system=bool(options.get("include_system", False)),
            include_tool=bool(options.get("include_tool", False)),
            max_chars=(int(options["max_chars"]) if options.get("max_chars") else None),
        )
        suggested_dir = str(Path(present[0]).resolve().parent) if present else str(Path.cwd())
        self._send_json({"dataset": dataset, "stats": stats, "missing": missing, "suggested_dir": suggested_dir})

    def _handle_clean(self, payload):
        conversations = payload.get("conversations", [])
        options = payload.get("options", {})
        cleaned, stats = clean_dataset.clean(
            conversations,
            min_turns=int(options.get("min_turns", 2) or 0),
            min_words=int(options.get("min_words", 8) or 0),
            near_dup_threshold=float(options.get("near_dup_threshold", 0.9) or 0),
            dedup_exact_enabled=bool(options.get("dedup_exact", True)),
            strip_boilerplate_enabled=bool(options.get("strip_boilerplate", True)),
            drop_refusals=bool(options.get("drop_refusals", False)),
            max_total_chars=(int(options["max_total_chars"]) if options.get("max_total_chars") else None),
        )
        self._send_json({"dataset": cleaned, "stats": stats})

    def _handle_validate(self, payload):
        conversations = payload.get("conversations", [])
        errors, warnings, stats = review_dataset.validate_dataset(conversations)
        self._send_json({"valid": not errors, "errors": errors, "warnings": warnings, "stats": stats})

    def _handle_save(self, payload):
        conversations = payload.get("conversations", [])
        output_path = payload.get("output_path") or "lora_dataset.json"
        jsonl = bool(payload.get("jsonl", False))
        split = payload.get("split")
        split = float(split) if split else None
        format_id = payload.get("format") or "sharegpt"

        fmt = export_formats.available_formats().get(format_id)
        if fmt is None or not fmt.get("available", False):
            self._send_json({"saved": False, "errors": [f"Export format '{format_id}' is not available."], "warnings": []})
            return

        # Validation always runs against the canonical ShareGPT shape, since
        # that's what every step of the pipeline (clean, review) works in —
        # regardless of which format we're about to convert to on save.
        errors, warnings, stats = review_dataset.validate_dataset(conversations)
        if errors:
            self._send_json({"saved": False, "errors": errors, "warnings": warnings, "stats": stats})
            return

        out = Path(output_path)
        try:
            converted = export_formats.convert_dataset(conversations, format_id)
            out.parent.mkdir(parents=True, exist_ok=True)
            chat_to_lora.write_output(converted, out, jsonl, split)
            if split:
                paths = [str(out.with_name(out.stem + suffix + out.suffix)) for suffix in (".train", ".val")]
            else:
                paths = [str(out)]
        except Exception as e:
            self._send_json({"saved": False, "errors": [f"write failed: {e}"], "warnings": warnings})
            return

        self._send_json({"saved": True, "paths": paths, "errors": [], "warnings": warnings, "stats": stats})

    def _handle_scan_folder(self, payload):
        folder = (payload.get("path") or "").strip()
        if not folder:
            self._send_json({"error": "no path provided"}, status=400)
            return
        p = Path(folder)
        if not p.exists():
            self._send_json({"exists": False, "is_dir": False, "candidates": []})
            return
        if not p.is_dir():
            self._send_json({"exists": True, "is_dir": False, "candidates": []})
            return
        candidates = scan_folder_for_exports(p)
        self._send_json({"exists": True, "is_dir": True, "candidates": candidates})

    def _handle_browse_folder(self, payload):
        # This app only ever binds to 127.0.0.1, so "the browser" and "the
        # server" are the same machine — popping a native OS dialog here
        # opens it on the user's own desktop, not some remote host.
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            self._send_json({"path": None, "error": "This Python install doesn't include tkinter, so a native folder dialog isn't available — paste the folder path into the box instead."})
            return
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(title="Select the folder containing your export")
            root.destroy()
        except Exception as e:
            self._send_json({"path": None, "error": f"Couldn't open a folder dialog: {e}"})
            return
        self._send_json({"path": selected or None})

    def _handle_load_files(self, payload):
        paths = payload.get("paths", [])
        combined = []
        file_stats = []
        errors = []
        for raw_path in paths:
            fp = Path(raw_path)
            if not fp.exists():
                errors.append(f"{raw_path}: file not found")
                continue
            try:
                with fp.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                errors.append(f"{raw_path}: {e}")
                continue

            if isinstance(data, dict) and isinstance(data.get("conversations"), list):
                convs = data["conversations"]
            elif isinstance(data, list):
                convs = data
            else:
                errors.append(f"{raw_path}: not a recognized ShareGPT JSON (expected a list of conversation objects)")
                continue

            count = 0
            for c in convs:
                if isinstance(c, dict) and isinstance(c.get("conversations"), list):
                    tagged = dict(c)
                    tagged["source_file"] = fp.name
                    combined.append(tagged)
                    count += 1
            file_stats.append({"path": str(fp), "name": fp.name, "conversations": count})

        self._send_json({"conversations": combined, "file_stats": file_stats, "errors": errors})

    def _handle_analyze_overlap(self, payload):
        conversations = payload.get("conversations", [])
        threshold = float(payload.get("near_dup_threshold", 0.9) or 0)
        groups = clean_dataset.find_duplicate_groups(conversations, near_dup_threshold=threshold)

        def score(i):
            c = conversations[i]
            turns = len(c.get("conversations", []))
            chars = sum(len(t.get("value", "")) for t in c.get("conversations", []))
            return (turns, chars)

        report = []
        for g in groups:
            idxs = g["indices"]
            best = max(idxs, key=score)
            report.append({
                "indices": idxs,
                "recommended_keep": best,
                "items": [
                    {
                        "index": i,
                        "title": conversations[i].get("title") or "(untitled)",
                        "source_file": conversations[i].get("source_file", "?"),
                        "turns": len(conversations[i].get("conversations", [])),
                    }
                    for i in idxs
                ],
            })

        self._send_json({
            "groups": report,
            "total_conversations": len(conversations),
            "duplicate_groups": len(report),
            "conversations_flagged": sum(len(g["indices"]) - 1 for g in report),
        })


def main():
    ap = argparse.ArgumentParser(description="Refinery: extract, clean, review, and export a LoRA dataset in one web UI.")
    ap.add_argument("--port", type=int, default=8765, help="port to serve on (default: 8765)")
    ap.add_argument("--no-browser", action="store_true", help="don't automatically open a browser tab")
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Refinery running at {url}")
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
