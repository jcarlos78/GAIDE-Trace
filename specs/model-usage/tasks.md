# Tasks: Model usage — per-session model and per-model statistics

> **Prerequisite:** `plan.md` approved (2026-09-14).
>
> Decomposition into **atomic** tasks (~30 min each). Each task has a verifiable completion criterion.
>
> **Status lifecycle:** `pending → in-progress → done → verified`.
> `done` = implemented and its own tests pass. `verified` = checked against the spec's acceptance criteria by someone (or some agent) other than whoever implemented it.
> Status only moves forward when the work actually happened — re-marking tasks to make work *appear* done violates Constitution Principle 9.

Tasks 1–4 form **commit 1** (token double-count fix). Tasks 5–16 form
**commit 2** (model usage). Task 0 is an optional, separate commit.

---

## Task 0 — Make `init.sh` run the suite without pytest (optional)

**Status:** done

**Files:** `init.sh`

**Description:** When `pytest` is absent, fall back to
`python3 -m unittest discover tests` instead of skipping the suite, so bring-up
cannot report OK without running tests. Own commit.

**Done when:**
- [x] On a machine without pytest, `./init.sh` runs the unittest suite and fails
  if a test fails (checked with a deliberately failing probe test: exit 1)
- [ ] With pytest installed, behaviour is unchanged (exit 5 still tolerated) —
  pytest branch is untouched in the diff, but not exercised: pytest is not
  installed on this machine

**Estimate:** ~15 min

**Depends on:** —

---

## Task 1 — Synthesized transcript fixtures + failing counting tests

**Status:** done

**Files:** `tests/fixtures.py`, `tests/test_transcript_usage.py` — no
`tests/__init__.py`: with it, pytest's default import mode could not import the
shared `fixtures` module; tests run via discovery or pytest (code review, commit 1)

**Description:** Helpers that build Claude Code–shaped transcript bytes
(assistant message split across N lines with repeated `message.id` + identical
`usage`; id-less message; `<synthetic>`; malformed line) and Antigravity-shaped
event records. Tests for AC1–AC3 against `summarize_transcript`.

**Done when:**
- [x] Tests exist for AC1, AC2, AC3 and fail on current `main` for AC1/AC3
- [x] Fixtures are hand-written; no bytes derived from real stores
- [x] Runs under `python3 -m unittest discover -s tests`

**Estimate:** ~30 min

**Depends on:** —

---

## Task 2 — Count usage once per assistant message

**Status:** done

**Files:** `server/gaide_trace_server.py`

**Description:** Dedup by `message.id` (id-less lines count once each), skip
placeholder models via a named constant, parse line by line defensively. The
legacy `models` string excludes placeholders.

**Done when:**
- [x] Task 1 tests pass
- [x] `scripts/check-file.sh server/gaide_trace_server.py` passes

**Estimate:** ~30 min

**Depends on:** Task 1

---

## Task 3 — Separate transcript indexing from writing; derived-index version

**Status:** done

**Files:** `server/gaide_trace_server.py`, `tests/test_model_usage_store.py`

**Description:** Split `store_transcript` into file write + `index_transcript`
(plan D-e); `rebuild_index` uses the latter. Add `meta` table and
`DERIVED_VERSION = 1`; on `serve` start re-index all stored transcripts when the
stored version is lower, logging counts and duration. Tests: a v0.3.1-shaped
index (inflated totals) is corrected on start; SHA-256 of every archive and
transcript file unchanged.

**Done when:**
- [x] Store test for the migration path passes (AC24, token-totals part)
- [x] `rebuild-index` no longer rewrites transcript files (test)
- [x] Server log line reports re-derivation (`tests/test_server_startup.py`)

**Notes:** transcripts are parsed line by line from disk; a synthesized 50 MB
transcript re-derives in 0.2 s with ~5 MB peak memory growth. Re-derivation
keeps each session's `transcript_updated_at` as the file's mtime. A fresh data
directory records the current version without logging an upgrade.

**Estimate:** ~45 min

**Depends on:** Task 2

---

## Task 4 — Commit-1 docs, checks and review

**Status:** done

**Files:** `docs/SERVER.md`

