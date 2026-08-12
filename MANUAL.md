# Refinery — User Manual

Refinery turns a ChatGPT or Claude.ai conversation export into a clean,
hand-reviewed training dataset for LoRA fine-tuning. This manual covers the
full workflow: getting your export, running it through Refinery's five
steps, and what to do when things go wrong.

If you just want the short version, see the Quick Start below. Everything
after that is reference material you can jump to as needed.

---

## Contents

1. [Quick Start](#1-quick-start)
2. [What Refinery Does](#2-what-refinery-does)
3. [Requirements](#3-requirements)
4. [Getting Your Export](#4-getting-your-export)
5. [Launching Refinery](#5-launching-refinery)
6. [Step 1 — Extract](#6-step-1--extract)
7. [Step 2 — Clean](#7-step-2--clean)
8. [Step 3 — Merge](#8-step-3--merge)
9. [Step 4 — Review](#9-step-4--review)
10. [Step 5 — Export](#10-step-5--export)
11. [Moving Between Steps](#11-moving-between-steps)
12. [Understanding the Output Format](#12-understanding-the-output-format)
13. [Using the Command-Line Scripts Instead](#13-using-the-command-line-scripts-instead)
14. [Troubleshooting](#14-troubleshooting)
15. [Tips for a Good Training Dataset](#15-tips-for-a-good-training-dataset)
16. [FAQ](#16-faq)

---

## 1. Quick Start

1. Export your history from ChatGPT or Claude.ai (see [§4](#4-getting-your-export)).
2. Open a terminal in your Refinery folder and run:
   ```
   python webapp.py
   ```
3. Your browser opens to `http://127.0.0.1:8765/`.
4. **Extract**: paste the path to your export file (or its `.zip`) and click **Run extraction**.
5. **Clean**: leave the defaults and click **Run cleaning** (or skip it).
6. **Merge**: skip this unless you're combining multiple exports.
7. **Review**: skim the list, fix anything that looks wrong, click **Check validity**.
8. **Export**: set an output path and click **Validate & Save**.

You now have a ShareGPT-format JSON file ready to hand to a LoRA training
tool (Axolotl, LLaMA-Factory, Unsloth, FastChat, etc.).

---

## 2. What Refinery Does

Refinery is a local pipeline with five stages:

```
 Extract  →  Clean  →  Merge  →  Review  →  Export
(parse)     (dedupe)   (combine)  (edit)    (validate & save)
```

- **Extract** reads a raw ChatGPT or Claude.ai export and turns it into a
  standard list of conversations (ShareGPT format: alternating `human`/`gpt`
  turns).
- **Clean** removes duplicate conversations, strips canned AI disclaimers,
  and filters out low-content exchanges.
- **Merge** is for combining that dataset with *other* already-cleaned
  datasets you have lying around, catching duplicates that span files.
- **Review** is a hands-on editor — browse every conversation, fix wording,
  delete junk, add or remove turns.
- **Export** checks the whole dataset against the schema a LoRA trainer
  expects, and only then writes it to disk, in the format you choose.

Every step is optional except Extract (or loading files directly in Merge)
and Export. You can skip Clean, skip Merge, and go straight from Extract to
Review to Export if you don't need the extra passes.

Refinery is a browser UI wrapped around four standalone Python scripts
(`chat_to_lora.py`, `clean_dataset.py`, `review_dataset.py`,
`export_formats.py`). It calls them directly rather than reimplementing
their logic, so anything documented here for the web app also applies if
you run those scripts from the command line instead (see [§13](#13-using-the-command-line-scripts-instead)).

---

## 3. Requirements

- **Python 3.10 or later.** No third-party packages are required — Refinery
  uses only the Python standard library, both for the backend server and
  the frontend (no CDN, no build step, works fully offline).
- **tkinter** is needed only for the **Browse…** folder-picker button in
  Extract. It ships with the standard python.org Windows installer. If it's
  missing, Browse… shows a clear error and you can just paste the folder
  path instead — nothing else in the app depends on it.
- Works entirely on `127.0.0.1` (your own machine) — nothing is sent over
  the network, and no account or API key is needed.

---

## 4. Getting Your Export

### ChatGPT

1. Go to **Settings → Data Controls → Export Data**.
2. Confirm the export request.
3. OpenAI emails you a download link. The ZIP contains `conversations.json`
   (plus some other files you can ignore).
4. You can point Refinery at the ZIP directly — no need to unzip it first.

### Claude.ai

1. Go to **Settings → Privacy → Export data**.
2. Choose what to include (Conversations / Users / Projects) and click
   **Export**.
3. You'll get a notification once it's ready, and an emailed download link
   (the link expires in 24 hours).
4. Again, point Refinery at the ZIP directly if you'd rather not unzip it.

Refinery auto-detects which platform each file came from — you can mix
ChatGPT and Claude exports in the same run.

---

## 5. Launching Refinery

Open a terminal (PowerShell on Windows) in the folder containing
`webapp.py`, then:

```
python webapp.py
```

This starts a local server and opens `http://127.0.0.1:8765/` in your
default browser automatically. If it doesn't open on its own, paste that
URL into your browser manually.

**Keep the terminal window open** — closing it (or pressing Ctrl+C inside
it) stops the server, and the page stops working.

### Options

| Flag | What it does |
|---|---|
| `--port N` | Run on a different port (default `8765`) — use this if `8765` is already taken |
| `--no-browser` | Don't automatically open a browser tab |

Example: `python webapp.py --port 8800`

---

## 6. Step 1 — Extract

This is where a raw export becomes a structured list of conversations.

### Manual path entry

Paste one path per line into the text box at the top. Each line can be:

- a `conversations.json` file,
- the export `.zip` (Refinery opens it and finds `conversations.json`
  inside automatically),
- or a folder (Refinery searches it recursively for export files).

Multiple lines are combined into one dataset, and you can mix ChatGPT and
Claude sources freely — format is detected per file.

### Scanning a folder

If you're not sure exactly which file you need, use the box below the
manual entry field:

1. Click **Browse…** to open a native folder picker (or paste a folder path
   directly and click **Scan folder**).
2. Refinery recursively searches that folder for every `.json` and `.zip`
   file, peeks inside each one (including inside zips) to detect whether
   it's a ChatGPT or Claude export, and shows a checklist.
3. Recognized files are pre-checked; unrecognized ones are shown too (with
   a reason, e.g. "no ChatGPT/Claude conversations detected") but left
   unchecked.
4. Click **Add selected to input list** to merge your picks into the
   manual-entry box above. The two methods compose — you can scan a folder
   *and* type in an extra path by hand.

### Options

| Option | What it does |
|---|---|
| Include system prompts | Keeps the system/instruction message (if present) as a `"system"` turn instead of dropping it |
| Include tool/function messages | Keeps tool-call and tool-result messages as `"gpt"` turns instead of dropping them |
| Min turns to keep | Drops any conversation with fewer than this many human+gpt turns (default `2`) |
| Max chars per message | Truncates any single message longer than this many characters (blank = no limit) |

Click **Run extraction**. You'll see a stat breakdown (conversations seen,
kept, dropped as empty, dropped as too short, any unrecognized files) and a
**Continue to Clean →** button.

---

## 7. Step 2 — Clean

This step removes noise from the extracted dataset. It runs in this order:
whitespace/boilerplate cleanup → exact-duplicate removal → near-duplicate
removal → quality filters. You can skip this step entirely with **Skip →
go to Merge** if you'd rather review the raw extraction as-is.

### Options

| Option | What it does |
|---|---|
| Min turns | Drops conversations with fewer than this many human+gpt turns (default `2`) |
| Min words | Drops conversations with fewer than this many total words across all turns (default `8`) |
| Near-duplicate threshold | Word-overlap similarity (0–1) above which two conversations are treated as duplicates; the longer one is kept (default `0.9`; set to `0` to disable near-duplicate detection entirely) |
| Max total characters | Drops conversations whose combined text is longer than this (blank = no limit) — useful for keeping examples within a model's context window |
| Remove exact duplicates | Toggles exact (byte-for-byte, after whitespace normalization) duplicate removal |
| Strip canned AI disclaimers | Removes openers like "As an AI language model," or "Certainly!" from the start of assistant turns |
| Drop refusal-only replies | Drops any conversation where an assistant turn is a short canned refusal (e.g. "I can't help with that request") |

Click **Run cleaning**. You'll see counts for input conversations, exact
dupes removed, near-dupes removed, drops by reason, and the final output
count, then **Continue to Merge →**.

---

## 8. Step 3 — Merge

This step is for combining your current working dataset with *other*
already-cleaned ShareGPT JSON files — for example, output from a previous
Refinery run, or a dataset a teammate cleaned separately. It's optional;
click **Skip → go to Review** if you don't have anything else to merge in.

### How it works

1. Paste the path(s) to other ShareGPT JSON files, one per line, into the
   text box. (You don't need to add your current dataset — it's included
   automatically.)
2. Click **Load & analyze overlap**. Refinery pools every conversation
   together (tagging each with which file it came from) and looks for
   exact and near-duplicates *across the whole pool*, not just within one
   file.
3. Duplicate/near-duplicate conversations are grouped together — even a
   chain of three or more similar conversations from different files ends
   up in one group, not three separate pairs.
4. Each group shows every copy with its source file, title, and turn
   count. The copy Refinery recommends keeping (the one with the most
   turns, then the most total text) is pre-checked; everything else in the
   group starts unchecked.
5. Adjust the checkboxes if you disagree with the recommendation — you can
   keep more than one copy from a group if you want.
6. Click **Apply & continue to Review →**. Every checked conversation
   (plus every conversation that wasn't part of any duplicate group) becomes
   your new working dataset.

Near-duplicate detection here uses the same 0.9 similarity threshold as the
Clean step and isn't currently adjustable from the Merge screen (run Clean
first with a different threshold if you need finer control before merging).

### Using Merge on its own

You don't have to run Extract first. If you land on Merge with an empty
working dataset and just paste in file paths, those files become your
entire pool — useful if you already have several cleaned datasets sitting
around and just want to de-duplicate and combine them.

---

## 9. Step 4 — Review

This is a full editor over the dataset — browse, search, fix, or delete
anything before it gets exported.

- **Left sidebar**: every conversation in the dataset, with a search box at
  the top. Search matches conversation titles, IDs, and message text.
- **Click a conversation** to open it in the main panel.
- **Title**: editable at the top of each conversation.
- **Each turn** shows a role pill (`system` / `human` / `gpt`), a dropdown
  to change that role, a text area to edit the message, and a **Delete
  turn** button.
- **+ Add human/gpt turn**: appends a new turn at the end, alternating role
  automatically based on the last existing turn.
- **Delete conversation**: removes the whole conversation (asks for
  confirmation first).
- **+ New conversation**: adds a blank conversation with one human and one
  gpt turn, ready to fill in.
- **Check validity**: runs the same schema check that Export uses, without
  saving anything. Conversations with errors are highlighted in red in the
  sidebar, and a badge plus an issues list explain exactly what's wrong and
  where.

Nothing here touches disk — it's all in-memory until you save in Export.
Click **Continue to Export →** when you're satisfied.

---

## 10. Step 5 — Export

This is the only step that writes anything to disk, and it always
validates first.

1. **Export format**: pick the output schema. Only **ShareGPT
   (`from`/`value`)** is available today — it's also Refinery's internal
   format, so this is effectively free. Alpaca and OpenAI-messages/ChatML
   are listed as "coming soon" (greyed out) — the conversion logic already
   exists in `export_formats.py`, it's just not switched on yet.
2. **Output file path**: where to save. Refinery pre-fills this based on
   where your extracted file(s) came from, but you can change it to
   anywhere.
3. **Train/val split ratio**: optional. Enter e.g. `0.9` to get
   `yourfile.train.json` and `yourfile.val.json` split 90/10, in file
   order. Leave blank for a single output file.
4. **Write JSON Lines (.jsonl)**: writes one JSON object per line instead
   of a single JSON array, if your training tool expects that.
5. Click **Validate & Save**.

### What gets checked

Before anything is written, Refinery validates the dataset against the
schema a LoRA trainer expects:

- Every conversation has a non-empty list of turns.
- Every turn's role is one of `system`, `human`, or `gpt`.
- Every turn has non-empty text.
- If present, a `system` turn must be first.
- Turns must strictly alternate `human`, `gpt`, `human`, `gpt`, … — no two
  turns from the same role in a row.
- Each conversation must start with `human` (after an optional leading
  `system` turn).

**If any of these fail, nothing is saved.** You'll see the exact list of
errors (which conversation, which turn, what's wrong) and a **Go fix in
Review** button that jumps you back to fix them.

Warnings (e.g. a conversation with only one turn, or one that ends on a
`human` turn with no reply) don't block saving, but are shown so you know
about them.

---

## 11. Moving Between Steps

- The stepper at the top shows all five steps. Click any step to jump to
  it — you're not locked into moving forward only.
- A step is only reachable if its prerequisite is met (e.g. Review and
  Export need at least one conversation in the working dataset).
- **Re-running an earlier step after editing in Review will ask for
  confirmation first**, since it discards those manual edits — Extract and
  Clean both check for this before running again.
- **Start over** (top right) clears the entire working dataset and resets
  every step back to its starting state. It doesn't touch anything already
  saved to disk.

---

## 12. Understanding the Output Format

Refinery's native format — and the only export format currently available
— is ShareGPT:

```json
[
  {
    "id": "chatgpt_67a678d3-...",
    "source": "chatgpt",
    "title": "Conversation title",
    "conversations": [
      {"from": "system", "value": "You are a helpful assistant."},
      {"from": "human", "value": "How do I reverse a string in Python?"},
      {"from": "gpt", "value": "Use s[::-1] to reverse a string."}
    ]
  }
]
```

- `id`, `source`, and `title` are metadata — most trainers ignore them and
  only read `conversations`.
- This is the format expected by Axolotl's `sharegpt` dataset type,
  LLaMA-Factory's `sharegpt` formatting, and Unsloth's
  `standardize_sharegpt()` helper. If your trainer expects `role`/`content`
  instead of `from`/`value`, that's a one-line transform most frameworks
  handle for you automatically (Unsloth's `standardize_sharegpt` does
  exactly this).

---

## 13. Using the Command-Line Scripts Instead

Everything the web app does is also available as three standalone scripts,
useful for scripting the pipeline (batch jobs, CI, etc.) without a browser.
`webapp.py` imports and calls these directly, so behavior is identical
either way.

### `chat_to_lora.py` — Extract

```
python chat_to_lora.py INPUT [INPUT ...] -o output.json [options]
```

| Flag | What it does |
|---|---|
| `-o, --output PATH` | Output file (default `lora_dataset.json`) |
| `--min-turns N` | Minimum human+gpt turns to keep (default `2`) |
| `--include-system` | Keep the system prompt as a `system` turn |
| `--include-tool` | Keep tool/function messages as `gpt` turns |
| `--max-chars N` | Truncate any message to N characters |
| `--jsonl` | Write JSON Lines instead of a JSON array |
| `--split RATIO` | Also write a `.train.json` / `.val.json` split |
| `--stats` | Print summary counts only, don't write a file |

### `clean_dataset.py` — Clean

```
python clean_dataset.py INPUT [INPUT ...] -o clean.json [options]
```

| Flag | What it does |
|---|---|
| `-o, --output PATH` | Output file (default `clean_dataset.json`) |
| `--min-turns N` | Minimum turns to keep (default `2`) |
| `--min-words N` | Minimum total words to keep (default `8`) |
| `--near-dup-threshold F` | Similarity threshold for near-dup removal (default `0.9`; `0` disables) |
| `--no-dedup-exact` | Disable exact-duplicate removal |
| `--no-strip-boilerplate` | Disable stripping canned AI-disclaimer phrases |
| `--drop-refusals` | Drop conversations with a canned refusal-only reply |
| `--max-total-chars N` | Drop conversations longer than N characters |
| `--jsonl` | Write JSON Lines instead of a JSON array |
| `--split RATIO` | Also write a train/val split |
| `--stats` | Print summary counts only, don't write a file |

Passing multiple input files to `clean_dataset.py` combines them first —
this is the same cross-file dedup the Merge step does, just from the
command line (though without Merge's grouped-report UI).

### `review_dataset.py` — Review (standalone editor)

```
python review_dataset.py INPUT.json [options]
```

Opens a lighter, single-purpose version of the Review step as its own local
web page — no Extract/Clean/Merge/Export around it, just load one file, edit
it, and save.

| Flag | What it does |
|---|---|
| `-o, --output PATH` | Where to save edits (default `INPUT.reviewed.json`) |
| `--in-place` | Save back to the input file (keeps a `.bak` backup) |
| `--port N` | Port to serve on (default `8765`) |
| `--no-browser` | Don't automatically open a browser tab |

### Typical pipeline

```
python chat_to_lora.py export.zip -o raw_dataset.json
python clean_dataset.py raw_dataset.json -o clean_dataset.json --split 0.9
python review_dataset.py clean_dataset.train.json --in-place
```

---

## 14. Troubleshooting

**"skipping missing path" / nothing gets extracted**
The path you entered doesn't exist on this machine, or has a typo. Refinery
reads files directly from disk — there's no upload step, so the exact path
matters. Use the folder scan or Browse… button if you're not sure of the
exact filename.

**Browse… shows "This Python install doesn't include tkinter"**
Your Python installation doesn't have tkinter bundled. Paste the folder
path into the text box instead and click **Scan folder** — everything else
works the same without it.

**Port already in use**
Something else is using port 8765. Run `python webapp.py --port 8800` (or
any other free port) instead.

**Export says "Not saved — N validation error(s)"**
The dataset has a structural problem (empty text, two same-role turns in a
row, a conversation that doesn't start with `human`, etc.). Nothing is
written when this happens. Click **Go fix in Review** — the sidebar
highlights exactly which conversations have errors, and the issues panel
lists what's wrong in each one.

**A conversation shows 0 turns / got dropped entirely during Extract**
Conversations with no visible text (pure system-message threads, deleted
messages, tool-only exchanges without `--include-tool`/"Include tool
messages" checked) are dropped automatically — this is expected, not a
bug. Check the "Dropped (empty)" and "Dropped (too short)" stats after
extraction to see how many were filtered this way.

**The Clean step found 0 duplicates but I know there are some**
The near-duplicate threshold (default `0.9`) requires 90% word overlap.
Lower it (e.g. `0.85`) to catch more loosely-worded duplicates, or raise it
if it's being too aggressive and merging conversations that are actually
different.

**A large export is slow to load**
Multi-hundred-MB `conversations.json` files (common for long-time ChatGPT
users) can take a few seconds to parse — this is normal. The folder-scan
preview skips full parsing for files over 150MB (it'll still be parsed
correctly during the actual extraction, just not previewed).

---

## 15. Tips for a Good Training Dataset

- **Turn count**: very short conversations (1 exchange) provide less
  training signal than multi-turn ones. The default `min-turns: 2` filters
  out single-exchange chats — raise it if you specifically want
  longer-conversation examples.
- **Near-duplicate threshold**: 0.9 is a conservative default — it avoids
  accidentally merging conversations that are topically similar but
  meaningfully different. Lower it cautiously; check what gets removed
  before trusting a lower threshold on a large dataset.
- **Dataset size**: as a rough guideline (echoed in Unsloth's own dataset
  documentation), 100 examples is a bare minimum for fine-tuning to show
  any effect; 1,000+ is preferable for reliable results. If your cleaned
  dataset is small, the Merge step is useful for combining several export
  sessions into one larger pool.
- **System prompts**: only include them (Extract → "Include system
  prompts") if you actually want the model to learn from that specific
  instruction context. If your system prompts vary a lot between
  conversations, including them can add noise rather than signal.
- **Review before you trust it**: automated cleaning (dedup, boilerplate
  stripping) is not perfect — skim through Review before exporting,
  especially for a dataset you're about to spend GPU time training on.

---

## 16. FAQ

**Does Refinery send my conversations anywhere?**
No. It's a local server bound to `127.0.0.1` only — nothing leaves your
machine. There's no analytics, no external API calls, no account required.

**Can I use it fully offline?**
Yes. No CDN assets, no external fonts, no network calls except to your own
`127.0.0.1` server.

**Can I mix ChatGPT and Claude exports in one dataset?**
Yes — format is auto-detected per file in Extract, and Merge works across
any combination of already-cleaned files regardless of original source.

**What happens to conversations that fail validation — are they deleted?**
No. A failed validation blocks the *save*, not the data. Nothing is lost —
go to Review, fix the flagged conversations (or delete them if you'd
rather), and try exporting again.

**Can I add support for another training format (Alpaca, ChatML, etc.)?**
Yes — `export_formats.py` is a small registry designed for this. Converter
functions for Alpaca and OpenAI-messages already exist as a starting point;
flip `"available": True` for that entry once you're happy with the
conversion, and it appears in the Export dropdown automatically (the
dropdown is populated from the backend, not hardcoded in the page).

**Is my data safe if I close the browser tab by accident?**
As long as the terminal running `python webapp.py` is still open, reload
the page — but note the working dataset lives in the browser tab's memory,
not on the server, so closing the tab does lose unsaved progress. Export
anything you don't want to lose before closing the tab.
