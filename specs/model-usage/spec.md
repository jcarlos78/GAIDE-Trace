# Spec: Model usage — per-session model and per-model statistics

> **Status:** approved
> **Author:** José Menezes (drafted with Claude Code)
> **Date:** 2026-09-14

## Context

The team server's console answers "how much did the team use AI assistance"
but not "**which model** did the work". For AI-assisted software engineering
research the model is a first-order variable: comparing sessions, prompts or
failure rates is not meaningful without knowing which model produced them, and
a single session may switch models mid-way.

Today the information is captured but not surfaced:

- **Claude Code** hook payloads do not carry a model. Across two real stores
  (~900 events) not one event had a `model` field. The model exists only in the
  transcript snapshot, on every assistant message (`message.model`).
- **Antigravity** records carry `model` on each `model.turn` event, but no token
  usage.
- The server keeps one comma-joined `models` string per session, derived from
  transcripts only, visible only on the session detail page. Antigravity models
  never reach it. There is no per-model token or turn accounting.

While analysing real transcripts for this spec, a defect in the existing token
accounting surfaced: Claude Code writes **one transcript line per content
block**, repeating the same `message.id` and identical `usage` on each line.
The server sums every line, so session and overview token totals are inflated
by roughly 2–2.8× on real data (one session: 1,121,638 output tokens reported,
405,279 actual). Per-model statistics built on the same counting would inherit
the error, so correct per-message counting is part of this spec.

For whom: the maintainer and research team members using the console to
analyse team sessions; admins who maintain the price table.

## Expected behavior

### Terms

- **Model turn** — one model response attributed to a model:
  - Claude Code: one distinct assistant message in the transcript, identified
    by `message.id`. Lines repeating an already-seen `message.id` are the same
    turn and contribute nothing further. Messages without an `id` count once per
    line. Usage on a message that names no model cannot be attributed to a
    model and is not counted (it does not occur in observed transcripts).
  - Antigravity (and any adapter emitting it): one `model.turn` event with a
    `model` value.
- **Placeholder models** — values that do not name a real model (Claude Code's
  `<synthetic>`). They are never attributed a turn, token or cost.
- **Session model usage** — for each model a session used: turns, input
  tokens, output tokens, cache-read tokens, cache-write tokens (5-minute and
  1-hour tiers separately where the transcript distinguishes them), and first /
  last turn time.
  - Source rule: if the session's transcript yields at least one attributed
    turn, usage comes from the transcript; otherwise from the session's
    `model.turn` events. The two are never added together, so a session is
    never counted twice.
  - Sources with no token data report tokens as unknown (not zero).
- **Dominant model** — the model with the most turns in a session; ties broken
  by output tokens, then by name.
- **Estimated cost** — tokens × the admin-maintained price for that model and
  token type, using the prices current at the time of viewing.

### Use cases

1. **See which model drove a session (main case)**
   - Given: sessions whose transcripts show `claude-opus-5` for 120 turns and
     `claude-sonnet-5` for 30 turns
   - When: a member opens the Sessions list
   - Then: the row shows `claude-opus-5 +1`; opening the session shows a
     per-model breakdown — turns, tokens by type and estimated cost for each
     model, plus the session total

2. **Filter sessions by model**
   - Given: sessions using different models
   - When: a member picks `claude-sonnet-5` in the Sessions model filter
   - Then: only sessions in which that model has at least one turn are listed
     (not only those where it is dominant), and the filter combines with
     project, window and session-id search

3. **Compare models on the Overview (main case)**
   - Given: a project + time window selected on Overview
   - When: the member views the Models card
   - Then: for each model with turns in that window: sessions, turns, input /
     output / cache-read / cache-write tokens, share of turns, and estimated
     cost; plus a per-day chart of turns split by model, with a table view of
     the same data. The window filter applies to turn time, not session time

4. **Antigravity session**
   - Given: an Antigravity session with 12 `model.turn` events on
     `gemini-3.7-flash` and no transcript
   - When: it is shown in the list, session page and Overview
   - Then: the model and 12 turns appear everywhere; tokens and cost show as
     unknown (`—`), and Overview token and cost totals are marked as covering
     only models with token data

5. **Admin maintains prices**
   - Given: an admin
   - When: they open the model price page
   - Then: they see every model seen in the data, with its prices ($ per
     million tokens for input, output, cache read, cache write 5m, cache write
     1h) or "unpriced", and when each price was last changed and by whom; they
     can set, change or clear a model's prices, and cost figures everywhere
     reflect the change on the next load

6. **Unpriced model (alternative case)**
   - Given: a model with turns but no price entry
   - When: any view shows its cost
   - Then: its cost is shown as `—` (never 0, never a guessed price); any total
     that excludes unpriced models says so and names how many were excluded

7. **Fast-mode turns (alternative case)**
   - Given: transcript turns whose usage reports a non-standard `speed`
   - When: cost is estimated
   - Then: standard prices are applied and the view flags that N turns ran at a
     premium speed, so the estimate is a lower bound for them

