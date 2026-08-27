# Project documentation

```
docs/
├── ARCHITECTURE.md   Design decisions D1–D8: why hooks, the two-layer store,
│                     local-first shipping, JSONL as the truth
├── ADAPTERS.md       The canonical event vocabulary and how to write an
│                     adapter for another AI coding tool
├── SERVER.md         Team server: deployment, HTTP API, backups, key roles
├── mcp-setup.md      Connecting the MCP servers of .mcp.json in each tool
└── adr/              Architecture Decision Records (use the adr-writer skill)
```

## ADRs

ADRs live in `adr/` as `NNNN-title-in-kebab-case.md`. They are immutable once accepted — a decision changes through a new ADR, never by editing an old one. To create one, use the `adr-writer` skill (`agents/skills/adr-writer.md`).

- [0001 — Develop GAIDE-Trace under the GAIDE harness](adr/0001-adopt-gaide-harness.md)

`ARCHITECTURE.md` predates the ADR practice and holds the project's founding decisions as `D1`–`D8`. It stays the reference for those; new architectural decisions become ADRs.

## Conventions

- Docs and ADRs in English; standard Markdown, no proprietary extensions.
- A design decision that constrains future work belongs in an ADR, not in a README paragraph.
