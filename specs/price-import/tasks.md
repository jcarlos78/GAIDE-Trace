# Tasks: Price import — load model prices from benchlm.ai

> **Prerequisite:** `plan.md` approved (2026-09-19, including D-b). Tasks approved 2026-09-19.
>
> Decomposition into **atomic** tasks (~30 min each). Each task has a verifiable completion criterion.
>
> **Status lifecycle:** `pending → in-progress → done → verified`.
> `done` = implemented and its own tests pass. `verified` = checked against the spec's acceptance criteria by someone (or some agent) other than whoever implemented it.
> Status only moves forward when the work actually happened — re-marking tasks to make work *appear* done violates Constitution Principle 9.

All tasks form **one commit** (spec, Principle 7), including the one-line
amendment to `specs/model-usage/spec.md`. Tasks 1–2 are red, 3–4 green.

---

## Task 1 — Logic skeleton and node-driven test harness

**Status:** verified

**Files:** `server/webui/price-import.js` (new), `server/webui/index.html`,
`tests/test_price_import.py` (new)

**Description:** Create `price-import.js` as a classic script defining
`normalizeModelName`, `parseFeed`, `classifyModels` and `buildPriceBody` as
stubs that throw, with the guarded
`if (typeof module !== "undefined") module.exports = {...}` (D-a). Load it in
`index.html` before `app.js`. In the test file, add a helper that runs
`node -e <harness>` via `subprocess`, passes a case table as JSON on stdin and
returns the JSON results; each test class calls `skipTest` with a reason when
`node` is not on `PATH` (D-b).

**Done when:**
- [x] One smoke test calls a stub through the harness and gets its error back
  as data (the harness itself works)
- [x] With `node` removed from `PATH`, the module's tests report as skipped
  with a message naming Node
- [x] The console at `#/prices` loads with no console errors (the
  `module` guard holds in the browser)

**Estimate:** ~30 min

**Depends on:** —

---

## Task 2 — Failing unit tests for matching, statuses, parsing and bodies

**Status:** verified

**Files:** `tests/test_price_import.py`

**Description:** Write the unit cases from the plan's testing strategy, all
with synthesized inputs:
- AC4: `Claude Opus 5` ↔ `claude-opus-5`; `Gemini 3.7 Flash` ↔
  `gemini-3.7-flash`; `Claude Opus 4.5` ↔ `claude-opus-4-5-20251101`; two feed
  entries collapsing to one name → ambiguous; `Claude Opus 4.7 (Adaptive)`
  does not match `claude-opus-4-7`; a non-8-digit numeric suffix is kept.
- AC3: importable, unchanged, no match, ambiguous, and not priced for `null`,
  `0`, negative, `"5"`, non-finite, and `10001`.
- AC13: non-object entries, missing / empty / 129-char / non-string `model`
  are skipped while valid siblings still classify.
- AC12 (parse side) and AC15: invalid JSON, missing or non-array `models`,
  non-object top level, 5,001 entries → refused with a reason.
- AC8 / AC9: a priced row's body carries its existing cache values unchanged;
  an unpriced row with empty, negative or non-numeric cache inputs returns the
  offending field names and no body.

**Done when:**
- [x] Every case above exists and fails against the stubs
- [x] No fixture is derived from the real downloaded feed

**Estimate:** ~30 min

**Depends on:** 1

---

## Task 3 — Implement `normalizeModelName` and `parseFeed`

**Status:** verified

**Files:** `server/webui/price-import.js`

**Description:** Normalization per the spec's Terms (lowercase, runs of
non-`[a-z0-9]` → `-`, trim `-`, strip a trailing `-YYYYMMDD`). `parseFeed`
takes the response text, refuses invalid JSON, a non-object top level, a
missing or non-array `models`, and more than 5,000 entries, each with a
reason; skips invalid entries (AC13); returns `lastUpdated` as a string or
null and the kept entries.

**Done when:**
- [x] AC4 normalization, AC12 (parse side), AC13 and AC15 tests pass

**Estimate:** ~30 min

**Depends on:** 2

---

## Task 4 — Implement `classifyModels` and `buildPriceBody`

**Status:** verified

**Files:** `server/webui/price-import.js`

**Description:** `classifyModels(pageModels, feedEntries)` returns one row per
page model with its status (AC3), matched feed name, and current vs. feed
input/output with per-field changed flags (AC5). Only `importable` rows are
tickable (D-e). `buildPriceBody(row, cacheInputs)` returns either the PUT body
(priced: current cache values; unpriced: the admin-entered ones) or the list
of missing/invalid fields (D-f, AC9). Never fills a cache value itself.

**Done when:**
- [x] The whole `test_price_import.py` suite passes with Node installed, none
  skipped
- [x] `./init.sh` passes

**Estimate:** ~30 min