**Description:** Upgrade note: token totals for Claude Code sessions decrease
after upgrade, and why. Run `./init.sh`, `python3 -m unittest discover tests`,
`code-reviewer`. Present the diff for approval and commit (HIC).

**Done when:**
- [x] All tests green; `./init.sh` OK
- [x] Code review findings addressed (blocker: sprint-contract command fixed in
  plan.md; suggestions applied except HTTP-API test → Task 7, version bump →
  maintainer at release)
- [x] Maintainer approved commit 1 (`9defefb`; spec docs `fec34c8`, init fallback `46c7004`)

**Estimate:** ~20 min

**Depends on:** Task 3

---

## Task 5 — Failing tests for per-turn extraction and source rule

**Status:** done

**Files:** `tests/test_transcript_usage.py`, `tests/test_model_usage_store.py`

**Description:** Tests for AC4 (per-model sums), AC5 (events-only session,
tokens `None`), AC6 (transcript wins), AC7 (re-upload replaces; duplicate
events no-op), AC25 (rebuild parity), 5m/1h cache split present/absent,
premium `speed`, model name > 128 chars (AC30).

**Done when:**
- [x] Tests exist and fail for the right reason (missing functionality)

**Estimate:** ~40 min

**Depends on:** Task 4

---

## Task 6 — `model_turns` index and `refresh_session_models`

**Status:** done

**Files:** `server/gaide_trace_server.py`

**Description:** Schema: `model_turns` (plan D-a) and
`sessions.dominant_model`, `sessions.model_count` (with `ALTER TABLE` for old
DBs). Per-turn extraction from transcripts; `refresh_session_models` (plan D-c)
called from `index_transcript`; live `model.turn` ingest inserts a row only
when no transcript rows exist, then recomputes D-b; `rebuild_index` clears and
re-derives. Bump `DERIVED_VERSION` to 2. All in single transactions per session.

**Done when:**
- [x] Task 5 tests pass
- [x] Migration test from Task 3 extended: a v0.3.1 index gains `model_turns`
  on start (AC24)

**Notes:** the source rule lives in `Store._replace_session_models` (plan D-c
named it `refresh_session_models`). Re-derivation also refreshes sessions that
have no transcript, from their `model.turn` events — so the mechanism now
covers events too. `DERIVED_VERSION` is 3: version 2 went to the separate
session-time-range bug fix, which lands as its own commit before this one.

**Estimate:** ~60 min (split if it runs over: extraction / refresh+ingest)

**Depends on:** Task 5

---

## Task 7 — HTTP test harness + failing API tests

**Status:** done

**Files:** `tests/server_harness.py`, `tests/test_model_usage_api.py`

**Description:** Start the server on `127.0.0.1:0` with a temp data dir; create
an admin user, a member user, member and agent keys. Tests for AC8, AC9, AC11,
AC12, AC15–AC17, AC19, AC21–AC23, AC26–AC28.

**Done when:**
- [x] Harness starts/stops cleanly with no leftover threads or files
- [x] Tests exist and fail for missing endpoints/fields

**Notes:** in-process server via `http.client` (semgrep blocks dynamic
`urllib` URLs). Test users get a pre-computed password hash and a direct
console session, and the shutdown poll is 20 ms — the suite went from 28 s to
1.5 s.

**Estimate:** ~45 min

**Depends on:** Task 6

---

## Task 8 — Price table and `/api/v1/models` endpoints

**Status:** done

**Files:** `server/gaide_trace_server.py`

**Description:** `model_prices` table (plan D-f); `GET /api/v1/models`
(member), `PUT /api/v1/models/prices` and `DELETE /api/v1/models/prices?model=`
(admin) with validation (all five fields, finite, 0 ≤ p ≤ 10,000, field named in
error), `updated_at`/`updated_by`. `rebuild-index` leaves it intact.

**Done when:**
- [x] AC21 (API part), AC22 (API part), AC23, AC26, AC27, AC28 tests pass

**Estimate:** ~40 min

**Depends on:** Task 7

---

## Task 9 — Session list/detail and overview API extensions with cost

**Status:** done

**Files:** `server/gaide_trace_server.py`

