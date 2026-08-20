#!/usr/bin/env python3
"""
GAIDE-Trace — Antigravity IDE adapter (incremental importer).

Antigravity (Google's agentic IDE) has no hook system, but it persists every
conversation ("cascade") locally as a SQLite database of protobuf-encoded
steps under ~/.gemini/antigravity-ide/conversations/<uuid>.db. This importer
tails those databases and translates each step into the same canonical,
tool-agnostic event vocabulary used by every GAIDE-Trace adapter
(schema/event.schema.json), appending to the per-project local store and —
when the store is connected to a team server — shipping through the same
offline-safe outbox.

The protobuf schema is not public; decoding is schemaless (wire-format walk)
and field paths were mapped empirically. Payload layout (per step):
    field 1        step type (14 user prompt, 15 model turn, 17 invalid tool
                   call, 23 task update, 98/99 context injection, 101 hook
                   notice; other types are tool executions: 5 write_to_file,
                   8 view_file, 9 list_dir, 21 run_command, 85
                   browser_subagent, 132 manage_task, ...)
    field 4        step status (observed: 3 = done)
    field 5.1.1    unix timestamp (seconds)
    field 5.4      tool call {1: call id, 2: tool name, 3: args JSON}
    field 5.12     step uuid
    field 19.2     user prompt text            (type 14)
    field 20       model turn; 20.7 = proposed tool call {1: id, 2: name}
    field 24.3     error details               (type 17)
    field 30.4     task title                  (type 23)
The trajectory_metadata_blob table's field 1.1 holds the workspace URI, which
maps a conversation to its project (and therefore to its .gaide-trace store).
The gen_metadata table carries the model id (e.g. "gemini-3.7-flash").
A schema change in a future Antigravity release degrades gracefully: steps
that no longer decode are imported as bare `note` events, never dropped.

Import is incremental and idempotent: per-store state records the last
imported step index per conversation, and only terminal steps advance it, so
a step is imported exactly once, after it finished. trace_ids are derived
from Antigravity's own step uuids, so the server-side dedupe holds even if
two machines import the same conversation.

Usage:
  python3 tools/import_antigravity.py                  # import all conversations
                                                       # of connected projects
  python3 tools/import_antigravity.py --workspace ~/GitHub/myproj
  python3 tools/import_antigravity.py --watch 60       # keep importing forever
  python3 tools/import_antigravity.py --create-store   # also projects not yet
                                                       # connected (creates .gaide-trace/)

Environment: GAIDE_TRACE_DIR / _PROJECT / _SERVER / _TOKEN / _MAX_FIELD /
_DISABLE as in the hook adapter; ANTIGRAVITY_CONVERSATIONS_DIR overrides the
default conversations location.
"""

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "antigravity"
DEFAULT_CONVERSATIONS = Path.home() / ".gemini" / "antigravity-ide" / "conversations"
STALE_AFTER = 600  # secs without db writes => treat non-terminal steps as final

# Shared store/outbox/redaction core lives in the Claude Code adapter file
# (kept single-file so install.sh can copy it into projects); import it.
_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "trace_hook", _here.parent / "hooks" / "trace_hook.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

# ------------------------------------------------------------ protobuf walk


def _varint(buf, i):
    result = shift = 0
    while True:
        b = buf[i]; i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def decode(buf, depth=0):
    """Schemaless protobuf decode: bytes -> list of (field, kind, value),
    where kind is 'v' (varint), 'm' (message), 's' (string) or 'b' (bytes).
    Returns None if buf is not a valid message."""
    out, i = [], 0
    try:
        while i < len(buf):
            key, i = _varint(buf, i)
            field, wt = key >> 3, key & 7
            if field == 0:
                return None
            if wt == 0:
                v, i = _varint(buf, i)
                out.append((field, "v", v))
            elif wt == 1:
                out.append((field, "b", buf[i:i + 8])); i += 8
            elif wt == 5:
                out.append((field, "b", buf[i:i + 4])); i += 4
            elif wt == 2:
                ln, i = _varint(buf, i)
                if i + ln > len(buf):
                    return None
                chunk = buf[i:i + ln]; i += ln
                sub = decode(chunk, depth + 1) if depth < 12 and chunk else None
                if sub:
                    out.append((field, "m", sub))
                else:
                    try:
                        out.append((field, "s", chunk.decode("utf-8")))
                    except UnicodeDecodeError:
                        out.append((field, "b", chunk))
            else:
                return None
            if i > len(buf):
                return None
    except (IndexError, TypeError):
        return None
    return out


def get(node, *path):
    """First value at a field path, descending into submessages."""
    for want in path[:-1]:
        node = next((v for f, k, v in node if f == want and k == "m"), None)
        if node is None:
            return None
    return next((v for f, k, v in node if f == path[-1] and k != "m"), None)


def get_msg(node, *path):
    for want in path:
        node = next((v for f, k, v in node if f == want and k == "m"), None)
        if node is None:
            return None
    return node