**Depends on:** 3

---

## Task 5 — Confirm AC16 coverage at the API

**Status:** done

**Files:** none expected (`tests/test_model_usage_api.py` only if a gap is
found)

**Description:** Check that the existing AC28 tests reject negative,
non-finite, non-numeric and over-limit prices through
`PUT /api/v1/models/prices`. Add a case only for a value class the plan's
unit tests treat as unimportable but the API tests don't cover.

**Done when:**
- [x] Each rejected value class from AC3 maps to an existing (or new) API test,
  noted in this task — negative, `"25"`, `true`, `null`, non-finite, over
  ceiling, `10**400` and missing are all rejected by existing `test_ac28_*`
  cases in `tests/test_model_usage_api.py`. `0` is the one class the console
  refuses but the API accepts, by design (a 0 feed price means "no list
  price"; a 0 hand-set price is legitimate). No new API test needed.

**Estimate:** ~15 min

**Depends on:** —

---

## Task 6 — Load control, fetch and error display

**Status:** verified

**Files:** `server/webui/app.js`

**Description:** In `viewPrices`, add "Load prices from benchlm.ai", rendered
only for admins (AC1). On click, fetch the constant feed URL with
`credentials: "omit"`, `referrerPolicy: "no-referrer"`, `cache: "no-store"`
and a 15 s `AbortController` timeout; refuse non-2xx and bodies over 5 MB
before parsing; pass the text to `parseFeed` (D-c). Every failure — network,
CORS/CSP, timeout, status, size, parse — shows a visible error naming the
cause and changes nothing (AC12). The control is disabled while a load is in
flight.

**Done when:**
- [x] As member, the control is absent; as admin, it is present
- [x] Loading makes no request to the GAIDE-Trace server beyond the existing
  `GET /api/v1/models` (browser network log)
- [x] An aborted route, a 500 and a non-JSON body each show a distinct error

**Estimate:** ~30 min

**Depends on:** 4

---

## Task 7 — Preview table, source line and select-all

**Status:** verified

**Files:** `server/webui/app.js`, `server/webui/style.css`

**Description:** Render the `classifyModels` rows inside `.table-scroll`:
model, matched feed name, current and feed input/output with changed values
highlighted, status badge, and a checkbox only on importable rows, none
ticked (AC5, AC7). Show `lastUpdated` and a fixed-literal benchlm.ai link with
`rel="noopener noreferrer"` (AC6, D-g). Add "Select all importable". Every
feed string goes through `esc()` / `modelCell()` (AC14). Styles reuse existing
tokens and work in light and dark.

**Done when:**
- [x] A synthesized feed with one row of each status renders all five statuses
- [x] A feed model `<img src=x onerror=alert(1)>` shows as literal text, no
  dialog, no console error
- [x] Nothing is ticked on first render; select-all ticks exactly the
  importable rows

**Estimate:** ~30 min

**Depends on:** 6

---

## Task 8 — Cache inputs for unpriced rows and Apply gating

**Status:** verified

**Files:** `server/webui/app.js`, `server/webui/style.css`

**Description:** When an unpriced importable row is ticked, show three empty
number inputs (cache-read, cache-write-5m, cache-write-1h) and a note that the
feed has no cache prices. Run `buildPriceBody` on every change; mark invalid
fields and keep Apply disabled while any ticked row reports missing fields, or
while nothing is ticked (AC9).

**Done when:**
- [x] Ticking an unpriced row disables Apply until all three inputs hold valid
  values; the inputs start empty
- [x] Unticking the row re-enables Apply for the remaining selection
  (verifier run 3)

**Estimate:** ~30 min

**Depends on:** 7

---

## Task 9 — Apply loop, per-row results and reload

**Status:** verified

**Files:** `server/webui/app.js`, `server/webui/style.css`

**Description:** Apply saves ticked rows one at a time through the existing
`PUT /api/v1/models/prices` (AC8, D-d). Each row shows success or its own
error as soon as it finishes; a failure does not stop the rest; a 401 stops
the loop and marks remaining rows "not applied" (AC10). Afterwards reload the
price table from `GET /api/v1/models` so "Last changed" shows the new time and
admin (AC11). Results stay visible until dismissed.

**Done when:**
- [x] Applying a priced row changes only `input`, `output`, `updated_at`,
  `updated_by` in the `GET /api/v1/models` diff
- [x] With one PUT intercepted as 400, the other rows save and the failed row
  shows the error and is not marked applied

**Estimate:** ~30 min

**Depends on:** 8

---

## Task 10 — Docs, cross-reference, `init.sh` warning

**Status:** verified

**Files:** `server/webui/app.js` (helper text), `docs/SERVER.md`,
`tests/README.md`, `init.sh`, `specs/model-usage/spec.md`

**Description:** Replace the "never fetched from a provider" helper text
(AC6). In `docs/SERVER.md`, describe the import: source, browser-side fetch,
no cache prices, admin review. In `tests/README.md`, note Node is optional and
only needed for `test_price_import.py`. Make `init.sh` print a warning when
`node` is absent. Point the model-usage out-of-scope bullet "Fetching prices
from any provider" to `specs/price-import`.

**Done when:**
- [x] `grep -r "never fetched" server/webui` finds nothing
- [x] `./init.sh` with `node` stripped from `PATH` warns and still passes
- [x] `scripts/check-stdlib-only.sh` passes

**Estimate:** ~20 min

**Depends on:** 9

---

## Task 11 — Verification against the running console

**Status:** done

**Files:** `specs/price-import/tasks.md` (statuses, traceability)

**Description:** Run the `verifier` skill against a local server, with the
feed URL intercepted by Playwright `page.route` to serve synthesized feeds;
the real benchlm.ai URL is never hit. Cover every console criterion in the
plan's testing strategy, including the timeout case (route delayed just past
15 s).