8. **Existing deployment upgrades (alternative case)**
   - Given: a server with sessions and transcripts ingested by v0.3.1
   - When: the upgraded server starts
   - Then: model usage is available for all existing sessions and session
     token totals are corrected, without the operator running any command and
     without modifying the event archive or the transcript files

9. **Non-admin tries to change prices (error case)**
   - Given: a member-role console user or a member / agent API key
   - When: they attempt to create, change or clear a price
   - Then: the request is refused (403 / 401) and no price changes

10. **Invalid price (error case)**
    - Given: an admin
    - When: they submit a negative, non-numeric, non-finite or absurdly large
      price
    - Then: the request is refused with a message naming the field, and no
      price changes

## Acceptance criteria

Per-message counting (defect fix)

- [ ] AC1 — A transcript in which one assistant message spans N lines with the
  same `message.id` and identical `usage` contributes that usage **once** to the
  session's input, output, cache-read and cache-write totals.
- [ ] AC2 — Assistant messages without an `id` contribute once per line.
- [ ] AC3 — Placeholder models (`<synthetic>`) contribute no turns, tokens or
  cost, and do not appear as a model anywhere in the console or API.

Session model usage

- [ ] AC4 — For a Claude Code transcript, per-model turns and tokens by type
  equal the sums over distinct messages of that model; the sum across models
  equals the session totals.
- [ ] AC5 — For a session with no transcript-attributed turns, per-model turns
  equal the count of its `model.turn` events per `model`, and tokens are
  reported as unknown (`null`), not 0.
- [ ] AC6 — A session with both a model-bearing transcript and `model.turn`
  events counts turns from the transcript only.
- [ ] AC7 — Re-uploading a transcript replaces that session's model usage
  (never accumulates it); re-sending already-ingested events (same `trace_id`)
  does not change it.
- [ ] AC8 — The session list API returns, per session, the dominant model and
  the number of distinct models; the session detail API returns the per-model
  breakdown including estimated cost per model.
- [ ] AC9 — The session list API accepts a `model` filter that matches sessions
  where the model has ≥ 1 turn, combinable with the existing filters; the
  console's Sessions view exposes it with the models present in the data.
- [ ] AC10 — The Sessions table shows `<dominant model>` and `+N` when N other
  models were used; sessions with no model show `—`.

Overview statistics

- [ ] AC11 — The overview API returns, per model, for turns within the
  project + window filter: sessions, turns, input / output / cache-read /
  cache-write tokens, and estimated cost; and a per-day series of turns per
  model. Days are bucketed in UTC, same as the existing activity series.
- [ ] AC12 — A turn is inside the window by its own timestamp; a session that
  spans the window boundary contributes only its turns inside the window.
- [ ] AC13 — The Overview Models card renders the per-model table, a turn-share
  indicator, and the per-day chart with a table toggle; with no model data in
  the window it shows an empty state, not an error.
- [ ] AC14 — With more models than distinct chart series, the chart shows the
  top models by turns and aggregates the rest into "other"; the table still
  lists every model.

Cost

- [ ] AC15 — Estimated cost per model = Σ(tokens of each type ÷ 1,000,000 ×
  that type's price), rounded for display only (the API returns unrounded
  values).
- [ ] AC16 — A model without a price entry has cost `null` in the API and `—`
  in the console; totals exclude it and report the count of excluded models.
- [ ] AC17 — A model with turns but unknown tokens (Antigravity) has cost
  `null`, and is counted as excluded from cost totals, not as unpriced.
- [ ] AC18 — Where the transcript does not split cache writes into 5m / 1h, all
  cache-write tokens are priced at the 5m rate, and this is stated in the view.
- [ ] AC19 — Views with cost show the number of turns that reported a
  non-standard `speed`, when > 0.
- [ ] AC20 — Cost figures are visible to every role that can see token counts
  (member and admin), labelled as estimates at current prices.

Price management

- [ ] AC21 — Admins can list, set, update and clear per-model prices via the
  API and a console page; the page lists every model present in the data, priced
  or not, plus any priced model not (yet) seen.
- [ ] AC22 — Each price entry records when it was last changed and by which
  user, and both are shown on the page.
- [ ] AC23 — Prices survive `rebuild-index` and server restarts.

Migration and rebuildability (D7)

- [ ] AC24 — On first start after upgrade, model usage and corrected token
  totals are derived for all existing sessions from stored transcripts and
  indexed events, with no operator command; the archive and transcript files are
  byte-identical before and after.
- [ ] AC25 — After `rebuild-index`, all model usage figures equal those produced
  by live ingest of the same data.

Negative (security) criteria

- [ ] AC26 — A member-role console user, a member key and an agent key receive
  401/403 on every price-modifying request, and the price table is unchanged.
- [ ] AC27 — An agent key cannot read model statistics, session model usage or
  prices (same as the existing read endpoints).
- [ ] AC28 — Price values that are negative, non-numeric, NaN/Infinity, or above
  a documented ceiling are rejected with 400 and not stored.
