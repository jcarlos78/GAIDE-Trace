# GAIDE-Trace

**A research-grade interaction ledger for AI-assisted development.**

GAIDE-Trace captures a complete, analyzable history of every interaction
between a developer and an AI coding agent (Claude Code) in a project:
prompts, model responses, tool calls, subagent activity, and full session
transcripts — stored as plain JSONL you can version, query and mine.

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