**Description:** `model` filter + `dominant_model`/`model_count` on
`GET /api/v1/sessions`; `models` breakdown on session detail; `models` block on
overview filtered by turn `ts` and project (plan D-i). Cost computed in Python
at query time (plan D-g) with `cost_status`, `unpriced_models`,
`no_token_models`, `premium_turns`. `EXPLAIN QUERY PLAN` check on a synthesized
large index.

**Done when:**
- [x] AC8, AC9, AC11, AC12, AC15, AC16, AC17, AC19 tests pass
- [ ] Overview query uses the `ts` index — **not met as written**: on a
  synthesized 200k-turn index SQLite prefers `idx_model_turns_model` to avoid a
  sort for `GROUP BY model`. Measured 531 ms (all time), 142 ms (from a date),
  108 ms (project + date). Acceptable at research-team scale; revisit if
  indexes grow past ~1M turns. **Deviation accepted by the maintainer
  (2026-09-14).**

**Notes:** the Sessions `model` filter scanned every turn of the model per
session row (703 ms at 200k turns); an added `(session_id, model)` index takes
it to 1 ms. Beyond plan D-i: session detail also returns `models_total`
(use case 1's session total); overview rows carry `turns_without_tokens`
(a model name fed by both a token source and a no-token source is never
silently under-priced); `models.series` names the chart's top models, and
`per_day` folds the rest into `model: null`.

**Estimate:** ~60 min (split if it runs over: sessions / overview)

**Depends on:** Task 8

---

## Task 10 — Validate the five-slot chart palette

**Status:** done

**Files:** `server/webui/style.css`

**Description:** Pick Carbon categorical colours for series 3–4 + neutral
"other"; run the dataviz skill's validator against `#262626` (contrast + CVD
separation). Add `--series-3`, `--series-4`, `--series-other` tokens. If it fails,
fall back to top 3 + other (plan D-j) and note it.

**Done when:**
- [x] Validator output recorded in the task notes; tokens added

**Notes:** `validate_palette.js "#8a3ffc,#009d9a,#0072c3,#d02670" --mode dark
--surface "#262626"` (order = stacking order, bottom up):

```text
[PASS] Lightness band         all 4 inside L 0.48–0.67
[PASS] Chroma floor           all 4 >= 0.1
[PASS] CVD separation         worst adjacent #d02670↔#0072c3 ΔE 14.1 (protan) · tritan 8.4
[PASS] Normal-vision floor    worst adjacent #0072c3↔#009d9a ΔE 15.6 (normal)
[PASS] Contrast vs surface    all 4 >= 3:1
→ ALL CHECKS PASS
```

Rejected on the way: Carbon's 40 steps (cyan 40, magenta 40) are too light for
the dark band; cyan 50 next to teal 50 fails the normal-vision floor (ΔE 13.3);
magenta 50/60 next to teal 50 only reaches the 6–8 CVD warn band; orange 60
passed but is too close to the console's orange "serious" status colour.
"Other" is Carbon helper grey `#8d8d8d` (4.56:1 on the tile), separated by the
2px segment gap.

**Estimate:** ~20 min

**Depends on:** —

---

## Task 11 — Console: Sessions model column + filter

**Status:** verified

**Files:** `server/webui/app.js`

**Description:** "Model" column showing `dominant +N` or `—`; model filter
select fed by `GET /api/v1/models`, combined with project/window/search. All
names through `esc()`.

**Done when:**
- [x] AC10 and AC9 (console part) observed in a browser against a seeded server
  (independent verifier, runs 1–2). The model filter's fetch changed after run 2
  (API shape, error surfacing) — re-verification pending.

**Estimate:** ~30 min

**Depends on:** Task 9

---

## Task 12 — Console: per-model breakdown on the session page

**Status:** verified

**Files:** `server/webui/app.js`, `server/webui/style.css`

**Description:** Table under the session header: model, source, turns, tokens
by type, premium turns, estimated cost / `—`, total row; estimate label.

**Done when:**
- [x] Mixed-model and events-only seeded sessions render as spec use cases 1 and 4
  (independent verifier, runs 1–2). The shared `modelTable` and the session
  response (500-row cap) changed after run 2 — re-verification pending.

**Estimate:** ~30 min

**Depends on:** Task 9

---

## Task 13 — Console: Overview Models card

**Status:** verified

