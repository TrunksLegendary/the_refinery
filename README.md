# Refinery

Takes a ChatGPT/Claude.ai export (crude) and turns it into a clean,
hand-reviewed training dataset (refined) for LoRA fine-tuning — ShareGPT
format today, with other formats pluggable in (Axolotl, LLaMA-Factory,
FastChat, etc. all read ShareGPT natively).

See **[MANUAL.md](MANUAL.md)** for the full walkthrough (every step, every
option, troubleshooting, and the FAQ). This README is the quick-reference
version.

## webapp.py — the easy way

One local web app that walks through the whole pipeline — Extract → Clean →
Review & edit → Export — with a live preview at every step. No installs
required (pure Python standard library, no CDN dependencies).

```bash
python webapp.py
```

Opens `http://127.0.0.1:8765/` in your browser:

1. **Extract** — paste the path to your export (`conversations.json`, the export `.zip`, or a folder) and pick extraction options. Or click **Browse…** to open a native folder picker (requires tkinter, which ships with the standard python.org Windows installer — if it's missing you'll get a clear message and can paste the path instead), or paste a folder path directly into "Scan folder". Either way, it recursively finds every `.json`/`.zip` in that folder, detects which ones are recognizable ChatGPT/Claude exports (and how many conversations are in each), and lets you check off which ones to add to the input list before running extraction.
2. **Clean** — dedupe, strip boilerplate, and filter low-quality conversations (or skip this step).
3. **Merge** — pull in other already-cleaned ShareGPT JSON files (from a previous run, a different export, wherever) and check for duplicate/overlapping conversations across all of them combined with your current working set. Exact and near-duplicates are grouped together (even across files) with the longest/most-complete copy pre-selected to keep; adjust the checkboxes and apply to fold everything into one working dataset. Optional — skip it to just continue with what you've got.
4. **Review** — browse every conversation, edit any turn's text or role, delete/add turns or whole conversations.
5. **Export** — pick an output format, then validate & save. Validation always checks the canonical ShareGPT schema before anything is written; the dataset is converted to the chosen format only after it passes. Optional train/val split and JSON Lines output.

Options: `--port N` (default 8765), `--no-browser`.

### Export formats

`export_formats.py` is a small registry so new output formats can be added
without touching the save pipeline. Right now:

| Format | Status |
|---|---|
| ShareGPT (`from`/`value`) | Available — this is also our internal canonical format |
| Alpaca (`instruction`/`input`/`output`) | Stubbed, shown greyed-out in the UI as "coming soon" |
| OpenAI messages / ChatML (`role`/`content`) | Stubbed, shown greyed-out in the UI as "coming soon" |

To finish one of the stubs: the converter functions (`to_alpaca`,
`to_openai_messages`) already exist in `export_formats.py` — just flip
`"available": True` for that entry in `FORMATS` once you're happy with the
conversion logic, and it appears in the Export dropdown automatically (the
dropdown is populated from `GET /api/formats`, not hardcoded in the UI).

## The individual scripts

The same logic is also available as three standalone command-line tools —
useful for scripting the pipeline (e.g. in a batch job) instead of using the
browser UI. `webapp.py` imports and runs these directly, so behavior is
identical either way.

1. **`chat_to_lora.py`** — parses the raw export into ShareGPT JSON.
2. **`clean_dataset.py`** — dedupes and quality-filters that JSON.
3. **`review_dataset.py`** — opens a lighter single-purpose web editor for just the review/edit/validate step, if you don't need the full wizard.

```bash
python chat_to_lora.py export.zip -o raw_dataset.json
python clean_dataset.py raw_dataset.json -o clean_dataset.json --split 0.9
python review_dataset.py clean_dataset.train.json
```

## chat_to_lora.py

Converts ChatGPT and Claude.ai conversation exports into a ShareGPT-format
JSON dataset.

## Getting your export

- **ChatGPT**: Settings > Data Controls > Export Data. OpenAI emails you a ZIP
  containing `conversations.json`.
- **Claude.ai**: Settings > Account > Export data. You get a ZIP containing
  `conversations.json`.

You can point the script directly at the ZIP — no need to unzip first.

## Usage

```bash
python chat_to_lora.py INPUT [INPUT ...] -o training_data.json
```

`INPUT` can be a `conversations.json` file, an export `.zip`, or a directory
(scanned recursively). You can mix ChatGPT and Claude files/zips in one run —
format is auto-detected per file.

### Common options

| Flag | What it does |
|---|---|
| `-o, --output PATH` | Output file (default `lora_dataset.json`) |
| `--min-turns N` | Drop conversations with fewer than N human/gpt turns (default 2) |
| `--include-system` | Keep the system prompt as a `"system"` turn |
| `--include-tool` | Keep tool/function-call output as a `"gpt"` turn |
| `--max-chars N` | Truncate any single message to N characters |
| `--jsonl` | Write JSON Lines instead of one JSON array |
| `--split 0.9` | Also write a `.train.json` / `.val.json` split |
| `--stats` | Print summary counts only, don't write a file |

