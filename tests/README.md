# Tests

`pytest` runner, `unittest`-style tests, standard library only — the same constraint as the code under test (see `AGENTS.md`, Tech stack). Run them with `pytest -q`, or via `./init.sh`, which runs the whole bring-up.

## State of the suite

**Empty.** GAIDE-Trace shipped through v0.3 without automated tests; adopting the GAIDE harness ([ADR 0001](../docs/adr/0001-adopt-gaide-harness.md)) made that visible rather than fixing it. Tests are not being back-filled wholesale, for the same reason specs are not: a test written against existing code encodes what the code does, not what it should do.

The rule going forward (Constitution Principle 2): **any change that alters behavior lands with tests for that behavior.** The suite grows one spec at a time.

## What tests here must never do

- **Never use captured data as fixtures.** Real traces contain prompts, file contents, and third-party personal data. Fixtures are synthesized — a handful of hand-written hook payloads and JSONL records, committed as code.
- **Never require a network.** The server is `http.server`; bind it to `127.0.0.1` on an ephemeral port with a temp data dir, as `init.sh` does.
- **Never write into a real store.** `.gaide-trace/` of this repo is the maintainer's own research data, not a scratch directory.