**Files:** `server/webui/app.js`, `server/webui/style.css`

**Description:** Full-width card: per-model table (sessions, turns, share,
tokens by type, cost), turn-share bar, per-day stacked bars (top 4 + other) via
`chartCard` with table toggle and tooltips; notes for unpriced / no-token
exclusions, premium turns, and the 5m cache-write rule; empty state.

**Done when:**
- [x] AC13, AC14, AC18, AC20 observed in a browser against a seeded server
  (verifier run 2). After run 2: chart rows moved to `Map`s (a model named
  `day`/`__proto__` broke the Overview — code review), 400px overflow fix, KPI
  tiles, 500-row cap — re-verification pending.

**Estimate:** ~60 min (split if it runs over: table / chart)

**Depends on:** Task 9, Task 10

---

## Task 14 — Console: admin Model prices view

**Status:** verified

**Files:** `server/webui/app.js`, `server/webui/index.html`

**Description:** Admin-only nav entry and `#/prices` view: every model from
`GET /api/v1/models`, prices or "unpriced", last changed at/by, inline set /
edit / clear with field-level errors. Non-admins redirected like Users/Keys.

**Done when:**
- [x] AC21, AC22 observed in a browser; member user does not see the nav entry
  and is redirected from `#/prices` (verifier runs 1–2). Load and clear paths
  changed after run 2 — re-verification pending.

**Estimate:** ~45 min

**Depends on:** Task 8

---

## Task 15 — Commit-2 docs

**Status:** done

**Files:** `docs/SERVER.md`, `docs/ARCHITECTURE.md`

**Description:** API table rows for new/extended endpoints; "Model usage and
cost estimates" section (sources, turn definition, exclusions, estimate
caveats, price management); D7 paragraph on derived-index versioning.

**Done when:**
- [x] Every endpoint and field from plan D-i is documented, plus the additions
  recorded in Task 9's notes, the 500-row cap (`omitted`, `models_omitted`),
  the startup skip/WARNING lines and ingest's handling of malformed input
  (`docs/SERVER.md` §6–§7). *(Corrected after code re-review: this was checked
  before those docs existed.)*

**Estimate:** ~30 min

**Depends on:** Task 9, Task 14

---

## Task 16 — Verification, reviews, commit

**Status:** in-progress

**Files:** — (seed script kept in scratchpad, not committed)

**Description:** Seed a local server with synthesized data (mixed-model Claude
sessions spanning the window edge, an Antigravity session, a hostile model
name, >5 models). Run `verifier` for console ACs incl. AC29; `code-reviewer`
and `security-reviewer` on the diff; `./init.sh`; present diff for approval and
commit (HIC).

**Done when:**
- [x] Every sprint-contract item checked — verifier run 2 passed all items; runs
  3–4 re-checked every area changed afterwards (run 4: no failures, no regressions)
- [x] Review findings addressed — both reviewers confirmed their first-round
  blockers fixed; their second-round blockers (token-sum overflow, docs claim,
  AC33 parse paths) are fixed with tests and exercised in verifier run 4, but
  the reviewers were not re-run on that last round
- [ ] Maintainer approved commit 2

