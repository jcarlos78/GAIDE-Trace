#!/usr/bin/env python3
"""
GAIDE-Trace Server — team-central interaction ledger.

A single-process, zero-dependency (Python stdlib only) server that receives
trace events and transcript snapshots shipped by `hooks/trace_hook.py` from
every team member's machine, stores them durably, and serves a web console
for exploration, management and export.

Storage model (mirrors the local store's two-layer philosophy):
  data/
  ├── trace.db               SQLite index (WAL) — queries, aggregates, auth
  ├── archive/events/        raw JSONL append log, one file per session
  └── transcripts/           full-fidelity transcript snapshots (one per session)

JSONL stays the source of truth: the SQLite index can always be rebuilt from
the archive (`rebuild-index` command). Ingest is idempotent (deduped by
trace_id), so client retries are always safe.

Auth, two mechanisms:
  * People sign in to the web console with username + password. First run
    creates the default account admin/admin, which MUST change its password
    on first login. Users, projects and keys are then managed visually.
  * Machines (hooks, scripts) authenticate with bearer API keys:
      agent   ingest only (what hooks and install prompts carry)
      member  ingest + read + export (for scripted API access)
      admin   everything
    Each project registered in the console gets its own agent key, embedded
    in a copy-paste install prompt for the team.

Usage:
  python3 gaide_trace_server.py serve [--host 0.0.0.0] [--port 8321] [--data DIR]
  python3 gaide_trace_server.py key create --name alice --role member
  python3 gaide_trace_server.py key list
  python3 gaide_trace_server.py key revoke <id>
  python3 gaide_trace_server.py rebuild-index

Environment (overridden by CLI flags):
  GAIDE_TRACE_SERVER_DATA   data directory (default: ./data)
  GAIDE_TRACE_SERVER_HOST   bind host (default: 0.0.0.0)
  GAIDE_TRACE_SERVER_PORT   bind port (default: 8321)

On first start, if no admin key exists, one is generated and printed once.
Run behind a TLS reverse proxy (Caddy/nginx) for anything non-local — see
docs/SERVER.md.
"""

import argparse
import csv
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.3.0"

MAX_EVENTS_BODY = 32 * 1024 * 1024        # 32 MB per events batch
MAX_TRANSCRIPT_BODY = 256 * 1024 * 1024   # 256 MB per transcript snapshot

EVENT_COLUMNS = [
    "trace_id", "ts", "received_at", "event", "native_event", "source",
    "session_id", "prompt_id",
    "project", "origin", "cwd", "permission_mode", "model", "agent_id",
    "agent_type", "tool_name", "tool_use_id", "tool_input", "tool_response",
    "prompt", "last_assistant_message",
]

# v0.2 stores shipped Claude Code hook names as `event`. Normalize to the
# canonical tool-agnostic vocabulary at ingest/index time; the original name
# is preserved in native_event. Old archives never need rewriting.
LEGACY_EVENTS = {
    "SessionStart": "session.start",
    "UserPromptSubmit": "prompt.submit",
    "PostToolUse": "tool.call",
    "PostToolUseFailure": "tool.fail",
    "Stop": "turn.end",
    "SubagentStart": "agent.start",
    "SubagentStop": "agent.end",
    "PreCompact": "context.compact",
    "SessionEnd": "session.end",
}


def normalize_event(row: dict):
    """Map legacy (v0.2, Claude-shaped) event names to canonical ones."""
    canonical = LEGACY_EVENTS.get(row.get("event"))
    if canonical:
        row["native_event"] = row.get("native_event") or row["event"]
        row["source"] = row.get("source") or "claude-code"
        row["event"] = canonical
    return row

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# ---------------------------------------------------------------- storage