def longest_string(node, min_len=2, skip_fields=()):
    """Longest decoded string in a subtree — best-effort extraction of the
    human-readable payload (assistant text, tool output) without a schema."""
    best = ""
    for f, k, v in node:
        if f in skip_fields:
            continue
        if k == "s" and len(v) > len(best):
            best = v
        elif k == "m":
            cand = longest_string(v, 1) or ""
            if len(cand) > len(best):
                best = cand
    return best if len(best) >= min_len else None


MODEL_RE = re.compile(rb"(?:gemini|claude|gpt|grok|deepseek|qwen|llama|mistral)"
                      rb"[A-Za-z0-9._-]{2,40}")

ID_LIKE = re.compile(r"^(bot-|call_|[0-9a-f-]{32,}$|-?\d+$)")

# ------------------------------------------------------------ step mapping


def step_ts(tree):
    secs = get(tree, 5, 1, 1)
    if isinstance(secs, int) and secs > 10 ** 9:
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    return None


def map_step(idx, step_type, status, tree, model, max_len):
    """Translate one decoded Antigravity step into canonical record fields."""
    rec = {"native_event": f"step.{step_type}"}
    call = get_msg(tree, 5, 4)
    if call is not None:
        rec["tool_use_id"] = get(call, 1)
        rec["tool_name"] = get(call, 2)
        rec["tool_input"] = core.clean(get(call, 3), max_len)

    if step_type == 14:                                   # user message
        rec["event"] = "prompt.submit"
        prompt = get(tree, 19, 2) or (get_msg(tree, 19) and
                                      longest_string(get_msg(tree, 19)))
        rec["prompt"] = core.clean(prompt, max_len)
    elif step_type == 15:                                 # one model generation
        rec["event"] = "model.turn"
        turn = get_msg(tree, 20)
        if turn is not None:
            proposed = get_msg(turn, 7)
            if proposed is not None:
                rec.setdefault("tool_use_id", get(proposed, 1))
                rec.setdefault("tool_name", get(proposed, 2))
            text = longest_string(turn, min_len=8, skip_fields=(7,))
            if text and not ID_LIKE.match(text):
                rec["last_assistant_message"] = core.clean(text, max_len)
    elif step_type == 17:                                 # model/tool error
        rec["event"] = "tool.fail"
        err = get_msg(tree, 24, 3)
        rec["tool_response"] = core.clean(
            err and (get(err, 2) or get(err, 1)), max_len)
    elif step_type in (98, 99):                           # context injection
        rec["event"] = "context.compact"
    elif step_type in (23, 101):                          # task update / hook notice
        rec["event"] = "note"
        note = get(tree, 30, 4) if step_type == 23 else get(tree, 114, 1)
        rec["last_assistant_message"] = core.clean(note, max_len)
    elif rec.get("tool_name"):                            # tool execution step
        rec["event"] = "tool.call" if status == 3 else "tool.fail"
        out = longest_string(tree, min_len=8, skip_fields=(5, 19, 20))
        if out is not None:
            rec["tool_response"] = core.clean(out, max_len)
    else:
        rec["event"] = "note"

    if model:
        rec["model"] = model
    return {k: v for k, v in rec.items() if v is not None}


# ------------------------------------------------------------ conversations


def conversation_workspace(db):
    """Workspace folder of a conversation, from trajectory_metadata_blob."""
    try:
        row = db.execute("SELECT data FROM trajectory_metadata_blob").fetchone()
    except sqlite3.Error:
        return None
    tree = decode(row[0]) if row and row[0] else None
    uri = tree and get(tree, 1, 1)
    if isinstance(uri, str) and uri.startswith("file://"):
        return Path(urllib_unquote(uri[7:]))
    return None


def urllib_unquote(s):
    import urllib.parse
    return urllib.parse.unquote(s)


def gen_models(db):
    """Model id of each generation, in order. gen_metadata rows align with
    the model-turn (type 15) steps, not with step indexes."""
    models = []
    try:
        for _idx, data in db.execute("SELECT idx, data FROM gen_metadata ORDER BY idx"):
            m = MODEL_RE.search(data or b"")
            models.append(m.group(0).decode("ascii", "replace") if m else None)
    except sqlite3.Error:
        pass
    return models


