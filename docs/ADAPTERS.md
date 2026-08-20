# Adapters — capturing any LLM coding tool

GAIDE-Trace is tool-agnostic at the **record** level, not at the capture
level: every IDE/agent persists (or exposes) sessions differently, so each
tool gets one small adapter whose only job is translation into the canonical
event vocabulary. Everything downstream — the local store, the outbox
shipping, the team server, the console, the analyses — is shared.

## Canonical vocabulary

| `event` | Meaning | Payload fields typically present |
|---|---|---|
| `session.start` | a session/conversation began | `cwd` |
| `prompt.submit` | the user sent a message | `prompt` |
| `model.turn` | one LLM generation finished | `model`, `last_assistant_message`, proposed `tool_name`/`tool_use_id` |
| `tool.call` | a tool executed successfully | `tool_name`, `tool_use_id`, `tool_input`, `tool_response` |
| `tool.fail` | a tool (or tool call) failed | same as `tool.call` |
| `turn.end` | the assistant finished answering a prompt | `last_assistant_message` |
| `agent.start` / `agent.end` | a subagent was spawned / finished | `agent_id`, `agent_type` |
| `context.compact` | context was compacted / re-injected | — |
| `session.end` | the session ended | `transcript_path` |
| `note` | tool-specific event with no canonical equivalent | `native_event` says what it was |

Every record also carries `source` (adapter id, e.g. `claude-code`,
`antigravity`), `native_event` (the tool's own name for what happened — never
throw information away) and the common envelope (`trace_id`, `ts`,
`session_id`, `project`, `cwd`, `model` when known). Full schema:
[`schema/event.schema.json`](../schema/event.schema.json).

Granularity differs by tool and that is fine: Claude Code emits `turn.end`
(one per user turn) but no `model.turn`; Antigravity emits `model.turn` (one
per generation) but has no explicit end-of-turn marker. Analyses should key
on the events they need and treat absent kinds as "not observable in that
tool", not as zero.

## Existing adapters

### Claude Code — `hooks/trace_hook.py` (real-time)

Registered by `install.sh` for the native hook events; each fire maps
1:1 to a canonical record:

`SessionStart→session.start`, `UserPromptSubmit→prompt.submit`,
`PostToolUse→tool.call`, `PostToolUseFailure→tool.fail`, `Stop→turn.end`,
`SubagentStart→agent.start`, `SubagentStop→agent.end`,
`PreCompact→context.compact`, `SessionEnd→session.end`.

On `turn.end`/`session.end` the live transcript is snapshotted into
`transcripts/` (highest-fidelity layer, includes per-turn token usage).

### Antigravity IDE — `tools/import_antigravity.py` (importer)

Antigravity has no hooks, but persists every conversation as a SQLite
database of protobuf-encoded steps in
`~/.gemini/antigravity-ide/conversations/<uuid>.db`. The importer tails
those databases (schemaless wire-format decoding — no protobuf dependency,
no official schema) and maps steps to events:

| Antigravity step | Canonical |
|---|---|
| type 14 (user message) | `prompt.submit` |
| type 15 (model generation) | `model.turn` (+ model id from `gen_metadata`) |
| tool-execution steps (5, 8, 9, 21, 31, 85, 132, … — any step carrying a tool call) | `tool.call` / `tool.fail` |
| type 17 (invalid tool call) | `tool.fail` |
| types 98/99 (context injection) | `context.compact` |
| types 23/101 (task update, hook notice) | `note` |
| undecodable / unknown | `note` (never dropped) |

Properties worth knowing:

- **Incremental & idempotent.** Per-store state remembers the last imported
  step per conversation; only finished steps advance it, and `trace_id` is
  deterministic per (conversation, step), so re-imports — even from two
  machines — dedupe server-side.
- **Workspace-routed.** Each conversation records its workspace folder; the
  importer writes into that project's own `.gaide-trace/` (only if the
  project opted in, unless `--create-store`), and ships through the same
  outbox if the store is connected to a team server.
- **Fragile by nature, safe by construction.** The format is reverse
  engineered; an Antigravity update may change it. Decoding failures degrade
  to `note` records rather than losing the step, and the raw databases stay
  untouched on disk for re-import after the adapter is fixed.
- Run it periodically (`cron`, `launchd`) or leave `--watch 60` running.

## Writing a new adapter

1. **Find the tool's session record.** Preference order: real-time hooks >
   a local store you can tail (SQLite/JSONL/protobuf) > an export command.
   (A gateway/proxy in front of the LLM API is a last resort: it sees
   requests, but not IDE-side events like tool approvals.)
2. **Translate to the vocabulary above.** Unmappable events become `note`
   with an honest `native_event`. Put the tool's name in `source`.
3. **Reuse the core.** `hooks/trace_hook.py` doubles as a library:
   `clean()` (redact + truncate), `spool()`/`flush_outbox()` (offline-safe
   shipping), `load_config()`/`server_config()`. Import it the way
   `tools/import_antigravity.py` does. Stdlib only — that is a project
   invariant (D8).
4. **Guarantee the invariants.** Local store written before any network I/O
   (D4/D6); never break or slow the user's session; deterministic
   `trace_id`s if your adapter can re-see the same event twice.
5. **Snapshot transcripts if the tool has them** (`transcripts/<session_id>`,
   overwrite semantics). Skip otherwise — events alone are already useful.

## Deferred adapters (decision, Aug 2026)

Cursor and Codex CLI adapters were considered for v0.3 and deliberately
deferred to v0.4. The v0.3 scope decision was: neutral core + canonical
vocabulary + the two adapters actually in use (Claude Code, Antigravity),
because each additional importer is another reverse-engineered local format
to maintain (Cursor: `state.vscdb` / workspaceStorage; Codex:
`~/.codex/sessions`). The adapter model above is the extension point — both
tools persist sessions locally, so they fit the same importer pattern the
Antigravity adapter established, with no changes needed to the store,
server, console or schema.