class Store:
    """All persistence: SQLite index + raw JSONL archive + transcript files."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.archive_dir = data_dir / "archive" / "events"
        self.transcripts_dir = data_dir / "transcripts"
        for d in (self.archive_dir, self.transcripts_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "trace.db"
        self._archive_lock = threading.Lock()
        with self.connect() as db:
            self._init_schema(db)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _init_schema(db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS keys (
          id INTEGER PRIMARY KEY,
          token_hash TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('agent','member','admin')),
          created_at TEXT NOT NULL,
          last_seen_at TEXT,
          revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
          trace_id TEXT PRIMARY KEY,
          ts TEXT NOT NULL,
          received_at TEXT NOT NULL,
          event TEXT NOT NULL,
          native_event TEXT,
          source TEXT,
          session_id TEXT NOT NULL,
          prompt_id TEXT,
          project TEXT,
          origin TEXT,
          cwd TEXT,
          permission_mode TEXT,
          model TEXT,
          agent_id TEXT,
          agent_type TEXT,
          tool_name TEXT,
          tool_use_id TEXT,
          tool_input TEXT,
          tool_response TEXT,
          prompt TEXT,
          last_assistant_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, ts);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_project ON events(project, ts);
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          salt TEXT NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('member','admin')),
          must_change_password INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          last_login_at TEXT,
          disabled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS web_sessions (
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY,
          name TEXT UNIQUE NOT NULL,
          created_at TEXT NOT NULL,
          created_by TEXT,
          key_id INTEGER REFERENCES keys(id),
          agent_token TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          project TEXT,
          origin TEXT,
          first_ts TEXT,
          last_ts TEXT,
          events INTEGER NOT NULL DEFAULT 0,
          prompts INTEGER NOT NULL DEFAULT 0,
          tool_calls INTEGER NOT NULL DEFAULT 0,
          failures INTEGER NOT NULL DEFAULT 0,
          models TEXT,
          input_tokens INTEGER,
          output_tokens INTEGER,
          cache_read_tokens INTEGER,
          cache_creation_tokens INTEGER,
          transcript_bytes INTEGER,
          transcript_lines INTEGER,
          transcript_updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_last ON sessions(last_ts);
        """)
        # v0.2 -> v0.3 migration: add columns if the table predates them.
        cols = {r[1] for r in db.execute("PRAGMA table_info(events)")}
        for col in ("native_event", "source"):
            if col not in cols:
                db.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
        db.commit()

    # ---- events ingest ----

    def ingest_events(self, records, origin: str):
        """Insert a batch. Returns (inserted, duplicates). Idempotent by trace_id."""
        now = utcnow()
        inserted, duplicates = 0, 0
        accepted = []
        with self.connect() as db:
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                row = normalize_event({c: rec.get(c) for c in EVENT_COLUMNS})
                if not row["trace_id"] or not row["session_id"] or not row["event"]:
                    continue
                row["received_at"] = now
                row["origin"] = origin
                row["ts"] = row["ts"] or now
                if not SESSION_ID_RE.match(str(row["session_id"])):
                    continue
                cur = db.execute(
                    f"INSERT OR IGNORE INTO events ({','.join(EVENT_COLUMNS)}) "
                    f"VALUES ({','.join('?' for _ in EVENT_COLUMNS)})",
                    [row[c] for c in EVENT_COLUMNS],
                )
                if cur.rowcount:
                    inserted += 1
                    accepted.append(row)
                else:
                    duplicates += 1
            for row in accepted:
                self._bump_session(db, row)
            db.commit()
        # Raw archive: append accepted records to per-session JSONL. The
        # archive, not SQLite, is the source of truth (index is rebuildable).
        with self._archive_lock:
            by_session = {}
            for row in accepted:
                by_session.setdefault(row["session_id"], []).append(row)
            for sid, rows in by_session.items():
                f = self.archive_dir / f"{sid}.jsonl"
                with f.open("a", encoding="utf-8") as fh:
                    for row in rows:
                        fh.write(json.dumps(
                            {k: v for k, v in row.items() if v is not None},
                            ensure_ascii=False) + "\n")
        return inserted, duplicates

    @staticmethod
    def _bump_session(db, row):
        ev = row["event"]
        db.execute(
            """INSERT INTO sessions (session_id, project, origin, first_ts, last_ts, events)
               VALUES (?,?,?,?,?,1)
               ON CONFLICT(session_id) DO UPDATE SET
                 events = events + 1,
                 project = COALESCE(sessions.project, excluded.project),
                 origin = COALESCE(sessions.origin, excluded.origin),
                 first_ts = MIN(sessions.first_ts, excluded.first_ts),
                 last_ts = MAX(sessions.last_ts, excluded.last_ts)""",
            (row["session_id"], row["project"], row["origin"], row["ts"], row["ts"]),
        )
        if ev == "prompt.submit":
            db.execute("UPDATE sessions SET prompts = prompts + 1 WHERE session_id = ?",
                       (row["session_id"],))
        elif ev == "tool.call":
            db.execute("UPDATE sessions SET tool_calls = tool_calls + 1 WHERE session_id = ?",
                       (row["session_id"],))
        elif ev == "tool.fail":
            db.execute("UPDATE sessions SET failures = failures + 1 WHERE session_id = ?",
                       (row["session_id"],))

    # ---- transcripts ----

    def store_transcript(self, session_id: str, data: bytes, project, origin):
        """Overwrite the snapshot (each supersedes the last, same as local
        semantics) and refresh the session's token accounting from it."""
        dst = self.transcripts_dir / f"{session_id}.jsonl"
        tmp = dst.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)
        stats = summarize_transcript(data)
        with self.connect() as db:
            db.execute(
                """INSERT INTO sessions (session_id, project, origin) VALUES (?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     project = COALESCE(sessions.project, excluded.project),
                     origin = COALESCE(sessions.origin, excluded.origin)""",
                (session_id, project, origin),
            )
            db.execute(
                """UPDATE sessions SET
                     transcript_bytes = ?, transcript_lines = ?, transcript_updated_at = ?,
                     input_tokens = ?, output_tokens = ?,
                     cache_read_tokens = ?, cache_creation_tokens = ?,
                     models = ?
                   WHERE session_id = ?""",
                (len(data), stats["lines"], utcnow(),
                 stats["input_tokens"], stats["output_tokens"],
                 stats["cache_read_tokens"], stats["cache_creation_tokens"],
                 stats["models"], session_id),
            )
            db.commit()
        return {"bytes": len(data), "lines": stats["lines"]}

    def transcript_path(self, session_id: str):
        p = self.transcripts_dir / f"{session_id}.jsonl"
        return p if p.is_file() else None

    # ---- index rebuild (archive is the source of truth) ----

    def rebuild_index(self):
        with self.connect() as db:
            db.execute("DELETE FROM events")
            db.execute("DELETE FROM sessions")
            db.commit()
            n = 0
            for f in sorted(self.archive_dir.glob("*.jsonl")):
                for line in f.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    row = normalize_event({c: rec.get(c) for c in EVENT_COLUMNS})
                    if not row["trace_id"]:
                        continue
                    cur = db.execute(
                        f"INSERT OR IGNORE INTO events ({','.join(EVENT_COLUMNS)}) "
                        f"VALUES ({','.join('?' for _ in EVENT_COLUMNS)})",
                        [row[c] for c in EVENT_COLUMNS],
                    )
                    if cur.rowcount:
                        self._bump_session(db, row)
                        n += 1
            db.commit()
        for f in sorted(self.transcripts_dir.glob("*.jsonl")):
            data = f.read_bytes()
            self.store_transcript(f.stem, data, None, None)
        return n