- [ ] AC29 — A model name containing HTML/JS (e.g. `<img src=x onerror=…>`)
  ingested via events or a transcript renders as inert text in every console
  view that shows model names, including the price page and chart tooltips.
- [ ] AC30 — Model names longer than a documented limit are truncated for
  attribution, so an agent key cannot store unbounded strings through this
  feature; a flood of distinct model names does not break the Overview (AC14
  aggregation still holds).

## Out of scope

- **Capture-path changes.** The Claude Code hook does not stamp the model onto
  events; `schema/event.schema.json` and captured records are unchanged.
  Sessions whose transcript never reached the server have no Claude Code model
  data — a known, accepted limitation.
- **Subagent transcripts.** Claude Code writes subagent messages to separate
  files that the hook does not snapshot today; their models and tokens are not
  counted. Unchanged by this spec.
- **Historical prices.** Cost uses the price table current at viewing time; no
  price history, no per-date prices, no currency other than USD.
- **Premium-speed pricing and batch/priority tiers.** Flagged (AC19), not priced.
- **Fetching prices from any provider.** No network access; no shipped default
  price table in the repository.
- **Budgets, alerts or quotas** on cost.
- **The pandas analysis toolkit** (`analysis/load_trace.py`) has the same
  per-line duplication in `load_transcripts`. Fixing it is a separate change.
- **Export formats.** CSV/JSONL event export already carries `model` per event;
  no per-model export is added.

## Effect on captured data

None. No new or changed event fields; stores already in the field are read as
they are. The event archive and transcript snapshots are never rewritten. New
server-side data is either derived (rebuildable per D7) or configuration (the
price table, stored like users and keys — not captured data, not part of the
archive).

Observable change to existing numbers: session and overview token totals
**decrease** after upgrade for Claude Code sessions, because duplicated lines
stop being counted. Release notes must say so, as figures previously exported
or quoted from the console were inflated.

## Security considerations

- **Data sensitivity:** model names and token counts — low sensitivity, same
  exposure as existing session data. Prices are operational configuration, not
  secret. No PII added.
- **Authentication / authorization:** reading model usage, statistics and
  prices: `member` and `admin` (console users and keys), same as existing read
  endpoints. Changing prices: `admin` only. `agent` keys: ingest only, as today.
- **Abuse cases:**
  - A member or agent key alters prices to distort cost figures → AC26.
  - An agent key (distributed in install prompts, so weakly held) ingests a model
    name carrying script → stored XSS in an admin's console → AC29.
  - An agent key floods distinct or huge model names to bloat the index or break
    the Overview → AC30, AC14.
  - Malformed price input (NaN, negative, huge) corrupting every cost figure →
    AC28.

Because this touches an authorization boundary (a new admin-only write
endpoint) and renders ingested strings, the `security-reviewer` skill runs on
the implementation diff.

## Invariants touched

- **D4, D5, D6:** not touched — no capture-path change.
- **D7 (JSONL is the truth):** derived model usage must be fully rebuildable from
  the archive + transcript snapshots (AC24, AC25). The price table is
  configuration, like users and keys, and is not derived from the archive.
- **Stdlib only:** honored — no new dependency on the server path or console
  (vanilla JS, no CDN).
- **Canonical vocabulary:** not widened; `model.turn` and `model` are used as
  already defined.

## Dependencies

- Other specs: none (first spec touching the server's session accounting).
- External systems: none. Claude Code's transcript format (`message.id`,
  `message.model`, `message.usage` incl. `cache_creation.ephemeral_5m/1h_*`,
  `speed`) is an external contract this project does not control — parsed
  defensively.
- Libraries: none new.

## Constitution adherence

- **Principle 1 (Spec before code):** written before implementation ✓
- **Principle 2 (Tests track behavior):** each AC maps to tests via
  `test-generator`; fixtures are synthesized transcripts/events, never harvested
  from real stores.
- **Principle 3 (Human approval):** implementation waits for approval of spec,
  plan and tasks.
- **Principle 7 (Atomic changes):** the counting defect fix (AC1–AC3 applied to
  existing totals) lands as its own commit, before the feature.
- **Principle 8 (Fail visibly):** malformed transcript lines are skipped as
  today, but migration failures on startup are logged, not swallowed; unknown
  tokens and unpriced models are shown as unknown, never as 0.

## Identified risks

- Risk: Claude Code changes its transcript format (e.g. stops repeating
  `message.id`, renames usage fields) | Mitigation: defensive parsing; AC tests
  pin the current format with synthesized fixtures so a change is noticed.
- Risk: users read "estimated cost" as billing truth | Mitigation: labelled as an
  estimate at current prices; unpriced/unknown/premium-speed exclusions always
  stated (AC16–AC20).
- Risk: first-start migration is slow on a large archive and delays serving |
  Mitigation: addressed in the plan (e.g. derive from stored transcripts only,
  report progress in logs).
- Risk: the corrected (lower) token totals look like data loss to the team |
  Mitigation: release notes explain the defect and the correction.
