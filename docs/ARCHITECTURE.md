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

- v0.1 (this repo): hooks capture + transcript snapshots + pandas loaders.
- v0.2: session report generator (Markdown/HTML per session), anonymization
  pass for publishable datasets.
- v0.3: adapters for other tools via gateway logs; optional SQLite index
  built from the JSONL (JSONL stays the source of truth).
- Possible packaging as a Claude Code **plugin** for one-command install.
