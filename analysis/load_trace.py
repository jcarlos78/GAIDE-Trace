#!/usr/bin/env python3
"""
GAIDE-Trace analysis loader.

Turns a .gaide-trace/ store into tidy pandas DataFrames ready for
research analysis (pattern mining, session reconstruction, token accounting).

Usage as a library:
    from load_trace import load_events, load_transcripts
    events = load_events(".gaide-trace")          # hook-level event log
    turns  = load_transcripts(".gaide-trace")     # full-fidelity transcript turns

Usage from the CLI:
    python3 load_trace.py /path/to/.gaide-trace   # prints a summary
"""

import json
import sys
from pathlib import Path

import pandas as pd


def load_events(trace_dir) -> pd.DataFrame:
    """One row per hook event (prompt submitted, tool used, turn ended...)."""
    rows = []
    for f in sorted(Path(trace_dir, "events").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], format="ISO8601")
        df = df.sort_values("ts").reset_index(drop=True)
    return df


def load_transcripts(trace_dir) -> pd.DataFrame:
    """One row per transcript entry (user/assistant messages, tool calls),
    with token usage where Claude Code recorded it."""
    rows = []
    for f in sorted(Path(trace_dir, "transcripts").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = e.get("message") or {}
            usage = msg.get("usage") or {}
            content = msg.get("content")
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
                tools = [c.get("name") for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]
            else:
                text, tools = (content if isinstance(content, str) else None), []
            rows.append({
                "session_id": f.stem,
                "uuid": e.get("uuid"),
                "parent_uuid": e.get("parentUuid"),
                "ts": e.get("timestamp"),
                "type": e.get("type"),
                "role": msg.get("role"),
                "model": msg.get("model"),
                "text": text,
                "tool_calls": tools or None,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_read_tokens": usage.get("cache_read_input_tokens"),
                "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], format="ISO8601", errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
    return df


def summarize(trace_dir) -> str:
    ev = load_events(trace_dir)
    tr = load_transcripts(trace_dir)
    lines = ["== GAIDE-Trace summary =="]
    if ev.empty:
        lines.append("no events recorded yet")
    else:
        lines.append(f"sessions: {ev['session_id'].nunique()}")
        lines.append(f"events:   {len(ev)}  ({ev['ts'].min()} → {ev['ts'].max()})")
        lines.append(f"prompts submitted: {(ev['event'] == 'UserPromptSubmit').sum()}")
        if "tool_name" in ev:
            top = ev.loc[ev["event"] == "PostToolUse", "tool_name"].value_counts().head(10)
            if not top.empty:
                lines.append("top tools: " + ", ".join(f"{k}×{v}" for k, v in top.items()))
    if not tr.empty:
        lines.append(f"transcript turns: {len(tr)}")
        for col in ("input_tokens", "output_tokens"):
            if col in tr:
                total = int(tr[col].fillna(0).sum())
                lines.append(f"{col.replace('_', ' ')}: {total:,}")
    return "\n".join(lines)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ".gaide-trace"
    print(summarize(target))