**Notes:** verifier run 1 failed AC3, AC14, AC30 and found a pre-existing
session-time bug (fixed in its own commit). Run 2 passed on desktop and found a
400px overflow. Run 3 failed AC33 in the console (a year-0001 event crashed the
Overview's all-time charts) and found four minor defects. Run 4 passed everything.
Known minor issues left open: the Overview Sessions tile counts sessions with
events while the Projects card counts all sessions (pre-existing); chart axes
omit the year, and turns with unparseable timestamps are in tables but not the
per-day chart; a failed stale price clear scrolls the list to its notice at the
top; a priced model with zero tokens can make a partial cost read "$0.00" (the
exclusion note is shown). Evidence screenshots are kept outside the repository
(scratchpad `verify-shots/`).

**Estimate:** ~60 min

**Depends on:** Tasks 11–15

---

## Task 17 — Harden derivation and the API against hostile stored data

**Status:** done

**Files:** `server/gaide_trace_server.py`, `server/webui/app.js`,
`server/webui/style.css`, `tests/test_transcript_usage.py`,
`tests/test_model_usage_store.py`, `tests/test_model_usage_api.py`,
`tests/test_server_startup.py`

**Description:** Added from the security and code reviews. Out-of-range
timestamps and absurd token counts no longer raise while indexing (they made an
agent key able to keep the server from starting after the upgrade);
re-derivation logs and skips a session it cannot derive; long message ids are
digested; per-model listings are capped at 500 with an omitted count; huge
integer prices and oversized JSON literals get a 400; console chart rows are
`Map`-keyed so no model name can collide with row fields.

**Done when:**
- [x] Regression tests for each hostile value, the skip-and-log path, the cap and
  the price edge cases pass
- [x] Second round (code + security re-review): token sums use floating-point
  `TOTAL()` so ingested counts cannot overflow SQL aggregates; session detail is
  capped too; the share card's "Other" is derived from window totals; event
  batches and transcript lines with over-long literals or deep nesting no
  longer drop the connection; non-text event fields are stored as JSON text.
  90 tests pass.
- [x] A real `serve` start over hostile stored transcripts reaches "console:"

**Estimate:** ~90 min

**Depends on:** Task 16 reviews

---

## Traceability

| Spec criterion | Task(s) | Status |
| --- | --- | --- |
| AC1 — message split over lines counted once | 1, 2 | done |
| AC2 — id-less messages count per line | 1, 2 | done |
| AC3 — placeholder models excluded | 1, 2, 11–13 | verified (runs 2, 4) |
| AC4 — per-model sums equal session totals | 5, 6 | done |
| AC5 — events-only session, tokens unknown | 5, 6 | done |
| AC6 — transcript wins over events | 5, 6 | done |
| AC7 — re-upload replaces, duplicate events no-op | 5, 6 | done |
| AC8 — list dominant/count, detail breakdown | 7, 9 | verified (runs 1, 4) |
| AC9 — `model` filter | 7, 9, 11 | verified (run 4) |
| AC10 — Sessions table `dominant +N` | 11, 16 | verified (run 4) |
| AC11 — overview per-model block + per-day | 7, 9 | done |
| AC12 — window by turn time | 7, 9 | verified (runs 2, 4) |
| AC13 — Models card + empty state | 13, 16 | verified (run 4) |
| AC14 — top models + "other" | 10, 13, 16, 17 | verified (runs 3, 4) |
| AC15 — cost formula, unrounded API | 7, 9 | verified (run 1 (hand-computed costs matched)) |
| AC16 — unpriced model cost null / `—` | 7, 9, 12, 13 | verified (run 4) |
| AC17 — no-token model cost null, separate count | 7, 9 | verified (runs 1, 4) |
| AC18 — unsplit cache writes at 5m rate, stated | 5, 6, 13 | verified (run 4) |
| AC19 — premium-speed turn count | 5, 7, 9 | verified (run 4) |
| AC20 — cost visible to members, labelled | 13, 16 | verified (run 4) |
| AC21 — admin price CRUD, all models listed | 7, 8, 14 | verified (runs 3, 4) |
| AC22 — last changed at/by | 7, 8, 14 | verified (run 4) |
| AC23 — prices survive rebuild/restart | 7, 8 | done |
| AC24 — automatic upgrade, files untouched | 3, 6 | done |
| AC25 — rebuild parity | 5, 6 | done |
| AC26 — non-admins cannot change prices | 7, 8 | verified (runs 1–3) |
| AC27 — agent key cannot read stats/prices | 7, 8, 9 | verified (runs 1–2) |
| AC28 — invalid prices rejected | 7, 8, 17 | done |
| AC29 — hostile model name renders inert | 11–14, 16 | verified (runs 1–4) |
| AC30 — model name truncation, flood-safe | 5, 6, 13, 17 | verified (runs 3, 4) |
| AC31 — Output tokens tile = Models card total | 13 | verified (runs 3, 4) |
| AC32 — Est. cost tile = Models card cost total | 13 | verified (runs 3, 4) |
| AC33 — hostile stored values never block ingest/start | 17 | verified (runs 3 (ingest), 4 (console)) |

> Update status as tasks progress, using the same lifecycle (`pending | in-progress | done | verified`). A criterion is `verified` only when exercised against the running application, not just by green unit tests.
