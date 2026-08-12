#!/usr/bin/env python3
"""
export_formats.py

Registry of output dataset formats for the ChatExtract pipeline.

Our internal/canonical working format everywhere in this app (extraction,
cleaning, review, validation) is ShareGPT: a list of

    {"id": ..., "title": ..., "conversations": [{"from": "human"|"gpt"|"system", "value": "..."}]}

records. This module defines how that canonical dataset gets turned into
bytes on disk for a given target format. ShareGPT is implemented (it's an
identity transform, since that's already our native shape). To add a new
format:

  1. Write a converter function: `list[dict] (ShareGPT) -> list[dict] (target shape)`.
  2. Register it in FORMATS below with a label, short description, and
     default file extension.

The webapp UI reads FORMATS to populate the export dropdown, and any format
without an `available: True` flag is shown greyed-out as "coming soon" so the
option is visible without being clickable.
"""

from typing import Any


def to_sharegpt(conversations: list[dict]) -> list[dict]:
    """ShareGPT is our canonical in-memory format, so this is an identity
    transform — included for symmetry with future formats and so the save
    pipeline can always go through a converter uniformly."""
    return conversations


def to_alpaca(conversations: list[dict]) -> list[dict]:
    """Flatten each human/gpt turn pair into an Alpaca-style
    {instruction, input, output} record. Multi-turn conversations become
    multiple rows, one per human->gpt pair, with prior turns folded into
    `input` as context.

    Not wired into the UI yet (see FORMATS below) — this is a starting
    point for whoever picks up Alpaca support next.
    """
    rows: list[dict] = []
    for conv in conversations:
        history: list[str] = []
        pending_instruction = None
        for turn in conv.get("conversations", []):
            role, value = turn.get("from"), turn.get("value", "")
            if role == "system":
                history.append(f"[system] {value}")
            elif role == "human":
                pending_instruction = value
            elif role == "gpt" and pending_instruction is not None:
                rows.append({
                    "instruction": pending_instruction,
                    "input": "\n".join(history),
                    "output": value,
                })
                history.append(f"Human: {pending_instruction}")
                history.append(f"Assistant: {value}")
                pending_instruction = None
    return rows


def to_openai_messages(conversations: list[dict]) -> list[dict]:
    """Convert to OpenAI/ChatML-style {"messages": [{"role","content"}]}
    records (from/value -> role/content, "gpt" -> "assistant",
    "human" -> "user"). Not wired into the UI yet — starting point only.
    """
    role_map = {"human": "user", "gpt": "assistant", "system": "system"}
    out = []
    for conv in conversations:
        messages = [
            {"role": role_map.get(t.get("from"), t.get("from")), "content": t.get("value", "")}
            for t in conv.get("conversations", [])
        ]
        out.append({"messages": messages})
    return out


FORMATS: dict[str, dict[str, Any]] = {
    "sharegpt": {
        "label": "ShareGPT (from/value)",
        "description": "Native format — {\"conversations\": [{\"from\": \"human\"|\"gpt\", \"value\": ...}]}. "
                        "Used by Axolotl, LLaMA-Factory, Unsloth's standardize_sharegpt, and most SFT trainers.",
        "convert": to_sharegpt,
        "extension": ".json",
        "available": True,
    },
    "alpaca": {
        "label": "Alpaca (instruction/input/output)",
        "description": "Single-turn {instruction, input, output} rows. Coming soon.",
        "convert": to_alpaca,
        "extension": ".json",
        "available": False,
    },
    "openai_messages": {
        "label": "OpenAI messages / ChatML",
        "description": "{\"messages\": [{\"role\", \"content\"}]} rows. Coming soon.",
        "convert": to_openai_messages,
        "extension": ".jsonl",
        "available": False,
    },
}


def available_formats() -> dict[str, dict[str, Any]]:
    return FORMATS


def convert_dataset(conversations: list[dict], format_id: str) -> list[dict]:
    fmt = FORMATS.get(format_id)
    if fmt is None:
        raise ValueError(f"Unknown export format: {format_id!r}")
    if not fmt.get("available", False):
        raise ValueError(f"Export format {format_id!r} is not available yet.")
    return fmt["convert"](conversations)
