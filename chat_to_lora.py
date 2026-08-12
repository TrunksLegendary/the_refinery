#!/usr/bin/env python3
"""
chat_to_lora.py

Convert ChatGPT or Claude.ai conversation export files (conversations.json,
or the export .zip you get emailed) into a ShareGPT-style JSON file ready
for LoRA fine-tuning (Axolotl, LLaMA-Factory, FastChat, etc.).

Output format (ShareGPT):
[
  {
    "id": "chatgpt_<conversation-id>",
    "source": "chatgpt",
    "title": "Conversation title",
    "conversations": [
      {"from": "system", "value": "..."},   # only if present
      {"from": "human", "value": "..."},
      {"from": "gpt", "value": "..."},
      ...
    ]
  },
  ...
]

Usage:
    python chat_to_lora.py INPUT [INPUT ...] -o training_data.json

    INPUT can be:
      - a conversations.json file (ChatGPT or Claude format, auto-detected)
      - a ChatGPT/Claude export .zip (the file you get emailed / download)
      - a directory (scanned recursively for *.json / *.zip)

Options:
    -o, --output PATH        Output JSON file (default: lora_dataset.json)
    --min-turns N             Drop conversations with fewer than N human/gpt turns (default: 2)
    --include-system          Include system prompts as a "system" turn (default: off)
    --include-tool             Include tool/function-call messages as "gpt" turns (default: off)
    --max-chars N              Truncate any single message to N characters (default: no limit)
    --jsonl                    Write JSON Lines instead of a single JSON array
    --split RATIO               Also write a train/val split, e.g. --split 0.9
    --stats                    Print summary statistics only, do not write output
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Helpers: reading input (files, dirs, zips)
# --------------------------------------------------------------------------

def find_json_blobs(inputs: list[str]) -> Iterable[tuple[str, Any]]:
    """Yield (source_label, parsed_json) for every conversations.json-like
    payload found across the given input paths (files, dirs, or zips)."""
    for raw in inputs:
        p = Path(raw)
        if not p.exists():
            print(f"  ! skipping missing path: {raw}", file=sys.stderr)
            continue

        if p.is_dir():
            candidates = list(p.rglob("*.json")) + list(p.rglob("*.zip"))
            for c in candidates:
                yield from find_json_blobs([str(c)])
            continue

        if p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".json")]
                # Prefer the canonical export filename if present
                names.sort(key=lambda n: (Path(n).name != "conversations.json", n))
                for n in names:
                    try:
                        with zf.open(n) as f:
                            data = json.load(f)
                        yield f"{p.name}:{n}", data
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
            continue

        if p.suffix.lower() == ".json":
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                yield str(p), data
            except json.JSONDecodeError as e:
                print(f"  ! could not parse {p}: {e}", file=sys.stderr)
            continue


def detect_format(data: Any) -> str | None:
    """Return 'chatgpt', 'claude', or None."""
    # ChatGPT: list of conversations, each has a "mapping" tree
    if isinstance(data, list) and data:
        sample = data[0]
        if isinstance(sample, dict):
            if "mapping" in sample:
                return "chatgpt"
            if "chat_messages" in sample:
                return "claude"
    # Some exports wrap the array
    if isinstance(data, dict):
        if "conversations" in data and isinstance(data["conversations"], list):
            inner = data["conversations"]
            if inner and isinstance(inner[0], dict) and "mapping" in inner[0]:
                return "chatgpt"
        if "chat_messages" in data:
            return "claude"
    return None


def normalize_to_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "conversations" in data:
        return data["conversations"]
    return [data]


# --------------------------------------------------------------------------
# Content cleanup
# --------------------------------------------------------------------------

CITATION_RE = re.compile(r".*?|【cite】【[^】]*】")


def clean_text(text: str, max_chars: int | None) -> str:
    if not text:
        return ""
    text = CITATION_RE.sub("", text)
    text = text.strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …[truncated]"
    return text


# --------------------------------------------------------------------------
# ChatGPT parsing
# --------------------------------------------------------------------------

def _chatgpt_extract_text(message: dict) -> str:
    content = message.get("content") or {}
    ctype = content.get("content_type")
    parts = content.get("parts") or []
    out = []
    for part in parts:
        if isinstance(part, str):
            if part:
                out.append(part)
        elif isinstance(part, dict):
            if part.get("content_type") == "image_asset_pointer":
                out.append("[image attached]")
            # ignore other structured parts (widgets, browsing display, etc.)
    if not out and ctype == "code":
        # code content sometimes stored directly as string list already handled above
        pass
    return "\n".join(out).strip()


def parse_chatgpt_conversation(conv: dict, include_system: bool, include_tool: bool,
                                max_chars: int | None) -> dict | None:
    mapping = conv.get("mapping") or {}
    if not mapping:
        return None

    # Walk the tree from current_node back to root to get the active thread,
    # falling back to a full traversal if current_node is missing/broken.
    current_node = conv.get("current_node")
    ordered_nodes = []

    if current_node and current_node in mapping:
        node_id = current_node
        chain = []
        while node_id:
            node = mapping.get(node_id)
            if not node:
                break
            chain.append(node)
            node_id = node.get("parent")
        ordered_nodes = list(reversed(chain))
    else:
        # fallback: sort all nodes with messages by create_time
        nodes = [n for n in mapping.values() if n.get("message")]
        nodes.sort(key=lambda n: (n["message"].get("create_time") or 0))
        ordered_nodes = nodes

    turns = []
    for node in ordered_nodes:
        msg = node.get("message")
        if not msg:
            continue
        author = msg.get("author") or {}
        role = author.get("role")

        # System prompts are almost always marked hidden/weight-0 in the
        # export even though they carry real content, so handle them before
        # the general hidden-message filter below.
        if role == "system":
            if not include_system:
                continue
            text = clean_text(_chatgpt_extract_text(msg), max_chars)
            if text:
                turns.append({"from": "system", "value": text})
            continue

        if msg.get("weight", 1.0) == 0.0:
            continue
        if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue

        if role == "tool":
            if not include_tool:
                continue
            text = clean_text(_chatgpt_extract_text(msg), max_chars)
            if text:
                turns.append({"from": "gpt", "value": text})
            continue

        if role not in ("user", "assistant"):
            continue

        text = clean_text(_chatgpt_extract_text(msg), max_chars)
        if not text:
            continue
        turns.append({"from": "human" if role == "user" else "gpt", "value": text})

    turns = _merge_consecutive_same_role(turns)
    if not turns:
        return None

    return {
        "id": f"chatgpt_{conv.get('id') or conv.get('conversation_id') or 'unknown'}",
        "source": "chatgpt",
        "title": conv.get("title") or "Untitled",
        "conversations": turns,
    }


# --------------------------------------------------------------------------
# Claude parsing
# --------------------------------------------------------------------------

def _claude_extract_text(message: dict) -> str:
    # Newer exports: message["text"] already has the flattened text.
    text = message.get("text")
    if text:
        return text
    # Fallback: reconstruct from content blocks.
    blocks = message.get("content") or []
    out = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            out.append(block["text"])
        elif btype == "tool_use":
            out.append(f"[tool call: {block.get('name', 'unknown')}]")
        elif btype == "tool_result":
            out.append("[tool result]")
        elif btype == "image":
            out.append("[image attached]")
    return "\n".join(out).strip()


def parse_claude_conversation(conv: dict, include_system: bool, include_tool: bool,
                               max_chars: int | None) -> dict | None:
    messages = conv.get("chat_messages") or []
    turns = []
    for msg in messages:
        sender = msg.get("sender")
        text = clean_text(_claude_extract_text(msg), max_chars)
        if not text:
            continue
        if sender == "human":
            turns.append({"from": "human", "value": text})
        elif sender == "assistant":
            turns.append({"from": "gpt", "value": text})
        # Claude exports don't typically carry separate system-role messages
        # in chat_messages; project custom instructions (if any) live elsewhere.

    turns = _merge_consecutive_same_role(turns)
    if not turns:
        return None

    return {
        "id": f"claude_{conv.get('uuid') or 'unknown'}",
        "source": "claude",
        "title": conv.get("name") or "Untitled",
        "conversations": turns,
    }


# --------------------------------------------------------------------------
# Shared post-processing
# --------------------------------------------------------------------------

def _merge_consecutive_same_role(turns: list[dict]) -> list[dict]:
    """Merge back-to-back turns from the same speaker (common when tool
    calls are interleaved) so the result strictly alternates."""
    merged: list[dict] = []
    for t in turns:
        if merged and merged[-1]["from"] == t["from"]:
            merged[-1]["value"] = (merged[-1]["value"] + "\n\n" + t["value"]).strip()
        else:
            merged.append(dict(t))
    return merged


def trim_to_alternating(turns: list[dict]) -> list[dict]:
    """ShareGPT-style trainers generally expect the turn list to start with
    human (after an optional leading system turn) and alternate cleanly.
    Drop a stray leading 'gpt' turn if present."""
    if not turns:
        return turns
    i = 0
    if turns[0]["from"] == "system":
        i = 1
    if i < len(turns) and turns[i]["from"] == "gpt":
        turns = turns[:i] + turns[i + 1:]
    return turns


# --------------------------------------------------------------------------
# Main conversion driver
# --------------------------------------------------------------------------

def convert(inputs: list[str], min_turns: int, include_system: bool,
            include_tool: bool, max_chars: int | None) -> tuple[list[dict], dict]:
    dataset: list[dict] = []
    stats = {
        "files_scanned": 0,
        "conversations_seen": 0,
        "conversations_kept": 0,
        "conversations_dropped_empty": 0,
        "conversations_dropped_short": 0,
        "by_source": {},
        "unrecognized_files": 0,
    }

    for label, blob in find_json_blobs(inputs):
        stats["files_scanned"] += 1
        fmt = detect_format(blob)
        if fmt is None:
            print(f"  ? unrecognized format, skipping: {label}", file=sys.stderr)
            stats["unrecognized_files"] += 1
            continue

        conv_list = normalize_to_list(blob)
        print(f"  - {label}: detected {fmt} export, {len(conv_list)} conversation(s)")

        for conv in conv_list:
            if not isinstance(conv, dict):
                continue
            stats["conversations_seen"] += 1

            if fmt == "chatgpt":
                result = parse_chatgpt_conversation(conv, include_system, include_tool, max_chars)
            else:
                result = parse_claude_conversation(conv, include_system, include_tool, max_chars)

            if result is None:
                stats["conversations_dropped_empty"] += 1
                continue

            result["conversations"] = trim_to_alternating(result["conversations"])
            n_turns = sum(1 for t in result["conversations"] if t["from"] in ("human", "gpt"))
            if n_turns < min_turns:
                stats["conversations_dropped_short"] += 1
                continue

            dataset.append(result)
            stats["conversations_kept"] += 1
            stats["by_source"][fmt] = stats["by_source"].get(fmt, 0) + 1

    return dataset, stats


def write_output(dataset: list[dict], output: Path, as_jsonl: bool, split: float | None):
    if split:
        cut = max(1, int(len(dataset) * split))
        train, val = dataset[:cut], dataset[cut:]
        _write_one(train, output.with_name(output.stem + ".train" + output.suffix), as_jsonl)
        _write_one(val, output.with_name(output.stem + ".val" + output.suffix), as_jsonl)
        print(f"Wrote {len(train)} train / {len(val)} val examples")
    else:
        _write_one(dataset, output, as_jsonl)
        print(f"Wrote {len(dataset)} examples to {output}")


def _write_one(dataset: list[dict], path: Path, as_jsonl: bool):
    with path.open("w", encoding="utf-8") as f:
        if as_jsonl:
            for row in dataset:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            json.dump(dataset, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Convert ChatGPT/Claude exports into a LoRA-ready ShareGPT JSON dataset.")
    ap.add_argument("inputs", nargs="+", help="conversations.json file(s), export .zip(s), or a directory")
    ap.add_argument("-o", "--output", default="lora_dataset.json", help="output path (default: lora_dataset.json)")
    ap.add_argument("--min-turns", type=int, default=2, help="minimum human+gpt turns to keep a conversation (default: 2)")
    ap.add_argument("--include-system", action="store_true", help="include system prompts as a system turn")
    ap.add_argument("--include-tool", action="store_true", help="include tool/function messages as gpt turns")
    ap.add_argument("--max-chars", type=int, default=None, help="truncate any single message to this many characters")
    ap.add_argument("--jsonl", action="store_true", help="write JSON Lines instead of a single JSON array")
    ap.add_argument("--split", type=float, default=None, help="train/val split ratio, e.g. 0.9")
    ap.add_argument("--stats", action="store_true", help="only print stats, don't write output")
    args = ap.parse_args()

    print("Scanning inputs...")
    dataset, stats = convert(
        args.inputs,
        min_turns=args.min_turns,
        include_system=args.include_system,
        include_tool=args.include_tool,
        max_chars=args.max_chars,
    )

    print("\nSummary:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    if args.stats:
        return

    if not dataset:
        print("\nNo conversations converted — nothing written.", file=sys.stderr)
        sys.exit(1)

    write_output(dataset, Path(args.output), args.jsonl, args.split)


if __name__ == "__main__":
    main()
