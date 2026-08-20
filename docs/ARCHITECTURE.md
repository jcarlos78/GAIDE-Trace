# GAIDE-Trace — Architecture

## Goal

Capture the **complete history of human–AI interactions** in a software
project with enough fidelity for post-hoc research analysis (pattern mining,
session reconstruction, cost/effort accounting), while remaining:

1. **Optional** — a standalone project any user connects (or not) per project.
2. **Non-intrusive** — capture never blocks or alters the agent's behavior.
3. **Analyzable** — plain JSONL, stable schema, pandas-ready loaders.
4. **Honest about privacy** — redaction at capture, local-first storage.

## Design decisions

### D1 — Capture via Claude Code hooks, not a network proxy

Three candidate capture points exist:

| Approach | Fidelity | Effort | Fragility |
|---|---|---|---|
| **Hooks (chosen)** | prompts, tools, turns, transcript path | low | low — official, versioned API |
| API proxy (MITM / gateway) | raw HTTP requests | high | high — breaks with auth/streaming changes |
| Manual export | whatever the user remembers to save | none | total |

Hooks are the same mechanism GAIDE already trusts for enforcement, they are
deterministic ("instructions degrade, mechanisms don't"), and they hand us
`transcript_path` — which unlocks the highest-fidelity source for free (D2).

### D2 — Two-layer store: event log + transcript snapshots

Claude Code already writes a complete transcript of every session
(`~/.claude/projects/<slug>/<session>.jsonl`): all messages, tool inputs and
outputs, and **per-turn token usage**. But it lives outside the project and is
deleted by retention cleanup (default ~30 days).

So GAIDE-Trace stores two layers under `<project>/.gaide-trace/`:

- **`events/<session_id>.jsonl`** — one normalized record per hook event.
  Compact, redacted, truncated. This is the *research-friendly* layer: a flat
  timeline that loads straight into a DataFrame.
- **`transcripts/<session_id>.jsonl`** — a byte-for-byte snapshot of the live
  transcript, refreshed at every `Stop` and at `SessionEnd`. This is the
  *archival* layer: full fidelity, survives retention cleanup, and lets any
  future analysis re-derive what the event layer truncated.

Snapshotting on every `Stop` (not only `SessionEnd`) matters: `SessionEnd`
does not fire if the process crashes; `Stop` fires at the end of every turn,
so the snapshot is at most one turn stale.

### D3 — Opt-in connection via `settings.local.json`

`install.sh` registers the hooks in the target project's
`.claude/settings.local.json` (personal, gitignored). Connecting is therefore
a per-user, per-project choice — GAIDE itself stays trace-free by default.
Teams that want tracing as policy use `--project` to write to the shared
`.claude/settings.json` instead.

The store is gitignored in the target repo by default. Researchers who want
versioned data should make `.gaide-trace/` its own git repository (or symlink
it into a dedicated data repo) — this keeps interaction data out of the code
repo's history and lets it have its own access control.

### D4 — Capture must never break the session

`trace_hook.py` always exits 0, writes nothing to stdout (some hooks inject
stdout into model context), and swallows all internal errors. A logging bug
must cost data, never a working session. Timeout is capped at 15s in the hook
registration.

### D5 — Redact at capture, not at analysis

Secrets (API keys, tokens, private keys) are redacted before anything touches
disk in the event layer. Rationale: a research dataset that is safe by
construction is worth more than one that requires a cleaning pass nobody
audits. Note the transcript snapshot layer is raw by design — it inherits
whatever protections the project's own hooks (e.g. GAIDE's `block-secrets.sh`)
already enforce upstream.

## Data model

See `schema/event.schema.json`. Key identifiers for analysis:

- `session_id` — one CLI session; joins events ↔ transcript snapshot.
- `prompt_id` — one user turn; groups the prompt, its tool calls and the
  final assistant message.
