# Plan: Model usage — per-session model and per-model statistics

> **Prerequisite:** `spec.md` approved (2026-09-14).

## Architectural decisions

- **D-a — One index row per model turn (`model_turns`), aggregated at query
  time.** Columns: `session_id`, `turn_key` (transcript `message.id`, else the
  event `trace_id`, else `line:<n>`), `model`, `source` (`transcript` |
  `events`), `ts`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_write_5m_tokens`, `cache_write_1h_tokens` (all NULL for event-derived
  turns), `premium` (0/1, `usage.speed` present and ≠ `standard`).
  PK `(session_id, turn_key)`; indexes on `(ts)` and `(model, ts)`.
  *Why per turn, not per session×model:* AC12 filters by turn time, so a
  pre-aggregated row cannot answer a window that cuts a session in half. Row
  count is bounded by assistant messages (low thousands per session), well
  within SQLite's comfort zone. — minor decision (derived index table, D7
  unchanged).
- **D-b — Denormalized `dominant_model` + `model_count` on `sessions`**,
  recomputed whenever a session's `model_turns` change. Keeps the Sessions list
  query a plain `SELECT` with pagination; the `model` filter uses
  `EXISTS (SELECT 1 FROM model_turns …)`. — minor decision.
- **D-c — Source rule enforced in one function**, `refresh_session_models(db,
  session_id, transcript_bytes | None)`: delete the session's rows; insert
  transcript turns; if none, insert turns from the session's `model.turn`
  events in the `events` table; recompute D-b. Live event ingest of a
  `model.turn` inserts its row directly only if the session has no
  `source='transcript'` rows (idempotence comes from the existing `trace_id`
  dedup — duplicates never reach this path, AC7).
- **D-d — Derived-index versioning with automatic re-derivation on start.** A
  `meta(key, value)` table holds `derived_version`. On `serve` start, if it is
  below the code's `DERIVED_VERSION`, every stored transcript is re-indexed (no
  file writes) and `model_turns` rebuilt, then the version is stored. Logged
  with counts and duration (Principle 8). Commit 1 introduces the mechanism at
  version 1 (corrected token totals); commit 2 bumps it to 2 (model turns).
  *Why not require `rebuild-index`:* spec use case 8 — no operator step.
  — **ADR not required** in my judgement: it is an operational mechanism inside
  the existing D7 contract (the index is derived and re-derivable). I will add a
  paragraph to `docs/ARCHITECTURE.md` D7 instead. Flagging so you can overrule.
- **D-e — Split `store_transcript` into write + `index_transcript`.** Today
  `rebuild_index` re-calls `store_transcript`, which rewrites each transcript
  file. Re-derivation must not touch files (AC24), so indexing gets its own
  entry point used by upload, rebuild and migration alike.
- **D-f — `model_prices` table** (`model` PK, `input`, `output`, `cache_read`,
  `cache_write_5m`, `cache_write_1h` in USD per MTok, `updated_at`,
  `updated_by`). Configuration like `users`/`keys`: `rebuild-index` does not
  clear it (AC23). All five prices required on set (0 is valid); ceiling
  10,000 USD/MTok (AC28).
- **D-g — Cost computed at query time in Python** from aggregated tokens and the
  current price table, never stored — prices change, derived cost must not go
  stale. Returned unrounded (AC15).
- **D-h — Limits.** Model names truncated to 128 characters when attributed
  (AC30). Placeholder set: `{"<synthetic>"}` — a named constant, extended only
  deliberately. Chart: top 4 models by turns + "other" (AC14).
- **D-i — API shape.**
  - `GET /api/v1/sessions` — new `model` filter; rows gain `dominant_model`,
    `model_count`. The legacy `models` string column stays populated for
    compatibility with existing API clients.
  - `GET /api/v1/sessions/<id>` — gains `models: [{model, source, turns,
    input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens,
    cache_write_1h_tokens, premium_turns, cost, cost_status}]` where
    `cost_status` ∈ `priced | unpriced | no_tokens`.
  - `GET /api/v1/overview` — gains `models: {rows: [...same shape + sessions,
    share], per_day: [{day, model, turns}], cost_total, unpriced_models,
    no_token_models, premium_turns}`.
  - `GET /api/v1/models` (member) — every model seen ∪ every priced model, with
    total turns and its price entry or `null`.
  - `PUT /api/v1/models/prices` (admin) — JSON `{model, input, output,
    cache_read, cache_write_5m, cache_write_1h}`. Model name in the body, not
    the path: real names contain `/`, `:` and `@`.
  - `DELETE /api/v1/models/prices?model=<name>` (admin).
- **D-j — Chart palette.** The console has two validated series colours; this
  chart needs five slots (4 + "other"). Extend with Carbon categorical colours
  and re-validate on `#262626` with the dataviz skill's validator before use;
  "other" uses a neutral grey.

