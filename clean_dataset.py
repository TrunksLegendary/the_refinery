#!/usr/bin/env python3
"""
clean_dataset.py

Second-pass cleanup/optimization for the ShareGPT-format JSON produced by
chat_to_lora.py. Removes exact and near-duplicate conversations, drops
low-content/low-quality conversations, strips common boilerplate phrases,
and normalizes whitespace — so the dataset you actually fine-tune on is
smaller and higher-signal than the raw export.

Typical pipeline:
    python chat_to_lora.py export.zip -o raw_dataset.json
    python clean_dataset.py raw_dataset.json -o clean_dataset.json

Usage:
    python clean_dataset.py INPUT [INPUT ...] -o clean_dataset.json

Options:
    -o, --output PATH          Output JSON file (default: clean_dataset.json)
    --min-turns N                Drop conversations with fewer than N human/gpt turns (default: 2)
    --min-words N                 Drop conversations with fewer than N total words across all turns (default: 8)
    --near-dup-threshold F        Jaccard word-overlap threshold above which two conversations are
                                    considered near-duplicates, 0-1 (default: 0.9). Set to 0 to disable.
    --no-dedup-exact               Disable exact-duplicate removal
    --no-strip-boilerplate         Disable stripping of canned AI-disclaimer phrases
    --drop-refusals                 Drop conversations containing a canned refusal-only assistant turn
    --max-total-chars N            Drop conversations whose combined text exceeds N characters
    --jsonl                        Write JSON Lines instead of a single JSON array
    --split RATIO                   Also write a train/val split, e.g. --split 0.9
    --stats                        Print summary statistics only, do not write output
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_dataset(inputs: list[str]) -> list[dict]:
    dataset: list[dict] = []
    for raw in inputs:
        p = Path(raw)
        if not p.exists():
            print(f"  ! skipping missing path: {raw}", file=sys.stderr)
            continue
        with p.open("r", encoding="utf-8") as f:
            if p.suffix.lower() == ".jsonl":
                for line in f:
                    line = line.strip()
                    if line:
                        dataset.append(json.loads(line))
            else:
                data = json.load(f)
                if isinstance(data, list):
                    dataset.extend(data)
                elif isinstance(data, dict) and "conversations" in data:
                    dataset.append(data)
    return dataset


# --------------------------------------------------------------------------
# Text normalization helpers
# --------------------------------------------------------------------------

WHITESPACE_RE = re.compile(r"[ \t]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
WORD_RE = re.compile(r"[a-zA-Z0-9']+")

# Anchored at the start of a turn only, so we don't mangle legitimate
# mid-sentence content. Conservative list of the most common canned openers.
BOILERPLATE_PATTERNS = [
    re.compile(r"^as an ai language model,?\s*", re.IGNORECASE),
    re.compile(r"^as an ai,?\s*", re.IGNORECASE),
    re.compile(r"^i'm claude,? an ai assistant (made|created|built) by anthropic\.?\s*", re.IGNORECASE),
    re.compile(r"^i'm an ai (assistant|language model)( created by \w+)?\.?\s*", re.IGNORECASE),
    re.compile(r"^i don't have (personal )?(opinions|feelings|beliefs)( of my own)?,?\s*(but\s*)?", re.IGNORECASE),
    re.compile(r"^certainly!\s*", re.IGNORECASE),
    re.compile(r"^of course!\s*", re.IGNORECASE),
    re.compile(r"^great question!\s*", re.IGNORECASE),
    re.compile(r"^i'd be happy to help( with that)?!?\s*", re.IGNORECASE),
]

REFUSAL_PATTERNS = [
    re.compile(r"^i (can'?t|cannot|won'?t|am not able to) (help|assist|do that|provide)", re.IGNORECASE),
    re.compile(r"^i'?m (sorry|not able to),? (but )?i (can'?t|cannot)", re.IGNORECASE),
    re.compile(r"^sorry,? i (can'?t|cannot|am not able to)", re.IGNORECASE),
    re.compile(r"^i'?m unable to (help|assist)", re.IGNORECASE),
]
REFUSAL_MAX_WORDS = 40


def normalize_whitespace(text: str) -> str:
    text = WHITESPACE_RE.sub(" ", text)
    text = BLANK_LINES_RE.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def strip_boilerplate(text: str) -> str:
    stripped = text
    for pat in BOILERPLATE_PATTERNS:
        new = pat.sub("", stripped, count=1)
        if new != stripped:
            stripped = new.lstrip()
    return stripped


def is_refusal_only(text: str) -> bool:
    words = text.split()
    if len(words) > REFUSAL_MAX_WORDS:
        return False
    return any(pat.search(text.strip()) for pat in REFUSAL_PATTERNS)


def word_set(text: str) -> set[str]:
    return set(w.lower() for w in WORD_RE.findall(text))


def conversation_text(conv: dict) -> str:
    return "\n".join(t.get("value", "") for t in conv.get("conversations", []))


def conversation_hash_key(conv: dict) -> str:
    text = conversation_text(conv)
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return normalized


# --------------------------------------------------------------------------
# Cleaning passes
# --------------------------------------------------------------------------

def apply_text_cleanup(conv: dict, do_strip_boilerplate: bool) -> dict:
    new_turns = []
    for turn in conv.get("conversations", []):
        value = normalize_whitespace(turn.get("value", ""))
        if do_strip_boilerplate and turn.get("from") == "gpt":
            value = strip_boilerplate(value)
        if not value:
            continue
        new_turns.append({**turn, "value": value})
    return {**conv, "conversations": new_turns}


def count_words(conv: dict) -> int:
    return len(WORD_RE.findall(conversation_text(conv)))


def count_turns(conv: dict) -> int:
    return sum(1 for t in conv.get("conversations", []) if t.get("from") in ("human", "gpt"))


def has_refusal_only_turn(conv: dict) -> bool:
    for t in conv.get("conversations", []):
        if t.get("from") == "gpt" and is_refusal_only(t.get("value", "")):
            return True
    return False


def dedup_exact(dataset: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    kept = []
    removed = 0
    for conv in dataset:
        key = conversation_hash_key(conv)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(conv)
    return kept, removed


def dedup_near(dataset: list[dict], threshold: float) -> tuple[list[dict], int]:
    if threshold <= 0:
        return dataset, 0

    # Bucket by the first few normalized words of the first human turn so we
    # only run the O(n^2) similarity check within small buckets, not across
    # the whole dataset.
    def bucket_key(conv: dict) -> str:
        for t in conv.get("conversations", []):
            if t.get("from") == "human":
                words = WORD_RE.findall(t.get("value", "").lower())
                return " ".join(words[:6])
        return ""

    buckets: dict[str, list[int]] = {}
    for i, conv in enumerate(dataset):
        buckets.setdefault(bucket_key(conv), []).append(i)

    word_sets = [word_set(conversation_text(c)) for c in dataset]
    lengths = [count_turns(c) for c in dataset]
    removed_idx: set[int] = set()

    for idx_list in buckets.values():
        for a in range(len(idx_list)):
            i = idx_list[a]
            if i in removed_idx:
                continue
            for b in range(a + 1, len(idx_list)):
                j = idx_list[b]
                if j in removed_idx:
                    continue
                wi, wj = word_sets[i], word_sets[j]
                if not wi or not wj:
                    continue
                intersection = len(wi & wj)
                union = len(wi | wj)
                similarity = intersection / union if union else 0
                if similarity >= threshold:
                    # keep whichever has more turns (more training signal)
                    if lengths[i] >= lengths[j]:
                        removed_idx.add(j)
                    else:
                        removed_idx.add(i)
                        break  # i is gone, stop comparing it further

    kept = [c for i, c in enumerate(dataset) if i not in removed_idx]
    return kept, len(removed_idx)


def find_duplicate_groups(dataset: list[dict], near_dup_threshold: float = 0.9,
                           include_exact: bool = True) -> list[dict]:
    """Group conversations across a dataset that are exact or near-duplicates
    of each other, without removing anything. Used for cross-file overlap
    reports (e.g. combining several already-cleaned exports) where the caller
    wants to see the groups and choose what to keep, rather than have one
    side silently dropped like dedup_exact/dedup_near do.

    Returns a list of {"indices": [i, j, ...]} dicts, one per group with 2+
    members that are exact or near-duplicates of each other (connected via
    union-find, so a chain of pairwise-similar conversations ends up in one
    group even if the first and last aren't directly similar enough).
    """
    n = len(dataset)
    parent = list(range(n))

    def uf_find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def uf_union(a: int, b: int) -> None:
        ra, rb = uf_find(a), uf_find(b)
        if ra != rb:
            parent[ra] = rb

    if include_exact:
        seen: dict[str, int] = {}
        for i, conv in enumerate(dataset):
            key = conversation_hash_key(conv)
            if key in seen:
                uf_union(i, seen[key])
            else:
                seen[key] = i

    if near_dup_threshold and near_dup_threshold > 0:
        def bucket_key(conv: dict) -> str:
            for t in conv.get("conversations", []):
                if t.get("from") == "human":
                    words = WORD_RE.findall(t.get("value", "").lower())
                    return " ".join(words[:6])
            return ""

        buckets: dict[str, list[int]] = {}
        for i, conv in enumerate(dataset):
            buckets.setdefault(bucket_key(conv), []).append(i)

        word_sets = [word_set(conversation_text(c)) for c in dataset]

        for idx_list in buckets.values():
            for a in range(len(idx_list)):
                i = idx_list[a]
                for b in range(a + 1, len(idx_list)):
                    j = idx_list[b]
                    wi, wj = word_sets[i], word_sets[j]
                    if not wi or not wj:
                        continue
                    intersection = len(wi & wj)
                    union_size = len(wi | wj)
                    similarity = intersection / union_size if union_size else 0
                    if similarity >= near_dup_threshold:
                        uf_union(i, j)

    groups_map: dict[int, list[int]] = {}
    for i in range(n):
        root = uf_find(i)
        groups_map.setdefault(root, []).append(i)

    return [{"indices": idxs} for idxs in groups_map.values() if len(idxs) > 1]


# --------------------------------------------------------------------------
# Main driver
# --------------------------------------------------------------------------

def clean(dataset: list[dict], min_turns: int, min_words: int, near_dup_threshold: float,
          dedup_exact_enabled: bool, strip_boilerplate_enabled: bool, drop_refusals: bool,
          max_total_chars: int | None) -> tuple[list[dict], dict]:
    stats = {
        "input_conversations": len(dataset),
        "removed_exact_duplicates": 0,
        "removed_near_duplicates": 0,
        "dropped_too_short_turns": 0,
        "dropped_too_few_words": 0,
        "dropped_too_long": 0,
        "dropped_refusal_only": 0,
        "output_conversations": 0,
    }

    # 1. text cleanup (whitespace, boilerplate) applied first so downstream
    #    length/word checks operate on cleaned text
    cleaned = [apply_text_cleanup(c, strip_boilerplate_enabled) for c in dataset]

    # 2. exact dedup
    if dedup_exact_enabled:
        cleaned, removed = dedup_exact(cleaned)
        stats["removed_exact_duplicates"] = removed

    # 3. near-dedup
    cleaned, removed = dedup_near(cleaned, near_dup_threshold)
    stats["removed_near_duplicates"] = removed

    # 4. quality filters
    final = []
    for conv in cleaned:
        if count_turns(conv) < min_turns:
            stats["dropped_too_short_turns"] += 1
            continue
        if count_words(conv) < min_words:
            stats["dropped_too_few_words"] += 1
            continue
        if max_total_chars and len(conversation_text(conv)) > max_total_chars:
            stats["dropped_too_long"] += 1
            continue
        if drop_refusals and has_refusal_only_turn(conv):
            stats["dropped_refusal_only"] += 1
            continue
        final.append(conv)

    stats["output_conversations"] = len(final)
    return final, stats


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
    ap = argparse.ArgumentParser(description="Dedup, filter, and clean a ShareGPT-format LoRA dataset.")
    ap.add_argument("inputs", nargs="+", help="ShareGPT JSON/JSONL file(s) (output of chat_to_lora.py)")
    ap.add_argument("-o", "--output", default="clean_dataset.json", help="output path (default: clean_dataset.json)")
    ap.add_argument("--min-turns", type=int, default=2, help="minimum human+gpt turns to keep (default: 2)")
    ap.add_argument("--min-words", type=int, default=8, help="minimum total words to keep (default: 8)")
    ap.add_argument("--near-dup-threshold", type=float, default=0.9,
                     help="Jaccard similarity threshold for near-duplicate removal, 0-1 (default: 0.9, 0 disables)")
    ap.add_argument("--no-dedup-exact", action="store_true", help="disable exact-duplicate removal")
    ap.add_argument("--no-strip-boilerplate", action="store_true", help="disable stripping canned AI-disclaimer phrases")
    ap.add_argument("--drop-refusals", action="store_true", help="drop conversations containing a canned refusal-only reply")
    ap.add_argument("--max-total-chars", type=int, default=None, help="drop conversations longer than this many characters")
    ap.add_argument("--jsonl", action="store_true", help="write JSON Lines instead of a single JSON array")
    ap.add_argument("--split", type=float, default=None, help="train/val split ratio, e.g. 0.9")
    ap.add_argument("--stats", action="store_true", help="only print stats, don't write output")
    args = ap.parse_args()

    print("Loading dataset...")
    dataset = load_dataset(args.inputs)
    print(f"  loaded {len(dataset)} conversation(s) from {len(args.inputs)} input(s)")

    print("Cleaning...")
    cleaned, stats = clean(
        dataset,
        min_turns=args.min_turns,
        min_words=args.min_words,
        near_dup_threshold=args.near_dup_threshold,
        dedup_exact_enabled=not args.no_dedup_exact,
        strip_boilerplate_enabled=not args.no_strip_boilerplate,
        drop_refusals=args.drop_refusals,
        max_total_chars=args.max_total_chars,
    )

    print("\nSummary:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    if args.stats:
        return

    if not cleaned:
        print("\nNo conversations remaining after cleaning — nothing written.", file=sys.stderr)
        sys.exit(1)

    write_output(cleaned, Path(args.output), args.jsonl, args.split)


if __name__ == "__main__":
    main()