- `agent_id` / `agent_type` — distinguishes main-loop activity from
  subagents (GAIDE's code-reviewer, security-reviewer, ...).

Typical research queries this supports directly:

- Tool-usage distribution per session / per task type.
- Prompt → number of tool calls → outcome chains (via `prompt_id`).
- Rework signals: `PostToolUseFailure` density, `PreCompact` frequency.
- Token/cost accounting per turn (transcript layer `usage` fields).
- Governance events: how often GAIDE hooks blocked actions (visible as
  failed/denied tool patterns).

## Team server (optional centralization layer)

`server/gaide_trace_server.py` extends the same design to a team: every
member's hooks ship each record to one central ledger, without weakening any
local guarantee.

### D6 — Local-first shipping with an outbox

The local store is written **before** any network I/O, unconditionally; the
server is an additional destination, never a dependency (D4 still holds: a
down server costs nothing). Each record is then queued as one file in
`.gaide-trace/outbox/` and shipped in batches; failures leave the queue
intact and later hook fires retry. Delivery is at-least-once and the server
dedupes by `trace_id`, so retries are safe by construction. File-per-record
spooling makes concurrent hook processes safe without locks.
`tools/backfill.py` replays a whole local store through the same idempotent
API — adopting the server late, or recovering a server from developer
machines, is the same one command.

### D7 — Server storage mirrors the two-layer model; JSONL stays the truth

The server persists three things: a raw append-only JSONL archive (one file
per session — the source of truth), the transcript snapshots (each upload
supersedes the last, same semantics as local), and a SQLite (WAL) index for
queries and aggregates. The index is derived data: `rebuild-index`
reconstructs it from the archive, and file-level backups of the data
directory are sufficient and consistent.

### D8 — Zero dependencies on the server too

The server is Python stdlib only (`http.server`, `sqlite3`), one process,
one data directory. Rationale: a research tool should be auditable and
deployable anywhere Python exists — a VPS with systemd, a container, a lab
machine — with no supply chain to review. TLS is delegated to a standard
reverse proxy (Caddy/nginx). Auth is bearer keys (SHA-256 hashes at rest)
with three roles; hooks get ingest-only `agent` keys, so a leaked hook key
can add data but never read it.

The web console (`server/webui/`) serves the management needs — team
dashboard, session timeline reconstruction, filtered JSONL/CSV export, key
management — while `GET /api/v1/export` keeps the data pipeline scriptable
(same schema as the local store, plus `origin` and `received_at`).

See `docs/SERVER.md` for deployment (VPS / container), backups and the API.

## Quantitative telemetry (optional third layer)

Claude Code exports OpenTelemetry metrics natively
(`CLAUDE_CODE_ENABLE_TELEMETRY=1` — see `config/otel.env.example`): tokens,
cost, session counts, per-model breakdowns. For a single-researcher setup the
transcript layer already contains token usage, so OTel is optional; it becomes
valuable when tracing **multiple users/machines** into one collector.

## Extending beyond Claude Code

The event schema is tool-agnostic on purpose (`event`, `session_id`,
`prompt`, `tool_name`, ...). Two extension paths:

1. **Other agentic CLIs with hook systems** — write an adapter that maps
   their events into the same schema and appends to the same store.
2. **Chat/completions traffic from arbitrary tools** (Cursor, ChatGPT
   wrappers, custom scripts) — route them through an **LLM gateway**
   (e.g. LiteLLM proxy) with logging callbacks, and convert its logs into
   GAIDE-Trace records. This is deliberately out of scope for v0.1: it
   requires per-tool configuration and TLS/auth handling, and the master's
   research corpus is Claude Code-centric. The schema will not need to change.

## Roadmap

- v0.1: hooks capture + transcript snapshots + pandas loaders.
- v0.2 (this repo): team server — central ledger with outbox shipping,
  web console, exports, key management (D6–D8); SQLite index built from the
  JSONL (JSONL stays the source of truth).
- v0.3: session report generator (Markdown/HTML per session), anonymization
  pass for publishable datasets; adapters for other tools via gateway logs.
- Possible packaging as a Claude Code **plugin** for one-command install.