## Affected components

- `server/gaide_trace_server.py` — transcript summarizer (per-message dedup,
  placeholder filter, per-turn extraction), schema (`meta`, `model_turns`,
  `model_prices`, `sessions.dominant_model/model_count`), `index_transcript`,
  `refresh_session_models`, startup re-derivation, `rebuild_index`, API
  endpoints above.
- `server/webui/app.js` — Sessions model column + filter; session page per-model
  table; Overview Models card (table, share bar, per-day stacked bars with table
  toggle); admin "Model prices" view.
- `server/webui/index.html` — nav entry "Model prices" (admin only).
- `server/webui/style.css` — series tokens `--series-3…5`, `--series-other`,
  share-bar styles.
- `tests/` — new, stdlib `unittest` (see Testing strategy).
- `docs/SERVER.md` — API table, cost-estimate section, upgrade note about
  corrected token totals.
- `docs/ARCHITECTURE.md` — D7: derived-index versioning paragraph.

Not touched: `hooks/`, `tools/`, `schema/`, `analysis/`, `install.sh`.

## Implementation sequence

Two commits (Principle 7), each red → green:

**Commit 1 — Fix transcript token double-counting**
1. Tests for AC1–AC3 against the summarizer and against session totals after an
   upload through the HTTP API (red).
2. Per-message dedup + placeholder filter in the summarizer.
3. D-e split (`index_transcript`), `meta` table, `DERIVED_VERSION = 1`,
   startup re-derivation; tests for "existing index gets corrected totals, files
   byte-identical" (part of AC24).
4. Green; `docs/SERVER.md` upgrade note.

**Commit 2 — Model usage and cost estimates**
5. Tests for AC4–AC9, AC11–AC12, AC15–AC17, AC19, AC21–AC28, AC30 (red).
6. `model_turns` table + per-turn extraction + `refresh_session_models` +
   ingest hook for `model.turn`; D-b columns; version bump to 2.
7. `model_prices` + `/api/v1/models` endpoints with authz and validation.
8. Session list/detail and overview API extensions with cost computation.
9. Console: Sessions column + filter (AC10); session page breakdown.
10. Console: Overview Models card + chart (AC13, AC14, AC18, AC20); palette
    validation.
11. Console: admin Model prices view (AC21, AC22).
12. Green; docs (`SERVER.md`, `ARCHITECTURE.md`).
13. `verifier` against a local server seeded with synthesized data (console
    ACs incl. AC29); `code-reviewer` and `security-reviewer` on the diff.

Version bump / release notes are left to you after merge (v0.4.0 suggested:
new API fields and a visible change to token totals).

## Testing strategy

- **Unit (`tests/test_transcript_usage.py`)** — summarizer/extractor on
  synthesized transcript bytes: duplicated `message.id` lines, id-less
  messages, `<synthetic>`, mixed models, 5m/1h cache split present and absent,
  `speed: fast`, malformed lines, over-long model names.
- **Store-level (`tests/test_model_usage_store.py`)** — `Store` on a temp dir:
  source rule (AC5, AC6), re-upload replaces (AC7), `rebuild-index` parity
  (AC25), startup re-derivation of a v0.3.1-shaped index with byte-identical
  files (AC24), prices survive rebuild (AC23).
