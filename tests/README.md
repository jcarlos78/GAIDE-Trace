# Tests

`unittest`-style tests, standard library only — the same constraint as the code under test (see `AGENTS.md`, Tech stack). Run them with `python3 -m unittest discover -s tests` or `pytest -q`, or via `./init.sh`, which runs the whole bring-up and uses pytest only when it is installed.

`test_price_import.py` is the one exception to "Python only": the price-import logic ships as browser JS (`server/webui/price-import.js`), so the test drives that exact file through `node`. Node is an optional test-time tool, never a runtime dependency. Without it those tests skip with a reason and `./init.sh` prints a warning.

Shared, synthesized fixtures live in `tests/fixtures.py` and are imported as a top-level module, so run tests through discovery or pytest rather than as `tests.<module>`.

## State of the suite

**Growing one spec at a time.** GAIDE-Trace shipped through v0.3 without automated tests; adopting the GAIDE harness ([ADR 0001](../docs/adr/0001-adopt-gaide-harness.md)) made that visible rather than fixing it. The first tests arrived with [`specs/model-usage`](../specs/model-usage/spec.md). Tests are not being back-filled wholesale, for the same reason specs are not: a test written against existing code encodes what the code does, not what it should do.

The rule going forward (Constitution Principle 2): **any change that alters behavior lands with tests for that behavior.** The suite grows one spec at a time.

## What tests here must never do

- **Never use captured data as fixtures.** Real traces contain prompts, file contents, and third-party personal data. Fixtures are synthesized — a handful of hand-written hook payloads and JSONL records, committed as code.
- **Never require a network.** The server is `http.server`; bind it to `127.0.0.1` on an ephemeral port with a temp data dir, as `init.sh` does.
- **Never write into a real store.** `.gaide-trace/` of this repo is the maintainer's own research data, not a scratch directory.