def summarize_transcript(data: bytes):
    """Extract token usage + models from a transcript snapshot. Understands
    the Claude Code JSONL format; other sources currently contribute only a
    line count (events still carry their model per record)."""
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_creation_tokens": 0}
    models = set()
    lines = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        lines += 1
        try:
            e = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        msg = e.get("message") or {}
        if not isinstance(msg, dict):
            continue
        if msg.get("model"):
            models.add(msg["model"])
        usage = msg.get("usage") or {}
        if isinstance(usage, dict):
            totals["input_tokens"] += usage.get("input_tokens") or 0
            totals["output_tokens"] += usage.get("output_tokens") or 0
            totals["cache_read_tokens"] += usage.get("cache_read_input_tokens") or 0
            totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens") or 0
    return {**totals, "lines": lines, "models": ",".join(sorted(models)) or None}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- auth


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return "gtr_" + secrets.token_urlsafe(32)


def create_key(store: Store, name: str, role: str) -> str:
    token = new_token()
    with store.connect() as db:
        db.execute(
            "INSERT INTO keys (token_hash, name, role, created_at) VALUES (?,?,?,?)",
            (hash_token(token), name, role, utcnow()),
        )
        db.commit()
    return token


# ---- console users (username + password, PBKDF2 at rest) ----

MIN_PASSWORD_LEN = 8
SESSION_TTL_SECONDS = 7 * 86400


def hash_password(password: str, salt_hex: str = None):
    salt_hex = salt_hex or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt_hex), 600_000)
    return dk.hex(), salt_hex


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    candidate, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(candidate, password_hash)


def create_user(store: Store, username: str, password: str, role: str,
                must_change: bool):
    pw_hash, salt = hash_password(password)
    with store.connect() as db:
        db.execute(
            """INSERT INTO users (username, password_hash, salt, role,
                                  must_change_password, created_at)
               VALUES (?,?,?,?,?,?)""",
            (username, pw_hash, salt, role, 1 if must_change else 0, utcnow()),
        )
        db.commit()


def set_password(store: Store, user_id: int, password: str, must_change: bool):
    pw_hash, salt = hash_password(password)
    with store.connect() as db:
        db.execute(
            """UPDATE users SET password_hash = ?, salt = ?,
                                must_change_password = ? WHERE id = ?""",
            (pw_hash, salt, 1 if must_change else 0, user_id),
        )
        # a password change invalidates every open session of that user
        db.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
        db.commit()


# Brute-force throttle: after 5 straight failures a username locks for 60s.
_login_fails = {}
_login_lock = threading.Lock()


def login_throttled(username: str) -> bool:
    with _login_lock:
        fails, until = _login_fails.get(username, (0, 0))
        return fails >= 5 and time.monotonic() < until


def login_result(username: str, ok: bool):
    with _login_lock:
        if ok:
            _login_fails.pop(username, None)
        else:
            fails, _ = _login_fails.get(username, (0, 0))
            _login_fails[username] = (fails + 1, time.monotonic() + 60)


def open_web_session(store: Store, user_id: int) -> str:
    token = "gts_" + secrets.token_urlsafe(32)
    now = time.time()
    with store.connect() as db:
        db.execute(
            "INSERT INTO web_sessions (token_hash, user_id, created_at, expires_at) "
            "VALUES (?,?,?,?)",
            (hash_token(token), user_id, utcnow(),
             datetime.fromtimestamp(now + SESSION_TTL_SECONDS, timezone.utc).isoformat()),
        )
        db.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                   (utcnow(), user_id))
        db.commit()
    return token


def authenticate(store: Store, headers):
    """Resolve the bearer token to an identity.

    Returns {name, role, kind: 'key'|'user', ...}. Console sessions ('gts_')
    map to users; anything else is looked up as an API key."""
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):].strip()
    if not token:
        return None
    if token.startswith("gts_"):
        with store.connect() as db:
            row = db.execute(
                """SELECT u.id, u.username, u.role, u.must_change_password
                   FROM web_sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?
                     AND u.disabled_at IS NULL""",
                (hash_token(token), utcnow()),
            ).fetchone()
        if not row:
            return None
        return {"kind": "user", "user_id": row["id"], "name": row["username"],
                "role": row["role"],
                "must_change_password": bool(row["must_change_password"])}
    with store.connect() as db:
        row = db.execute(
            "SELECT id, name, role FROM keys WHERE token_hash = ? AND revoked_at IS NULL",
            (hash_token(token),),
        ).fetchone()
        if row:
            db.execute("UPDATE keys SET last_seen_at = ? WHERE id = ?",
                       (utcnow(), row["id"]))
            db.commit()
    if not row:
        return None
    return {"kind": "key", "name": row["name"], "role": row["role"],
            "must_change_password": False}