**Done when:**
- [x] Each sprint-contract item in `plan.md` has a recorded result
- [x] The traceability table below is updated from the verifier's report only

**Result:** three independent verifier runs (Playwright, local server,
benchlm.ai intercepted, synthesized data). Run 1: all console criteria pass;
defect D1 (a 401 mid-apply signed the admin out and lost per-row results,
spec use case 6). Run 2, after the D1 and review fixes: D1 fixed, stale-preview
fix confirmed, regressions pass; minor defect N1 (head button overflow at
390 px). Run 3: N1 fixed, Apply un-gating on untick confirmed. Every sprint
contract item passed. Not observable: a real CORS/CSP block, because
`route.fulfill` bypasses CORS; that path shares the abort case's error
branch, which passed. Out of scope and pre-existing: at 390 px the top nav
makes the page scroll sideways.

**Estimate:** ~45 min

**Depends on:** 10

---

## Task 12 — Code and security review

**Status:** done

**Files:** as findings require

**Description:** Run `code-reviewer` and `security-reviewer` on the diff
(third-party strings rendered in the admin console). Fix confirmed findings;
re-run affected tests and verifier cases.

**Done when:**
- [x] Both reviews report no open blocking findings — security review: no
  blockers; code review: one blocker (task statuses ahead of evidence), fixed
  by moving Tasks 1 and 6–9 back to `in-progress` until the verifier ran.
  Fixed from the suggestions: stale preview after a hand edit (now
  re-classified on every table load), `Origin` disclosure in docs and comment,
  a real deep-nesting test, the disabled-button rule scoped to the import card,
  earlier results kept on retry, escaped current prices, globals comment.
  Not taken: a server CSP (pre-existing gap, needs its own change).
- [x] Human review before commit — approval delegated: the maintainer granted
  auto-approve for the 2026-09-19 session; no line-by-line human review of this
  diff has happened yet

**Estimate:** ~30 min

**Depends on:** 11

---

## Traceability

| Spec criterion | Task(s) | Status |
| --- | --- | --- |
| AC1 — admin-only control, browser-side fetch | 6, 11 | verified (runs 1–2) |
| AC2 — loading changes no price | 6, 11 | verified (run 1) |
| AC3 — one status per model | 2, 4, 7 | verified (unit + run 1 UI) |
| AC4 — normalized-name matching | 2, 3, 4 | verified (unit + run 1 UI) |
| AC5 — matched name, current vs feed, changes shown | 4, 7 | verified (run 1) |
| AC6 — `lastUpdated`, source named, helper text | 7, 10 | verified (run 1) |
| AC7 — nothing pre-ticked, select-all | 7 | verified (runs 1–2) |
| AC8 — priced row keeps cache prices, existing PUT | 2, 4, 9 | verified (runs 1–2) |
| AC9 — unpriced row needs all three cache prices | 2, 4, 8 | verified (runs 1, 3) |
| AC10 — per-row result, failures isolated | 9, 11 | verified (runs 1–2, incl. 401) |
| AC11 — reload, "Last changed" updated | 9, 11 | verified (runs 1–2) |
| AC12 — feed failures shown, nothing changes | 2, 3, 6, 11 | verified (runs 1–2; real CORS block not observable) |
| AC13 — invalid entries skipped | 2, 3 | done (unit only) |
| AC14 — feed strings render as text | 7, 11, 12 | verified (runs 1–2) |
| AC15 — >5,000 entries refused | 2, 3 | verified (unit + run 1) |
| AC16 — server validation unchanged | 5 | verified (existing API tests + run 1) |

> Update status as tasks progress, using the same lifecycle (`pending | in-progress | done | verified`). A criterion is `verified` only when exercised against the running application, not just by green unit tests.