def import_conversation(db_path: Path, store: Path, project: str,
                        state: dict, max_len: int, dry_run=False):
    """Import new terminal steps of one conversation. Returns records written."""
    sid = db_path.stem
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    stale = (time.time() - db_path.stat().st_mtime) > STALE_AFTER
    conv_state = state.setdefault(sid, {"idx": -1, "gens": 0})
    last_idx = conv_state["idx"]
    gen_no = conv_state.get("gens", 0)
    records = []
    try:
        workspace = conversation_workspace(db)
        models = gen_models(db)
        rows = db.execute(
            "SELECT idx, step_type, status, step_payload FROM steps "
            "WHERE idx > ? ORDER BY idx", (last_idx,)).fetchall()
    except sqlite3.Error:
        db.close()
        return []
    finally:
        db.close()

    for idx, step_type, status, payload in rows:
        # Only terminal steps advance the pointer: a still-running step is
        # imported once, later, in its final form. A stale db (session over)
        # flushes everything.
        if status in (0, 1, 2) and not stale:
            break
        tree = decode(payload or b"")
        if tree is None:
            rec = {"event": "note", "native_event": f"step.{step_type}"}
            ts = None
        else:
            model = models[gen_no] if (step_type == 15 and gen_no < len(models)) else None
            rec = map_step(idx, step_type, status, tree, model, max_len)
            ts = step_ts(tree)
            if step_type == 15:
                gen_no += 1
        # Deterministic per (conversation, step): re-imports and imports from
        # two machines dedupe to the same record server-side.
        trace_id = f"ag-{sid[:8]}-{idx:05d}"
        record = {
            "trace_id": trace_id,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "source": SOURCE,
            "session_id": sid,
            "project": project,
            "cwd": str(workspace) if workspace else None,
            **rec,
        }
        record = {k: v for k, v in record.items() if v is not None}
        if last_idx < 0 and not records:
            records.append({
                "trace_id": f"ag-{sid[:8]}-start",
                "ts": record["ts"], "event": "session.start",
                "native_event": "trajectory.first_import", "source": SOURCE,
                "session_id": sid, "project": project,
                **({"cwd": str(workspace)} if workspace else {}),
            })
        records.append(record)
        conv_state["idx"] = idx
        conv_state["gens"] = gen_no

    if records and not dry_run:
        events_dir = store / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        ship = core.server_config(core.load_config(store)) is not None
        with (events_dir / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                if ship:
                    core.spool(store, r)
    return records


# ------------------------------------------------------------ store routing


def load_state(store: Path):
    f = store / "collectors" / "antigravity.state.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(store: Path, state: dict):
    f = store / "collectors" / "antigravity.state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(f)


def run_once(args, max_len):
    conv_dir = Path(args.conversations)
    if not conv_dir.is_dir():
        print(f"error: conversations dir not found: {conv_dir}", file=sys.stderr)
        return 2
    imported = {}
    touched_stores = set()
    for db_path in sorted(conv_dir.glob("*.db")):
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            workspace = conversation_workspace(db)
            db.close()
        except sqlite3.Error:
            continue
        if args.workspace and (not workspace or
                               Path(args.workspace).resolve() != workspace.resolve()):
            continue
        # Route to the store: explicit --store wins; otherwise the workspace's
        # own .gaide-trace (only if the project opted in, unless --create-store).
        if args.store:
            store = Path(args.store)
        elif workspace and workspace.is_dir():
            store = workspace / ".gaide-trace"
            if not store.is_dir() and not args.create_store:
                continue
        else:
            continue  # workspace unknown/gone and no explicit store
        cfg = core.load_config(store)
        project = (os.environ.get("GAIDE_TRACE_PROJECT") or cfg.get("project")
                   or (workspace.name if workspace else "antigravity"))
        state = load_state(store)
        recs = import_conversation(db_path, store, project, state,
                                   max_len, args.dry_run)
        if recs:
            imported[db_path.stem] = len(recs)
            if not args.dry_run:
                save_state(store, state)
                touched_stores.add(store)

    # Ship everything queued (also drains records spooled by earlier runs).
    for store in touched_stores:
        remote = core.server_config(core.load_config(store))
        if remote:
            server, token = remote
            core.flush_outbox(store, server, token,
                              time.monotonic() + core.SHIP_DEADLINE)
    total = sum(imported.values())
    if total or args.verbose:
        for sid, n in imported.items():
            print(f"  {sid}: +{n} events")
        print(f"imported {total} events from {len(imported)} conversation(s)"
              + (" [dry-run]" if args.dry_run else ""))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--conversations",
                    default=os.environ.get("ANTIGRAVITY_CONVERSATIONS_DIR",
                                           str(DEFAULT_CONVERSATIONS)),
                    help="Antigravity conversations dir (default: %(default)s)")
    ap.add_argument("--workspace", help="only import conversations of this project path")
    ap.add_argument("--store", help="write to this store instead of each "
                                    "workspace's own .gaide-trace/")
    ap.add_argument("--create-store", action="store_true",
                    help="also import projects that never opted in "
                         "(creates their .gaide-trace/)")
    ap.add_argument("--watch", type=int, metavar="SECONDS",
                    help="keep running, importing every SECONDS")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be imported, write nothing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if os.environ.get("GAIDE_TRACE_DISABLE") == "1":
        return 0
    max_len = int(os.environ.get("GAIDE_TRACE_MAX_FIELD", "20000"))
    if args.watch:
        while True:
            run_once(args, max_len)
            time.sleep(args.watch)
    return run_once(args, max_len)


if __name__ == "__main__":
    sys.exit(main())
