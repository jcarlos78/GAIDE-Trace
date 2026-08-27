# Specs — Spec-Driven Development

This folder is the **source of truth** for GAIDE-Trace's behavior. Code serves specs; specs do not document code.

## Structure

```
specs/
├── constitution.md         Non-negotiable principles (do not edit without an ADR)
├── README.md               This file
├── template/               Templates for a new feature
│   ├── spec.md
│   ├── plan.md
│   └── tasks.md
└── <feature-slug>/         One folder per feature
    ├── spec.md
    ├── plan.md
    └── tasks.md
```

## Pre-GAIDE code has no specs — on purpose

GAIDE-Trace existed before it adopted this harness ([ADR 0001](../docs/adr/0001-adopt-gaide-harness.md)). Nothing shipped through v0.3 — capture adapters, event schema, team server, web console — has a retroactive spec, and none will be back-filled wholesale: a spec written from the code it is supposed to constrain documents the implementation instead of the intent.

The rule going forward: **the first behavioral change to a component writes the spec for the behavior it touches.** Scope the spec to that behavior, not to the whole component. Until then, the standing description of intent lives in `docs/ARCHITECTURE.md` (decisions D1–D8), `docs/ADAPTERS.md` (the canonical event vocabulary) and the invariants section of `AGENTS.md`.

## SDD flow

```
   ┌──────┐    ┌──────┐    ┌───────┐    ┌──────────────────┐
   │ Spec │ →  │ Plan │ →  │ Tasks │ →  │  Implementation  │
   └──────┘    └──────┘    └───────┘    └──────────────────┘
      ↑           ↑            ↑                ↑
      └──── human and agent collaborate at each step ────┘
```

Each step has a **human gate**: no one — not even the agent — skips a step without explicit human approval.

## How to start a new feature

### Option 1 — Using the spec-writer skill (recommended)
```
/spec-writer I want to add: <natural-language description>
```
The skill drives the full flow.

### Option 2 — Manually
1. `cp -r template/ <new-feature-slug>/`
2. Edit `spec.md` (fill in the marked sections)
3. Request human review
4. Edit `plan.md` after the spec is approved
5. Edit `tasks.md` after the plan is approved
6. Implement task by task

## What a GAIDE-Trace spec must cover

Beyond the template's own sections, a spec here is incomplete without:

- **Effect on captured data** — new or changed event fields, and whether stores already in the field stay readable. Captured data outlives every version of this code; backward-incompatible schema changes need a migration path and an ADR.
- **Which invariant it touches** — D4 (capture never breaks the session), D5 (redact at capture), D6 (local-first shipping), D7 (JSONL is the truth), or the stdlib-only rule. A spec that silently breaks one of these is rejected, not amended.
- **Privacy and ethics impact** — anything that widens what is captured, or where it is sent, is a research-data decision before it is a technical one.
