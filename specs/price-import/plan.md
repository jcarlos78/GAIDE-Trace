# Plan: Price import — load model prices from benchlm.ai

> **Prerequisite:** `spec.md` approved (2026-09-15).
>
> **Status:** approved 2026-09-19 by José Menezes, including D-b (Node at test time).

## Architectural decisions

- **D-a: Pure logic in its own console file, `server/webui/price-import.js`.**
  This file holds the functions with no DOM or network access:
  `normalizeModelName`, `parseFeed` (shape checks, entry skipping, size cap),
  `classifyModels` (models on the price page plus feed entries become preview
  rows with a status) and `buildPriceBody` (a ticked row plus admin-entered
  cache values becomes a PUT body, or a list of missing fields). It is a
  classic script that defines globals, and at the end it does
  `if (typeof module !== "undefined") module.exports = {...}` so tests can
  load it.
  *Why a separate file:* `app.js` runs its router on load and touches
  `document`, so it can't be loaded outside a browser. The server already
  serves any file in the flat `webui/` directory, so this needs no server
  change. `index.html` loads it before `app.js`. (Minor decision.)
- **D-b: Unit tests run the JS through `node`, driven from Python
  `unittest`.** `tests/test_price_import.py` starts
  `node -e <harness>` via `subprocess`. The harness requires
  `price-import.js`, runs the case table the Python test passes as JSON on
  stdin, and prints results as JSON. The assertions stay in Python, so the
  suite stays one `unittest` run. **If `node` is not on `PATH`, the tests call
  `skipTest` with a message saying why.**
  *Trade-off, flagged for you:* this adds an optional test-time tool (Node) to
  a project whose test suite is stdlib Python. It is not a runtime or
  deployment dependency, and nothing in `hooks/`, `tools/`, `server/` or
  `schema/` changes, so in my judgement it needs no ADR. The cost: on a
  machine without Node, `./init.sh` reports "tests OK" with these tests
  skipped, and CI runs no tests today anyway. The alternatives are worse.
  Porting the logic to Python tests a copy, not the shipped code. Relying
  only on Playwright covers AC4's matching cases through the UI, which is
  slow and brittle. I'll make `init.sh` print a warning when Node is absent.
- **D-c: The fetch runs in `app.js` with fixed options.** It uses the constant
  URL `https://benchlm.ai/api/data/pricing`, `credentials: "omit"`,
  `referrerPolicy: "no-referrer"`, `cache: "no-store"`, and an
  `AbortController` that aborts after 15 s (AC12). It reads the body as
  text, refuses anything over 5 MB before `JSON.parse`, then calls
  `parseFeed`, which refuses more than 5,000 entries (AC15). The 5 MB byte
  cap is my addition, in line with AC15's intent: the entry cap alone can't
  stop a huge single document from hanging the tab before it is parsed. The
  feed today is 13 KB.
- **D-d: Apply saves one row at a time, in order, through the existing `PUT`.**
  Saves run one after another, not in parallel. Each row shows a result as
  soon as it has one (AC10). An error on one row does not stop the rest. A
  401 stops the loop, because every later row would also fail and the
  console already sends the admin to sign in. Rows not yet attempted are
  reported as "not applied". Once all rows finish, the table reloads from
  `GET /api/v1/models` (AC11). The preview keeps each row's result
  visible until the admin dismisses it.
- **D-e: Rows with status `unchanged` can't be ticked.** Applying one would only
  bump `updated_at`, which would falsely say the price was reviewed. The spec
  says only importable rows can be ticked, so this follows from AC3 and AC7.
- **D-f: Cache values.** Priced models send their current `cache_read`,
  `cache_write_5m` and `cache_write_1h` from the `GET /api/v1/models` response
  used to build the preview. If a price changes elsewhere between preview and
  apply, those cached values overwrite the change. That is the same
  last-write-wins behavior as the existing edit row. Unpriced models get three
  empty number inputs. `buildPriceBody` returns the missing or invalid fields
  and the Apply button stays disabled while any ticked row has them (AC9).
- **D-g: Rendering.** Every feed string goes through the existing `esc()` /
  `modelCell()` helpers (AC14). The benchlm.ai link is a fixed literal with
  `rel="noopener noreferrer"`, never built from feed data.
- **No ADR.** The server, the capture path, dependencies and the storage
  model are unchanged. The one project-level choice is Node at test time
  (D-b), recorded here for you to overrule.

## Affected components

- `server/webui/price-import.js`: **new**, pure logic (D-a).
- `server/webui/app.js`: `viewPrices` gains the load control, the preview
  panel (status, matched name, current vs. feed prices, checkboxes, select-all,
  cache inputs, per-row results) and the apply loop. The helper text is
  updated (AC6).
- `server/webui/index.html`: add `<script src="price-import.js">` before
  `app.js`.
- `server/webui/style.css`: preview-row states (changed-value highlight,
  status badges, per-row result). Reuses existing tokens.
- `tests/test_price_import.py`: **new**, node-driven unit tests (D-b).
- `tests/README.md`: note the optional Node requirement for this module.
- `init.sh`: warn when `node` is missing.
- `specs/model-usage/spec.md`: the out-of-scope bullet "Fetching prices from
  any provider" points to `specs/price-import`.
- `docs/SERVER.md`: the Model prices section describes the import, where the
  data comes from, what it does not cover (cache prices) and that the fetch
  happens in the admin's browser.