ROLE_RANK = {"agent": 0, "member": 1, "admin": 2}


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = f"gaide-trace/{VERSION}"
    store: Store = None          # set by serve()
    webui_dir: Path = None

    # -- plumbing --

    def log_message(self, fmt, *args):  # quieter default log line
        sys.stderr.write("%s [%s] %s\n" % (
            self.address_string(), datetime.now().strftime("%H:%M:%S"), fmt % args))

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status=status)

    def read_body(self, max_len: int):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > max_len:
            return None
        data = self.rfile.read(length)
        if self.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                data = gzip.decompress(data)
            except OSError:
                return None
            if len(data) > max_len:
                return None
        return data

    def require(self, min_role: str, allow_must_change=False):
        """Authenticate and enforce role. Returns identity dict or None (replied)."""
        ident = authenticate(self.store, self.headers)
        if not ident:
            self.send_error_json(401, "missing or invalid credentials")
            return None
        if ident["must_change_password"] and not allow_must_change:
            self.send_error_json(403, "password_change_required")
            return None
        if ROLE_RANK[ident["role"]] < ROLE_RANK[min_role]:
            self.send_error_json(403, f"requires role '{min_role}'")
            return None
        return ident

    # -- routing --

    def do_GET(self):
        try:
            self.route("GET")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            self.route("POST")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_PUT(self):
        try:
            self.route("PUT")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_DELETE(self):
        try:
            self.route("DELETE")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route(self, method):
        url = urlparse(self.path)
        path = url.path
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if path == "/healthz":
            return self.send_json({"ok": True, "version": VERSION})

        if path.startswith("/api/v1/"):
            return self.route_api(method, path, q)

        if method == "GET":
            return self.serve_static(path)
        self.send_error_json(404, "not found")

    def route_api(self, method, path, q):
        m = (method, path)

        if m == ("GET", "/api/v1/me"):
            ident = self.require("agent", allow_must_change=True)
            if ident:
                self.send_json({"name": ident["name"], "role": ident["role"],
                                "kind": ident["kind"],
                                "must_change_password": ident["must_change_password"],
                                "version": VERSION})
            return

        if m == ("POST", "/api/v1/auth/login"):
            return self.api_login()

        if m == ("POST", "/api/v1/auth/password"):
            return self.api_change_password()

        if m == ("POST", "/api/v1/auth/logout"):
            token = (self.headers.get("Authorization", "")
                     .removeprefix("Bearer ").strip())
            if token.startswith("gts_"):
                with self.store.connect() as db:
                    db.execute("DELETE FROM web_sessions WHERE token_hash = ?",
                               (hash_token(token),))
                    db.commit()
            return self.send_json({"ok": True})

        if m == ("GET", "/api/v1/users"):
            if self.require("admin"):
                with self.store.connect() as db:
                    rows = db.execute(
                        """SELECT id, username, role, must_change_password,
                                  created_at, last_login_at, disabled_at
                           FROM users ORDER BY created_at""").fetchall()
                self.send_json([dict(r) for r in rows])
            return

        if m == ("POST", "/api/v1/users"):
            return self.api_create_user()

        if method == "POST" and re.fullmatch(r"/api/v1/users/\d+/reset", path):
            return self.api_reset_user(int(path.split("/")[4]))

        if method == "DELETE" and re.fullmatch(r"/api/v1/users/\d+", path):
            return self.api_disable_user(int(path.rsplit("/", 1)[1]))

        if m == ("GET", "/api/v1/projects/manage"):
            if self.require("member"):
                self.send_json(self.api_projects_manage())
            return

        if m == ("POST", "/api/v1/projects"):
            return self.api_create_project()

        if method == "POST" and re.fullmatch(r"/api/v1/projects/\d+/rotate", path):
            return self.api_rotate_project(int(path.split("/")[4]))

        if method == "DELETE" and re.fullmatch(r"/api/v1/projects/\d+", path):
            return self.api_delete_project(int(path.rsplit("/", 1)[1]))

        if m == ("POST", "/api/v1/events"):
            return self.api_ingest_events()

        if method == "PUT" and path.startswith("/api/v1/transcripts/"):
            return self.api_put_transcript(path.split("/api/v1/transcripts/", 1)[1], q)

        if m == ("GET", "/api/v1/overview"):
            if self.require("member"):
                self.send_json(self.api_overview(q))
            return

        if m == ("GET", "/api/v1/projects"):
            if self.require("member"):
                with self.store.connect() as db:
                    rows = db.execute(
                        "SELECT DISTINCT project FROM sessions "
                        "WHERE project IS NOT NULL ORDER BY project").fetchall()
                self.send_json([r["project"] for r in rows])
            return

        if m == ("GET", "/api/v1/sessions"):
            if self.require("member"):
                self.send_json(self.api_sessions(q))
            return

        if method == "GET" and path.startswith("/api/v1/sessions/"):
            rest = path.split("/api/v1/sessions/", 1)[1]
            if rest.endswith("/transcript"):
                sid = rest[: -len("/transcript")]
                return self.api_get_transcript(sid)
            if self.require("member"):
                return self.api_session_detail(rest)
            return

        if m == ("GET", "/api/v1/export"):
            return self.api_export(q)

        if m == ("GET", "/api/v1/keys"):
            if self.require("admin"):
                with self.store.connect() as db:
                    rows = db.execute(
                        """SELECT k.id, k.name, k.role, k.created_at, k.last_seen_at,
                                  k.revoked_at,
                                  (SELECT COUNT(*) FROM sessions s WHERE s.origin = k.name)
                                  AS sessions
                           FROM keys k ORDER BY k.created_at DESC""").fetchall()
                self.send_json([dict(r) for r in rows])
            return

        if m == ("POST", "/api/v1/keys"):
            if not self.require("admin"):
                return
            body = self.read_body(64 * 1024)
            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return self.send_error_json(400, "invalid JSON")
            name = (payload.get("name") or "").strip()
            role = payload.get("role") or "member"
            if not name or role not in ROLE_RANK:
                return self.send_error_json(400, "need name and role in agent|member|admin")
            token = create_key(self.store, name, role)
            return self.send_json({"token": token, "name": name, "role": role}, 201)

        if method == "DELETE" and path.startswith("/api/v1/keys/"):
            if not self.require("admin"):
                return
            try:
                key_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.send_error_json(400, "bad key id")
            with self.store.connect() as db:
                db.execute("UPDATE keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                           (utcnow(), key_id))
                db.commit()
            return self.send_json({"ok": True})

        self.send_error_json(404, "unknown API route")

    # -- API implementations --

    def api_ingest_events(self):
        ident = self.require("agent")
        if not ident:
            return
        body = self.read_body(MAX_EVENTS_BODY)
        if body is None:
            return self.send_error_json(413, "missing or oversized body")
        try:
            records = json.loads(body)
        except json.JSONDecodeError:
            return self.send_error_json(400, "body must be a JSON array of event records")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return self.send_error_json(400, "body must be a JSON array of event records")
        inserted, duplicates = self.store.ingest_events(records, origin=ident["name"])
        self.send_json({"inserted": inserted, "duplicates": duplicates})

    def api_put_transcript(self, session_id, q):
        ident = self.require("agent")
        if not ident:
            return
        if not SESSION_ID_RE.match(session_id):
            return self.send_error_json(400, "bad session id")
        body = self.read_body(MAX_TRANSCRIPT_BODY)
        if body is None:
            return self.send_error_json(413, "missing or oversized body")
        result = self.store.store_transcript(
            session_id, body, project=q.get("project"), origin=ident["name"])
        self.send_json(result)

    def _json_body(self, max_len=64 * 1024):
        try:
            return json.loads(self.read_body(max_len) or b"{}")
        except json.JSONDecodeError:
            return None

    # -- console auth & user management --

    def api_login(self):
        payload = self._json_body()
        if payload is None:
            return self.send_error_json(400, "invalid JSON")
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        if not username or not password:
            return self.send_error_json(400, "need username and password")
        if login_throttled(username):
            return self.send_error_json(429, "too many attempts — wait a minute")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username = ? AND disabled_at IS NULL",
                (username,)).fetchone()
        ok = bool(row) and verify_password(password, row["password_hash"], row["salt"])
        login_result(username, ok)
        if not ok:
            return self.send_error_json(401, "wrong username or password")
        token = open_web_session(self.store, row["id"])
        self.send_json({"token": token, "name": row["username"], "role": row["role"],
                        "must_change_password": bool(row["must_change_password"]),
                        "version": VERSION})

    def api_change_password(self):
        ident = self.require("member", allow_must_change=True)
        if not ident:
            return
        if ident["kind"] != "user":
            return self.send_error_json(400, "API keys have no password")
        payload = self._json_body()
        if payload is None:
            return self.send_error_json(400, "invalid JSON")
        current = payload.get("current_password") or ""
        new = payload.get("new_password") or ""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?",
                             (ident["user_id"],)).fetchone()
        if not verify_password(current, row["password_hash"], row["salt"]):
            return self.send_error_json(401, "current password is wrong")
        if len(new) < MIN_PASSWORD_LEN:
            return self.send_error_json(400, f"new password needs >= {MIN_PASSWORD_LEN} chars")
        if new == current:
            return self.send_error_json(400, "new password must differ from the current one")
        set_password(self.store, ident["user_id"], new, must_change=False)
        token = open_web_session(self.store, ident["user_id"])  # old sessions were revoked
        self.send_json({"ok": True, "token": token})

    def api_create_user(self):
        if not self.require("admin"):
            return
        payload = self._json_body()
        if payload is None:
            return self.send_error_json(400, "invalid JSON")
        username = (payload.get("username") or "").strip().lower()
        role = payload.get("role") or "member"
        if not re.fullmatch(r"[a-z0-9._-]{2,40}", username) or role not in ("member", "admin"):
            return self.send_error_json(400, "need username [a-z0-9._-]{2,40} and role member|admin")
        temp_password = secrets.token_urlsafe(9)
        try:
            create_user(self.store, username, temp_password, role, must_change=True)
        except sqlite3.IntegrityError:
            return self.send_error_json(409, "username already exists")
        self.send_json({"username": username, "role": role,
                        "temp_password": temp_password}, 201)

    def api_reset_user(self, user_id: int):
        if not self.require("admin"):
            return
        with self.store.connect() as db:
            row = db.execute("SELECT id, username FROM users WHERE id = ?",
                             (user_id,)).fetchone()
        if not row:
            return self.send_error_json(404, "unknown user")
        temp_password = secrets.token_urlsafe(9)
        set_password(self.store, user_id, temp_password, must_change=True)
        with self.store.connect() as db:
            db.execute("UPDATE users SET disabled_at = NULL WHERE id = ?", (user_id,))
            db.commit()
        self.send_json({"username": row["username"], "temp_password": temp_password})

    def api_disable_user(self, user_id: int):
        ident = self.require("admin")
        if not ident:
            return
        if ident.get("user_id") == user_id:
            return self.send_error_json(400, "you cannot disable your own account")
        with self.store.connect() as db:
            active_admins = db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled_at IS NULL"
            ).fetchone()["n"]
            row = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                return self.send_error_json(404, "unknown user")
            if row["role"] == "admin" and active_admins <= 1:
                return self.send_error_json(400, "cannot disable the last admin")
            db.execute("UPDATE users SET disabled_at = ? WHERE id = ?",
                       (utcnow(), user_id))
            db.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
            db.commit()
        self.send_json({"ok": True})

    # -- registered projects (visual onboarding) --

    PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

    def api_projects_manage(self):
        with self.store.connect() as db:
            rows = db.execute(
                """SELECT p.id, p.name, p.created_at, p.created_by, p.agent_token,
                          k.revoked_at AS key_revoked,
                          (SELECT COUNT(*) FROM sessions s WHERE s.project = p.name) AS sessions,
                          (SELECT MAX(last_ts) FROM sessions s WHERE s.project = p.name) AS last_ts
                   FROM projects p LEFT JOIN keys k ON k.id = p.key_id
                   ORDER BY p.created_at DESC""").fetchall()
        return [dict(r) for r in rows]

    def api_create_project(self):
        ident = self.require("member")
        if not ident:
            return
        payload = self._json_body()
        if payload is None:
            return self.send_error_json(400, "invalid JSON")
        name = (payload.get("name") or "").strip()
        if not self.PROJECT_NAME_RE.match(name):
            return self.send_error_json(400, "project name: letters, digits, . _ - (max 64)")
        token = new_token()
        with self.store.connect() as db:
            try:
                cur = db.execute(
                    "INSERT INTO keys (token_hash, name, role, created_at) VALUES (?,?,?,?)",
                    (hash_token(token), f"project:{name}", "agent", utcnow()))
                db.execute(
                    """INSERT INTO projects (name, created_at, created_by, key_id, agent_token)
                       VALUES (?,?,?,?,?)""",
                    (name, utcnow(), ident["name"], cur.lastrowid, token))
                db.commit()
            except sqlite3.IntegrityError:
                return self.send_error_json(409, "project already registered")
        self.send_json({"name": name, "agent_token": token}, 201)

    def api_rotate_project(self, project_id: int):
        if not self.require("member"):
            return
        token = new_token()
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?",
                             (project_id,)).fetchone()
            if not row:
                return self.send_error_json(404, "unknown project")
            if row["key_id"]:
                db.execute("UPDATE keys SET revoked_at = ? WHERE id = ?",
                           (utcnow(), row["key_id"]))
            cur = db.execute(
                "INSERT INTO keys (token_hash, name, role, created_at) VALUES (?,?,?,?)",
                (hash_token(token), f"project:{row['name']}", "agent", utcnow()))
            db.execute("UPDATE projects SET key_id = ?, agent_token = ? WHERE id = ?",
                       (cur.lastrowid, token, project_id))
            db.commit()
        self.send_json({"name": row["name"], "agent_token": token})

    def api_delete_project(self, project_id: int):
        if not self.require("admin"):
            return
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?",
                             (project_id,)).fetchone()
            if not row:
                return self.send_error_json(404, "unknown project")
            if row["key_id"]:
                db.execute("UPDATE keys SET revoked_at = ? WHERE id = ?",
                           (utcnow(), row["key_id"]))
            db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            db.commit()
        # Registration + key are gone; already-ingested trace data is kept.
        self.send_json({"ok": True})

    @staticmethod
    def _filters(q, alias=""):
        """Build a WHERE fragment + params from project/from/to query params."""
        where, params = [], []
        col = (alias + ".") if alias else ""
        if q.get("project"):
            where.append(f"{col}project = ?")
            params.append(q["project"])
        if q.get("from"):
            where.append(f"{col}ts >= ?")
            params.append(q["from"])
        if q.get("to"):
            where.append(f"{col}ts < ?")
            params.append(q["to"])
        return (" WHERE " + " AND ".join(where)) if where else "", params

    def api_overview(self, q):
        where, params = self._filters(q)
        with self.store.connect() as db:
            totals = db.execute(
                f"""SELECT COUNT(DISTINCT session_id) AS sessions,
                           COUNT(*) AS events,
                           SUM(event = 'prompt.submit') AS prompts,
                           SUM(event = 'tool.call') AS tool_calls,
                           SUM(event = 'tool.fail') AS failures,
                           COUNT(DISTINCT project) AS projects,
                           COUNT(DISTINCT origin) AS origins
                    FROM events{where}""", params).fetchone()
            per_day = db.execute(
                f"""SELECT substr(ts, 1, 10) AS day,
                           SUM(event = 'prompt.submit') AS prompts,
                           SUM(event = 'tool.call') AS tool_calls
                    FROM events{where} GROUP BY day ORDER BY day""", params).fetchall()
            top_tools = db.execute(
                f"""SELECT tool_name AS tool, COUNT(*) AS n FROM events
                    {where + (' AND ' if where else ' WHERE ')}
                    event = 'tool.call' AND tool_name IS NOT NULL
                    GROUP BY tool_name ORDER BY n DESC LIMIT 12""", params).fetchall()
            # Token totals come from transcript snapshots (sessions table).
            swhere, sparams = [], []
            if q.get("project"):
                swhere.append("project = ?")
                sparams.append(q["project"])
            if q.get("from"):
                swhere.append("last_ts >= ?")
                sparams.append(q["from"])
            if q.get("to"):
                swhere.append("first_ts < ?")
                sparams.append(q["to"])
            sw = (" WHERE " + " AND ".join(swhere)) if swhere else ""
            tokens = db.execute(
                f"""SELECT SUM(input_tokens) AS input_tokens,
                           SUM(output_tokens) AS output_tokens,
                           SUM(cache_read_tokens) AS cache_read_tokens,
                           SUM(cache_creation_tokens) AS cache_creation_tokens
                    FROM sessions{sw}""", sparams).fetchone()
            projects = db.execute(
                f"""SELECT project, COUNT(*) AS sessions, SUM(prompts) AS prompts,
                           SUM(tool_calls) AS tool_calls, SUM(failures) AS failures,
                           MAX(last_ts) AS last_ts
                    FROM sessions{sw} GROUP BY project ORDER BY last_ts DESC""",
                sparams).fetchall()
            origins = db.execute(
                f"""SELECT origin, COUNT(*) AS sessions, SUM(prompts) AS prompts,
                           SUM(tool_calls) AS tool_calls, MAX(last_ts) AS last_ts
                    FROM sessions{sw} GROUP BY origin ORDER BY last_ts DESC""",
                sparams).fetchall()
        return {
            "totals": {**dict(totals), **dict(tokens)},
            "per_day": [dict(r) for r in per_day],
            "top_tools": [dict(r) for r in top_tools],
            "projects": [dict(r) for r in projects],
            "origins": [dict(r) for r in origins],
        }

    def api_sessions(self, q):
        where, params = [], []
        if q.get("project"):
            where.append("project = ?")
            params.append(q["project"])
        if q.get("origin"):
            where.append("origin = ?")
            params.append(q["origin"])
        if q.get("from"):
            where.append("last_ts >= ?")
            params.append(q["from"])
        if q.get("to"):
            where.append("first_ts < ?")
            params.append(q["to"])
        if q.get("q"):
            where.append("session_id LIKE ?")
            params.append(f"%{q['q']}%")
        w = (" WHERE " + " AND ".join(where)) if where else ""
        limit = min(int(q.get("limit") or 50), 500)
        offset = max(int(q.get("offset") or 0), 0)
        with self.store.connect() as db:
            total = db.execute(f"SELECT COUNT(*) AS n FROM sessions{w}", params).fetchone()["n"]
            rows = db.execute(
                f"""SELECT * FROM sessions{w}
                    ORDER BY last_ts DESC LIMIT ? OFFSET ?""",
                params + [limit, offset]).fetchall()
        return {"total": total, "sessions": [dict(r) for r in rows]}

    def api_session_detail(self, session_id):
        if not SESSION_ID_RE.match(session_id):
            return self.send_error_json(400, "bad session id")
        with self.store.connect() as db:
            sess = db.execute("SELECT * FROM sessions WHERE session_id = ?",
                              (session_id,)).fetchone()
            if not sess:
                return self.send_error_json(404, "unknown session")
            events = db.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY ts",
                (session_id,)).fetchall()
        self.send_json({
            "session": dict(sess),
            "has_transcript": self.store.transcript_path(session_id) is not None,
            "events": [
                {k: v for k, v in dict(e).items() if v is not None} for e in events
            ],
        })

    def api_get_transcript(self, session_id):
        if not self.require("member"):
            return
        if not SESSION_ID_RE.match(session_id):
            return self.send_error_json(400, "bad session id")
        p = self.store.transcript_path(session_id)
        if not p:
            return self.send_error_json(404, "no transcript snapshot for this session")
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{session_id}.jsonl"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def api_export(self, q):
        if not self.require("member"):
            return
        fmt = q.get("format") or "jsonl"
        if fmt not in ("jsonl", "csv"):
            return self.send_error_json(400, "format must be jsonl or csv")
        where, params = [], []
        if q.get("project"):
            where.append("project = ?")
            params.append(q["project"])
        if q.get("session"):
            where.append("session_id = ?")
            params.append(q["session"])
        if q.get("origin"):
            where.append("origin = ?")
            params.append(q["origin"])
        if q.get("from"):
            where.append("ts >= ?")
            params.append(q["from"])
        if q.get("to"):
            where.append("ts < ?")
            params.append(q["to"])
        if q.get("event"):
            where.append("event = ?")
            params.append(q["event"])
        w = (" WHERE " + " AND ".join(where)) if where else ""
        with self.store.connect() as db:
            rows = db.execute(
                f"SELECT * FROM events{w} ORDER BY ts", params).fetchall()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if fmt == "jsonl":
            out = "\n".join(
                json.dumps({k: v for k, v in dict(r).items() if v is not None},
                           ensure_ascii=False)
                for r in rows)
            body = (out + "\n").encode("utf-8") if out else b""
            ctype = "application/x-ndjson; charset=utf-8"
            fname = f"gaide-trace-events-{stamp}.jsonl"
        else:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=EVENT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow(dict(r))
            body = buf.getvalue().encode("utf-8")
            ctype = "text/csv; charset=utf-8"
            fname = f"gaide-trace-events-{stamp}.csv"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- static web console --

    STATIC_TYPES = {".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".svg": "image/svg+xml"}

    def serve_static(self, path):
        if path in ("/", "/index.html") or path.startswith("/#"):
            path = "/index.html"
        name = Path(path.lstrip("/")).name  # flat dir; defeats traversal
        f = self.webui_dir / name
        ext = f.suffix.lower()
        if not f.is_file() or ext not in self.STATIC_TYPES:
            return self.send_error_json(404, "not found")
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.STATIC_TYPES[ext])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------- CLI


