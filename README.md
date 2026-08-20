# GAIDE-Trace

**A research-grade interaction ledger for AI-assisted development.**

GAIDE-Trace captures a complete, analyzable history of every interaction
between a developer and an AI coding agent (Claude Code) in a project:
prompts, model responses, tool calls, subagent activity, and full session
transcripts — stored as plain JSONL you can version, query and mine.

For teams, an optional **zero-dependency server** centralizes every member's
traces into one durable ledger with a web console (dashboard, session
timelines, exports, key management) — see
[Team server](#team-server-optional).

It is a **standalone, opt-in companion** to [GAIDE](https://github.com/jcarlos78/GAIDE)
(Governed AI Development Environment). You can connect it to a GAIDE project,
to any other Claude Code project, or not at all — the choice belongs to each
user, per project.

## Why

Governance needs evidence. GAIDE enforces *how* AI-assisted development should
happen; GAIDE-Trace records *what actually happened*, so you can audit
sessions, study human–AI collaboration patterns, and support empirical
research (this project was born to support a master's research program on
AI-assisted software engineering).

## How it works

GAIDE-Trace uses Claude Code's native **hooks** — the same deterministic
mechanism GAIDE uses for enforcement ("instructions degrade, mechanisms
don't"). One small Python script is registered for the relevant lifecycle
events and appends a normalized record to a local store:

```
your-project/
└── .gaide-trace/
    ├── events/       # one JSONL per session: prompts, tool calls, turn ends
    └── transcripts/  # full-fidelity Claude Code transcript snapshots
                      # (messages, tool I/O, per-turn token usage)
```

Captured events: `SessionStart`, `UserPromptSubmit`, `PostToolUse`,
`PostToolUseFailure`, `Stop`, `SubagentStart`, `SubagentStop`, `PreCompact`,
`SessionEnd`. On `Stop`/`SessionEnd` the live transcript is snapshotted into
the store, so the raw record survives Claude Code's retention cleanup.

Two layers, two purposes:

| Layer | Source | Best for |
|---|---|---|
| `events/` | hooks (this project) | timeline analysis, tool-usage patterns, prompt taxonomy |
| `transcripts/` | Claude Code transcript snapshot | full conversation reconstruction, token accounting per turn |

Optionally, enable Claude Code's native **OpenTelemetry** export for
quantitative metrics (cost, latency, tokens per request) — see
`config/otel.env.example`.

## Install (connect to a project)

```bash
git clone https://github.com/jcarlos78/GAIDE-Trace
cd GAIDE-Trace
./install.sh /path/to/your-project          # personal opt-in (settings.local.json)
./install.sh /path/to/your-project --project # shared: every contributor traces
```

Requirements: Python 3.9+ on PATH. No third-party dependencies for capture;
`pandas` only for the analysis toolkit.

Disconnect anytime (data is preserved):

```bash
./uninstall.sh /path/to/your-project
# or temporarily: export GAIDE_TRACE_DISABLE=1
```

## Team server (optional)

For a team, run the central ledger server — Python stdlib only, nothing to
install — and connect each project to it:

```bash
# on your server (VPS or container — see docs/SERVER.md for TLS & systemd):
python3 server/gaide_trace_server.py serve
# then sign in at http://<server>:8321/ with admin/admin
# (a new password is required on first login)
```

Everything else is visual: create team accounts on the **Users** page,
register a project on the **Projects** page, and click **install prompt** —
the console generates a copy-paste prompt that any team member gives to
Claude Code, which clones this repo and connects their project automatically.
The manual equivalent still works:

```bash
./install.sh /path/to/your-project --server https://trace.example.com --token gtr_...
python3 tools/backfill.py /path/to/your-project/.gaide-trace   # pre-existing history
```

Capture stays **local-first**: every event is written to the project's local
store before any network I/O, then shipped through an offline-safe outbox
(at-least-once, deduped server-side) — a down server can never lose data.
The server keeps a raw JSONL archive as the source of truth plus a SQLite
index, and serves a web console at `/` with a team dashboard, per-session
timelines, transcript downloads, filtered JSONL/CSV exports, and API key
management (roles: `agent` = ingest-only for hooks, `member`, `admin`).

Deployment (container with automatic TLS, or bare VPS with systemd + Caddy),
backups and the HTTP API are documented in [`docs/SERVER.md`](docs/SERVER.md).

## Analyze

```bash
pip install pandas
python3 analysis/load_trace.py /path/to/your-project/.gaide-trace
```

Or in a notebook:

```python
from load_trace import load_events, load_transcripts
events = load_events("your-project/.gaide-trace")   # tidy DataFrame of events
turns  = load_transcripts("your-project/.gaide-trace")  # messages + token usage
```

The event schema is documented in `schema/event.schema.json`.

## Privacy & ethics

- Obvious secrets (API keys, tokens, private keys) are redacted at capture time.
- Oversized payloads are truncated in the event layer (`GAIDE_TRACE_MAX_FIELD`).
- The store is **local and gitignored by default**; publishing or versioning
  the data is an explicit decision by the researcher.
- If sessions may contain personal data of third parties, treat the store as
  research data: apply your institution's ethics/consent requirements before
  sharing.

## Design notes

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design:
data model, why hooks + transcript snapshots (and not a proxy), how to extend
capture to other AI tools via an LLM gateway, and the analysis roadmap.

## License

MIT — same as GAIDE.
