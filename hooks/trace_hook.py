#!/usr/bin/env python3
"""
GAIDE-Trace — universal hook logger for Claude Code.

Reads the hook event JSON from stdin and appends one normalized JSONL line
to the trace store. Designed to be registered for multiple hook events
(SessionStart, UserPromptSubmit, PostToolUse, Stop, SubagentStop, SessionEnd, ...).

Guarantees:
- Never blocks the agent: always exits 0, even on internal errors.
- Never prints to stdout (stdout of some hooks is injected into the model context).
- Redacts obvious secrets and truncates oversized payloads (raw fidelity is
  preserved separately via the transcript snapshot at SessionEnd).

Environment variables (all optional):
  GAIDE_TRACE_DIR        Store location. Default: <cwd>/.gaide-trace
  GAIDE_TRACE_MAX_FIELD  Max chars kept per captured field. Default: 20000
  GAIDE_TRACE_DISABLE    Set to "1" to disable logging entirely.
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- redaction

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)"
               r"(\"?\s*[:=]\s*\"?)([^\s\"',;]{8,})"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                    # OpenAI/Anthropic-style keys
    re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),  # GitHub
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact(text: str) -> str:
    def _kv(m):
        return m.group(1) + m.group(2) + "[REDACTED]"
    text = SECRET_PATTERNS[0].sub(_kv, text)
    for pat in SECRET_PATTERNS[1:]:
        text = pat.sub("[REDACTED]", text)
    return text


def clean(value, max_len):
    """Serialize, redact and truncate any captured value."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    value = redact(value)
    if len(value) > max_len:
        value = value[:max_len] + f"…[truncated, {len(value)} chars total]"
    return value


# ---------------------------------------------------------------- main

def main() -> None:
    if os.environ.get("GAIDE_TRACE_DISABLE") == "1":
        return

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {"_parse_error": True, "_raw_head": raw[:500]}

    max_len = int(os.environ.get("GAIDE_TRACE_MAX_FIELD", "20000"))
    cwd = event.get("cwd") or os.getcwd()
    trace_dir = Path(os.environ.get("GAIDE_TRACE_DIR") or Path(cwd) / ".gaide-trace")
    events_dir = trace_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    session_id = event.get("session_id") or "unknown-session"
    record = {
        "trace_id": uuid.uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "unknown"),
        "session_id": session_id,
        "prompt_id": event.get("prompt_id"),
        "cwd": cwd,
        "permission_mode": event.get("permission_mode"),
        "model": event.get("model"),
        "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "tool_name": event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "tool_input": clean(event.get("tool_input"), max_len),
        "tool_response": clean(event.get("tool_response"), max_len),
        "prompt": clean(event.get("prompt"), max_len),
        "last_assistant_message": clean(event.get("last_assistant_message"), max_len),
        "transcript_path": event.get("transcript_path"),
    }
    # Drop empty fields to keep the JSONL lean.
    record = {k: v for k, v in record.items() if v is not None}

    out_file = events_dir / f"{session_id}.jsonl"
    with out_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # On session end, snapshot the full transcript before Claude Code's
    # retention cleanup can delete it. The transcript is the highest-fidelity
    # record (full messages, tool calls, token usage per turn).
    if record["event"] in ("SessionEnd", "Stop"):
        src = event.get("transcript_path")
        if src and Path(src).is_file():
            snap_dir = trace_dir / "transcripts"
            snap_dir.mkdir(parents=True, exist_ok=True)
            dst = snap_dir / f"{session_id}.jsonl"
            try:
                data = Path(src).read_bytes()
                # Overwrite: each snapshot supersedes the previous partial one.
                dst.write_bytes(data)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Trace logging must never break the agent session.
        pass
    sys.exit(0)