- **HTTP (`tests/test_model_usage_api.py`)** — real server on `127.0.0.1`,
  ephemeral port, temp data dir (same pattern as `init.sh`): list/detail/
  overview shapes and numbers, window by turn time (AC12), cost maths and
  statuses (AC15–AC17, AC19), `model` filter (AC9), authz matrix for member
  user / member key / agent key / admin (AC26, AC27), price validation (AC28).
- **Console (verifier + Playwright)** — AC10, AC13, AC14, AC18, AC20, AC21,
  AC22 and AC29 (a synthesized event with model `<img src=x onerror=…>`
  renders as text, no dialog/console error).
- Fixtures are synthesized in the test files; nothing is copied from real
  stores.
- Runner: tests are `unittest` and run with `python3 -m unittest discover
  tests` as well as `pytest -q`. **Note:** `init.sh` only runs tests when
  `pytest` is installed, and it is not installed on this machine — so today
  bring-up would report "OK" without running them. Proposed separate one-line
  fix: fall back to `python3 -m unittest discover tests` (its own commit, your
  call).

## Implementation risks

- Risk: startup re-derivation over many/large transcripts delays binding the
  port past a container health-check | Mitigation: stream-parse line by line,
  log progress; measure on a synthesized 50 MB transcript. If too slow, run it
  after binding with API responses flagging `derived: "rebuilding"` — would be
  raised with you before switching.
- Risk: live ingest and upload race on the same session (events thread vs
  transcript PUT) | Mitigation: each path's delete-and-insert runs inside one
  SQLite transaction; the source rule is re-evaluated inside it.
- Risk: overview query cost with a window over `model_turns` joined to
  `sessions` for the project filter | Mitigation: `(ts)` index; check
  `EXPLAIN QUERY PLAN` on a synthesized 200k-turn index.
- Risk: palette validation fails for five slots on `#262626` | Mitigation: drop
  to top 3 + other rather than ship an inaccessible chart.

## Sprint contract

- [ ] Uploading a transcript whose assistant messages span several lines each
  yields session tokens counted once per message — verify by:
  `python3 -m unittest discover -s tests -p 'test_transcript_usage.py' -v`
  (tests import their shared `fixtures` module, so they run via discovery
  or pytest, not as `tests.<module>`); and on a local server,
  `GET /api/v1/sessions/<id>` totals equal a hand-computed per-`message.id` sum.
- [ ] An index built by v0.3.1 is corrected and gains model usage on first
  start of the new server, archive/transcript files unchanged — verify by:
  store test comparing SHA-256 of every file before/after; server log shows
  the re-derivation line.
- [ ] Sessions list shows `dominant +N` and filters by model — verify by: open
  `http://127.0.0.1:<port>/#/sessions`, pick a model, rows narrow to sessions
  using it.
- [ ] Session page shows the per-model breakdown with cost / `—` — verify by:
  open a mixed-model seeded session.
- [ ] Overview Models card respects project + window by turn time, top-4+other
  chart with table toggle — verify by: seeded sessions spanning the window
  edge; switch 7d/30d/all.
- [ ] Antigravity-style session shows model + turns, tokens and cost `—` —
  verify by: seeded `model.turn` events with no transcript.
- [ ] Admin sets/clears a price; costs change on reload; member cannot — verify
  by: console as admin and as member; API authz tests.
- [ ] Hostile model name renders inert — verify by: verifier (AC29).
- [ ] `rebuild-index` reproduces identical model figures — verify by: store
  parity test.
- [ ] `./init.sh` passes; `scripts/check-stdlib-only.sh` passes.

## Definition of Done

- [ ] All tests derived from the spec pass
- [ ] Code review approved (`code-reviewer` skill + human review)
- [ ] Security review (`security-reviewer` skill) — new admin write endpoint and
  rendering of ingested strings
- [ ] ADRs recorded for relevant decisions (none planned — see D-d)
- [ ] Documentation updated (`docs/SERVER.md`, `docs/ARCHITECTURE.md`)
- [ ] No secrets in the diff
- [ ] Constitution respected