def serve(args):
    data_dir = Path(args.data).resolve()
    store = Store(data_dir)
    Handler.store = store
    Handler.webui_dir = Path(__file__).resolve().parent / "webui"

    # First-run bootstrap: default console account. The console forces a
    # password change on the first login, so admin/admin never survives it.
    with store.connect() as db:
        has_user = db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not has_user:
        create_user(store, "admin", "admin", "admin", must_change=True)
        print("=" * 64, flush=True)
        print("  First run — sign in to the console with admin / admin.", flush=True)
        print("  You will be required to set a new password immediately.", flush=True)
        print("=" * 64, flush=True)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"GAIDE-Trace server v{VERSION}", flush=True)
    print(f"  data:    {data_dir}", flush=True)
    print(f"  console: http://{args.host}:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(description="GAIDE-Trace team server")
    parser.add_argument("--data", default=os.environ.get("GAIDE_TRACE_SERVER_DATA", "./data"))
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="run the server (default)")
    p_serve.add_argument("--host", default=os.environ.get("GAIDE_TRACE_SERVER_HOST", "0.0.0.0"))
    p_serve.add_argument("--port", type=int,
                         default=int(os.environ.get("GAIDE_TRACE_SERVER_PORT", "8321")))

    p_key = sub.add_parser("key", help="manage API keys")
    key_sub = p_key.add_subparsers(dest="key_cmd", required=True)
    p_create = key_sub.add_parser("create")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--role", choices=["agent", "member", "admin"], default="member")
    key_sub.add_parser("list")
    p_revoke = key_sub.add_parser("revoke")
    p_revoke.add_argument("id", type=int)

    p_user = sub.add_parser("user", help="manage console accounts")
    user_sub = p_user.add_subparsers(dest="user_cmd", required=True)
    user_sub.add_parser("list")
    p_ureset = user_sub.add_parser("reset", help="reset a password (lockout recovery)")
    p_ureset.add_argument("username")

    sub.add_parser("rebuild-index", help="rebuild SQLite index from the JSONL archive")

    args = parser.parse_args()
    if args.cmd in (None, "serve"):
        if args.cmd is None:  # bare invocation: behave like `serve` with defaults
            args.host = os.environ.get("GAIDE_TRACE_SERVER_HOST", "0.0.0.0")
            args.port = int(os.environ.get("GAIDE_TRACE_SERVER_PORT", "8321"))
        return serve(args)

    store = Store(Path(args.data).resolve())
    if args.cmd == "key":
        if args.key_cmd == "create":
            token = create_key(store, args.name, args.role)
            print(f"{args.name} ({args.role}): {token}")
        elif args.key_cmd == "list":
            with store.connect() as db:
                for r in db.execute("SELECT id, name, role, created_at, last_seen_at, revoked_at FROM keys"):
                    status = "revoked" if r["revoked_at"] else "active"
                    print(f"[{r['id']}] {r['name']:<20} {r['role']:<8} {status:<8} "
                          f"created {r['created_at']}  last seen {r['last_seen_at'] or '-'}")
        elif args.key_cmd == "revoke":
            with store.connect() as db:
                db.execute("UPDATE keys SET revoked_at = ? WHERE id = ?", (utcnow(), args.id))
                db.commit()
            print(f"key {args.id} revoked")
    elif args.cmd == "user":
        if args.user_cmd == "list":
            with store.connect() as db:
                for r in db.execute("SELECT * FROM users ORDER BY created_at"):
                    status = "disabled" if r["disabled_at"] else (
                        "must-change-pw" if r["must_change_password"] else "active")
                    print(f"[{r['id']}] {r['username']:<20} {r['role']:<8} {status:<15} "
                          f"last login {r['last_login_at'] or '-'}")
        elif args.user_cmd == "reset":
            with store.connect() as db:
                row = db.execute("SELECT id FROM users WHERE username = ?",
                                 (args.username,)).fetchone()
            if not row:
                sys.exit(f"error: no user '{args.username}'")
            temp = secrets.token_urlsafe(9)
            set_password(store, row["id"], temp, must_change=True)
            with store.connect() as db:
                db.execute("UPDATE users SET disabled_at = NULL WHERE id = ?",
                           (row["id"],))
                db.commit()
            print(f"temporary password for {args.username}: {temp}")
            print("(they must set a new one on the next login)")
    elif args.cmd == "rebuild-index":
        n = store.rebuild_index()
        print(f"index rebuilt: {n} events")


if __name__ == "__main__":
    main()
