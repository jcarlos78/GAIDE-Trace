# Spec: Price import — load model prices from benchlm.ai

> **Status:** approved
> **Author:** José Menezes (drafted with Claude Code)
> **Date:** 2026-09-15

## Context

The Model prices page (`specs/model-usage`, AC21–AC23) makes an admin type every
price by hand, five fields per model. A team that uses a dozen models across
providers has to look up each list price and retype it, and do that again
whenever a provider changes prices. Cost estimates drift out of date because
updating them is tedious.

[benchlm.ai](https://benchlm.ai) publishes a machine-readable pricing feed at
`https://benchlm.ai/api/data/pricing`. Observed on 2026-09-15:

- JSON: `{"lastUpdated": "September 15, 2026", "models": [...]}`, 100 entries,
  69 with prices. Each entry has `model`, `creator`, `inputPrice`,
  `outputPrice`, `contextWindow` and `sourceType`. Prices are USD per million
  tokens, and `null` when not listed.
- **Only input and output prices.** It has no cache-read or cache-write prices,
  and those token types make up most of the tokens in a Claude Code session.
- **Display names, not API ids.** The feed says `Claude Opus 5` and
  `Gemini 3.7 Flash`, but captured data says `claude-opus-5` and
  `gemini-3.7-flash`.
- Open-weight models are listed at `0` / `0`, which means "no API list price",
  not "free".
- Served with `Access-Control-Allow-Origin: *` and a 1-hour cache. benchlm.ai
  publishes no license or terms for the data.

For whom: admins who maintain the price table.

This spec replaces one out-of-scope item in `specs/model-usage`, "Fetching
prices from any provider", with the narrower behavior below. Nothing else in
that spec changes: estimates still use prices current at viewing time,
unpriced models are never guessed, and only admins change prices.

## Expected behavior

### Terms

- **Feed:** the JSON document at `https://benchlm.ai/api/data/pricing`.
- **Normalized name:** a model name lowercased, with every run of characters
  other than `a–z` and `0–9` replaced by a single `-`, leading and trailing
  `-` trimmed, and a trailing date suffix (`-YYYYMMDD`) removed.
  `Claude Opus 4.5`, `claude-opus-4-5` and `claude-opus-4-5-20251101` all
  normalize to `claude-opus-4-5`.
- **Match:** a model on the price page whose normalized name equals the
  normalized name of exactly one feed entry. If two or more feed entries
  normalize to the same name, the model is **ambiguous** and cannot be matched.
- **Importable row:** a matched model whose feed entry has a finite
  `inputPrice` and `outputPrice`, each greater than 0 and at most 10,000.

### Use cases

1. **Import prices for known models (main case)**
   - Given: an admin on the Model prices page, and models `claude-opus-5`
     (priced) and `gemini-3.7-flash` (unpriced) seen in the data
   - When: they choose "Load prices from benchlm.ai"
   - Then: the browser fetches the feed and shows a preview without changing
     any price. Each model on the page gets one row: its name, the feed entry
     it matched, current and feed input/output prices (changed values
     highlighted), and a status (importable, unchanged, no match, ambiguous,
     not priced in feed). The feed's `lastUpdated` and a link to benchlm.ai are
     shown. No row is ticked by default.

2. **Confirm selected rows**
   - Given: the preview, with `claude-opus-5` ticked
   - When: the admin applies the selection
   - Then: `claude-opus-5` gets the feed's input and output prices and keeps
     its current cache-read, cache-write-5m and cache-write-1h prices. The price
     table reloads and cost figures everywhere reflect the change on the next
     load.

3. **Unpriced model needs cache prices (alternative case)**
   - Given: the preview, with unpriced `gemini-3.7-flash` importable
   - When: the admin ticks it
   - Then: its three cache-price fields show as empty inputs, and the row
     cannot be applied until all three have valid values. The page says the
     feed has no cache prices. Nothing fills them in for the admin.

4. **Model not in the feed (alternative case)**
   - Given: a model with no match, an ambiguous match, or a feed entry with a
     `null` or `0` price
   - When: the preview is shown
   - Then: the row shows why it cannot be imported, cannot be ticked, and its
     current price is untouched. The admin can still price it by hand.

5. **Feed unavailable or malformed (error case)**
   - Given: benchlm.ai is unreachable, the fetch is blocked (offline network or
     a proxy content-security policy), it times out, or the response is not
     JSON of the expected shape
   - When: the admin loads prices
   - Then: a visible error says the feed could not be loaded and why, and no
     price changes

6. **Partial failure on apply (error case)**
   - Given: several ticked rows, where one save is refused (for example, the
     admin's session expired or the server rejects a value)
   - When: the selection is applied
   - Then: every row reports its own result, rows that saved stay saved, and
     the failed row names the error. Nothing is reported as applied that was
     not.

## Acceptance criteria

Fetching and preview

- [ ] AC1: The Model prices page has a "Load prices from benchlm.ai" control,
  visible only to admins. Using it fetches the feed from the admin's browser;
  the server makes no outbound request.
- [ ] AC2: Loading the feed changes no price. Prices change only when the admin
  applies ticked rows.
- [ ] AC3: The preview lists every model on the price page with one status:
  `importable`, `unchanged` (feed input/output equal the current price),
  `no match`, `ambiguous`, or `not priced in feed` (`null`, `0`, non-finite, or
  outside 0–10,000).
- [ ] AC4: Matching follows the normalized-name rule in Terms. It is tested
  against at least: display name vs API id, dotted vs hyphenated versions, a
  dated suffix, a name that matches two feed entries (ambiguous), and a
  suffixed variant that must not match its base (`Claude Opus 4.7 (Adaptive)`
  must not match `claude-opus-4-7`).
- [ ] AC5: Each importable row shows the matched feed name, current and feed
  input/output prices, and which values would change.
- [ ] AC6: The preview shows the feed's `lastUpdated` value and names
  benchlm.ai as the source. The page's helper text no longer says prices are
  "never fetched from a provider".
- [ ] AC7: No row is ticked by default. A "select all importable" control
  exists.

Applying

- [ ] AC8: Applying a ticked, already-priced row sets input and output from the
  feed and sends the model's current cache-read, cache-write-5m and
  cache-write-1h prices unchanged, through the existing
  `PUT /api/v1/models/prices`.
- [ ] AC9: A ticked, unpriced row cannot be applied until the admin enters
  valid values for all three cache prices. The console never fills them in,
  whether as 0, as a copy of input, or as a provider ratio.
- [ ] AC10: Each applied row reports success or its own error. A failed row
  does not stop the others and is not shown as applied.
- [ ] AC11: After applying, the price table reloads from the server, and the
  "Last changed" column shows the new time and the admin as the author.

Errors and hostile input

- [ ] AC12: A network error, CORS or CSP block, a timeout (at most 15 s), a
  non-2xx response, invalid JSON, or a document without a `models` array shows
  a visible error and changes nothing.
- [ ] AC13: Feed entries that are not objects, or whose `model` is not a string
  of 1–128 characters, are skipped. They never break the preview.
- [ ] AC14: Every string from the feed (`model`, `lastUpdated`, `creator`) is
  rendered as text, never as HTML. A feed name containing markup shows
  literally.
- [ ] AC15: A feed with more than 5,000 entries is refused with a visible error
  rather than rendered.
- [ ] AC16: Server-side validation is unchanged: a feed value that got past the
  console still meets `specs/model-usage` AC28 at the API.

## Out of scope

- **Server-side fetching, scheduled or automatic sync.** The server makes no
  outbound requests. A server-side import endpoint needs its own spec and an
  ADR.
- **Recording the price's source.** `updated_by` stays the admin's name. The
  price table does not record that a value came from benchlm.ai. Doing that
  needs a schema change and its own spec.
- **Cache, batch, long-context or premium-speed prices.** The feed has none.
  They stay admin-maintained.
- **Manual mapping** of an unmatched or ambiguous model to a feed entry. Such
  models are priced by hand as today.
- **Creating price entries for feed models not seen in the data** or not
  already on the price page.
- **Other price sources** or a configurable feed URL.
- **Shipping a price table in the repository.** Still out of scope.

## Effect on captured data

None. No event, archive, transcript or index change. Prices are configuration
and are changed only through the existing admin endpoint.

## Security considerations

- **Data sensitivity:** public list prices. The request to benchlm.ai carries
  no GAIDE-Trace data, no credentials and no model names from the team's
  data. It does reveal the admin's IP address and the console's origin (via
  `Referer`/`Origin`) to benchlm.ai. The fetch uses `credentials: "omit"` and
  `referrerPolicy: "no-referrer"`.
- **Authentication / authorization:** unchanged. Only admins see the control,
  and prices are written only through the existing admin-only PUT endpoint
  (`specs/model-usage` AC26).
- **Abuse cases:**
  - benchlm.ai, or anyone who can tamper with its response, serves markup in
    model names to get stored or reflected XSS in an admin's console → AC14.
  - The feed serves absurd, negative or non-finite prices to distort every
    cost figure → AC3 and AC16 (never importable, and rejected by the server
    anyway), plus AC2 (the admin reviews every change before it happens).
  - The feed serves a huge or deeply malformed document to hang the admin's
    tab → AC12, AC13, AC15.
  - The feed renames an entry so that it matches a different model and
    silently mis-prices it → AC4 (exact normalized match only), AC5 (the
    matched feed name is shown), AC7 (nothing pre-ticked).
- **Data licensing:** benchlm.ai publishes no license or terms. The console
  fetches the public feed on an admin's explicit action and shows its source.
  It does not redistribute, cache or ship the data. If benchlm.ai publishes
  terms that forbid this, the feature is removed.

The change renders third-party strings in the admin console, so the
`security-reviewer` skill runs on the implementation diff.

## Invariants touched

- **D4, D5, D6, D7:** not touched. No capture, ingest or index change.
- **Stdlib only / no CDN:** honored. The server gets no new dependency. The
  console makes a runtime data request to a third party, which is not a
  script or asset load, and only when an admin asks for it.
- **Canonical vocabulary:** not touched.

## Dependencies

- Other specs: `specs/model-usage` (price table, `PUT /api/v1/models/prices`,
  AC26, AC28). This spec amends its out-of-scope list.
- External systems: benchlm.ai pricing feed. It is an undocumented external
  contract this project does not control, so it is parsed defensively and the
  observed shape is pinned in tests with synthesized fixtures.
- Libraries: none.

## Constitution adherence

- **Principle 1 (Spec before code):** this spec is written before
  implementation.
- **Principle 2 (Tests track behavior):** matching, status assignment and
  feed validation (AC3, AC4, AC12, AC13, AC15) are unit-tested. The flow
  (AC1, AC2, AC5–AC11, AC14) is verified end to end with the `verifier` skill,
  using a synthesized feed.
- **Principle 3 (Human approval):** implementation waits for approval of the
  spec, plan and tasks.
- **Principle 7 (Atomic changes):** one feature commit. The one-line amendment
  to `specs/model-usage` goes in the same commit because it is the same
  decision.
- **Principle 8 (Fail visibly):** feed failures and per-row save failures are
  shown, never swallowed (AC10, AC12).

## Identified risks

- **benchlm.ai changes or removes the feed.** Mitigation: defensive parsing
  and a visible error. Manual pricing still works.
- **Feed prices are wrong or stale.** Mitigation: `lastUpdated` is shown, the
  admin reviews every change, and estimates are already labelled as estimates.
- **A normalized match picks the wrong model** (for example, a provider reuses
  a name). Mitigation: the matched feed name is shown and nothing is
  pre-ticked.
- **Browser-side fetch is blocked** in locked-down deployments by a proxy CSP
  or egress rules. Mitigation: AC12 names the cause, and manual pricing is
  unaffected. A server-side import is a later spec.