### Example

```bash
python chat_to_lora.py chatgpt-export.zip claude-export.zip \
  -o dataset.json --include-system --min-turns 2 --split 0.9
```

## Output format

Each conversation becomes one ShareGPT-style record:

```json
{
  "id": "chatgpt_67a678d3-...",
  "source": "chatgpt",
  "title": "Conversation title",
  "conversations": [
    {"from": "system", "value": "..."},
    {"from": "human", "value": "..."},
    {"from": "gpt", "value": "..."}
  ]
}
```

This is the format expected by Axolotl's `sharegpt` dataset type and
LLaMA-Factory's `sharegpt` formatting — check your training framework's docs
for any field renaming it needs (e.g. some tools expect `"from"/"value"`
renamed to `"role"/"content"`, which is a one-line `jq`/Python transform away).

### Notes

- Branching ChatGPT conversations (edited/regenerated messages) follow the
  currently active branch (`current_node`), not every dead-end branch.
- Empty conversations and messages with no text (pure image/tool-only turns
  unless `--include-tool` is set) are dropped automatically.
- Citation markers ChatGPT sometimes injects into text are stripped.

## clean_dataset.py

Second pass: dedupes and quality-filters a ShareGPT JSON file (the output of
`chat_to_lora.py`, or any file in that format).

```bash
python clean_dataset.py INPUT [INPUT ...] -o clean_dataset.json
```

What it does, in order:

1. **Whitespace/boilerplate cleanup** — collapses excess blank lines/spaces,
   and strips canned openers like "As an AI language model," or "Certainly!"
   from the start of assistant turns.
2. **Exact-duplicate removal** — identical conversations (by normalized text) are dropped.
3. **Near-duplicate removal** — conversations whose word-overlap similarity
   is above a threshold are collapsed to one (the longer one is kept).
4. **Quality filters** — drops conversations that are too short (turn count
   or word count), too long, or (optionally) end in a canned refusal.

### Options

| Flag | What it does |
|---|---|
| `-o, --output PATH` | Output file (default `clean_dataset.json`) |
| `--min-turns N` | Drop conversations with fewer than N human/gpt turns (default 2) |
| `--min-words N` | Drop conversations with fewer than N total words (default 8) |
| `--near-dup-threshold F` | Word-overlap similarity (0-1) above which conversations are treated as duplicates (default 0.9; `0` disables) |
| `--no-dedup-exact` | Disable exact-duplicate removal |
| `--no-strip-boilerplate` | Disable stripping of canned AI-disclaimer phrases |
| `--drop-refusals` | Drop conversations containing a canned refusal-only reply |
| `--max-total-chars N` | Drop conversations whose combined text exceeds N characters |
| `--jsonl` | Write JSON Lines instead of one JSON array |
| `--split 0.9` | Also write a `.train.json` / `.val.json` split |
| `--stats` | Print summary counts only, don't write a file |

### Example

```bash
python clean_dataset.py raw_dataset.json -o clean_dataset.json \
  --min-words 10 --near-dup-threshold 0.85 --drop-refusals --split 0.9
```

## review_dataset.py

Opens a local web page (no install needed — pure Python standard library) to
browse every conversation, hand-edit any turn's text or role, delete bad
turns/conversations, or add new ones.

```bash
python review_dataset.py clean_dataset.json
```

This starts a server at `http://127.0.0.1:8765/` and opens it in your default
browser. Edit anything, then click **Save**. Before anything touches disk,
the dataset is validated against the LoRA/ShareGPT schema:

- every conversation has a non-empty `conversations` list
- `from` is one of `system` / `human` / `gpt`
- every `value` is non-empty text
- an optional `system` turn must come first
- turns must strictly alternate `human`, `gpt`, `human`, `gpt`, … (no repeats)
- the conversation starts with `human` (after the optional system turn)

If validation finds errors, **nothing is saved** — the UI shows exactly which
conversation and turn failed and why, so you can fix it and try again.
Warnings (e.g. a very short conversation, or one that ends on a human turn)
don't block saving, but are shown too.

### Options

| Flag | What it does |
|---|---|
| `-o, --output PATH` | Where to save edits (default: `INPUT.reviewed.json`) |
| `--in-place` | Save back to the input file instead (keeps a `.bak` backup of the previous version) |
| `--port N` | Port to serve on (default: 8765) |
| `--no-browser` | Don't automatically open a browser tab |

### Example

```bash
python review_dataset.py clean_dataset.train.json --in-place
```