Not touched: `server/gaide_trace_server.py`, `hooks/`, `tools/`, `schema/`,
`analysis/`, `deploy/`.

## Implementation sequence

One commit (spec Principle 7 note), red then green:

1. `price-import.js` skeleton with exports, plus `tests/test_price_import.py`
   covering AC3, AC4, AC9 (the logic side), AC13 and AC15 (red).
2. Implement `normalizeModelName`, `parseFeed`, `classifyModels` and
   `buildPriceBody` (green).
3. Console: load control, fetch with D-c options and error display
   (AC1, AC2, AC12).
4. Console: preview table, select-all and cache inputs (AC5–AC7, AC9, AC14).
5. Console: apply loop, per-row results and reload (AC8, AC10, AC11).
6. Docs, spec cross-reference, `init.sh` warning, `tests/README.md`.
7. `verifier` against a local server with the feed request intercepted by
   Playwright (`page.route`) to serve synthesized feeds. The real benchlm.ai
   URL is never hit in verification. Then `code-reviewer` and
   `security-reviewer` on the diff.

## Testing strategy

- **Unit (`tests/test_price_import.py`, node-driven):**
  - AC4 normalization and matching: `Claude Opus 5` ↔ `claude-opus-5`,
    `Gemini 3.7 Flash` ↔ `gemini-3.7-flash`, `Claude Opus 4.5` ↔
    `claude-opus-4-5-20251101`, two feed entries collapsing to one name
    (ambiguous), `Claude Opus 4.7 (Adaptive)` must not match
    `claude-opus-4-7`, and a date-like suffix that is not 8 digits stays.
  - AC3 statuses: importable, unchanged, no match, ambiguous, and not priced
    for each of `null`, `0`, negative, `"5"` (string), `1e309`/non-finite,
    and `10001`.
  - AC13: non-object entries, a missing, empty or 129-character `model`, and a
    non-string `model` are skipped while valid siblings still classify.
  - AC12 (parse side) and AC15: invalid JSON, a missing or non-array
    `models`, a top level that is not an object, and 5,001 entries are
    refused with a reason.
  - AC8 and AC9 bodies: a priced row carries its existing cache values; an
    unpriced row with empty, negative or non-numeric cache inputs reports
    those fields and builds no body.
- **Server:** no new tests. The API is unchanged, and AC16 is already covered
  by `tests/test_model_usage_api.py` (AC28 price validation). I'll confirm
  that coverage rather than duplicate it.
- **Console (verifier + Playwright, intercepted feed):** AC1 (not visible as
  member; no request to the server beyond the existing API), AC2, AC5–AC8,
  AC10 (one row forced to fail by intercepting its PUT with a 400), AC11,
  AC12 (feed route aborted, 500, non-JSON, delayed past 15 s), and AC14 (feed
  model `<img src=x onerror=alert(1)>` renders as text, with no dialog and no
  console error).
- Fixtures are synthesized feeds written in the tests. The real feed
  downloaded while drafting the spec is not committed.

## Implementation risks

- **The 15 s timeout makes the AC12 verifier case slow.** Mitigation: a
  single case, with the route delayed just past the limit.
- **`module.exports` in a classic script.** It is guarded by
  `typeof module`, so browsers never see it. The check is that the console
  loads with no console errors.
- **Wide preview table on narrow screens.** Mitigation: reuse the existing
  `.table-scroll` wrapper, as the price table does.
- **Node version differences in test runners.** Mitigation: the harness uses
  only CommonJS `require` and `JSON`, which work on any maintained Node.

## Sprint contract

- [ ] Matching and statuses behave as AC3/AC4 state. Verify by:
  `python3 -m unittest discover -s tests -p 'test_price_import.py' -v`
  (all pass, none skipped, with Node installed).
- [ ] Without Node, the module's tests skip with a reason and `init.sh` warns.
  Verify by: running with `PATH` stripped of `node`.
- [ ] Only admins see "Load prices from benchlm.ai". Verify by: opening
  `#/prices` as admin and as member.
- [ ] Loading a synthesized feed shows the preview, with every price
  unchanged on the server. Verify by: Playwright-intercepted feed, then
  `GET /api/v1/models` before and after.
- [ ] Applying a ticked priced row changes only input/output. Verify by: the
  `GET /api/v1/models` diff shows only `input`, `output`, `updated_at` and
  `updated_by` changed.
- [ ] An unpriced ticked row blocks Apply until all three cache values are
  valid. Verify by: the verifier interacting with the inputs.
- [ ] One failing row does not stop the others, and each shows its result.
  Verify by: intercepting one PUT with a 400.
- [ ] Unreachable, non-JSON, wrong-shape and timed-out feeds show an error and
  change nothing. Verify by: Playwright routes.
- [ ] A hostile feed model name renders inert. Verify by: the verifier (AC14).
- [ ] `./init.sh` passes, and `scripts/check-stdlib-only.sh` passes.

## Definition of Done

- [ ] All tests derived from the spec pass
- [ ] Code review approved (`code-reviewer` skill + human review)
- [ ] Security review (`security-reviewer` skill): third-party strings are
  rendered in the admin console
- [ ] ADRs recorded for relevant decisions (none planned; see D-b)
- [ ] Documentation updated (`docs/SERVER.md`, `tests/README.md`,
  `specs/model-usage/spec.md` cross-reference)
- [ ] No secrets in the diff
- [ ] Constitution respected
