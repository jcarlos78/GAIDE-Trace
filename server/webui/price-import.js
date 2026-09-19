/* GAIDE-Trace console — price import from the benchlm.ai pricing feed
   (specs/price-import). Pure logic only: no DOM, no network, so the unit
   tests can load this file under Node. app.js does the fetching and
   rendering.

   The feed is an undocumented third-party document: everything in it is
   checked here before app.js renders or saves any of it. */

"use strict";

const PRICE_FEED_URL = "https://benchlm.ai/api/data/pricing";
const PRICE_FEED_MAX_BYTES = 5 * 1024 * 1024;
const PRICE_FEED_MAX_ENTRIES = 5000;
const PRICE_FEED_MAX_NAME = 128;
const PRICE_MAX_PER_MTOK = 10000;   // same ceiling as the server (MAX_PRICE_PER_MTOK)
const PRICE_CACHE_FIELDS = ["cache_read", "cache_write_5m", "cache_write_1h"];

function normalizeModelName(name) {
  return String(name).toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-\d{8}$/, "");
}

function parseFeed(text) {
  // A cap on characters, applied before JSON.parse; app.js also refuses an
  // oversized body by its byte count before reading it into a string.
  if (text.length > PRICE_FEED_MAX_BYTES) {
    return { ok: false, error: "the feed is larger than 5 MB" };
  }
  let doc;
  try {
    doc = JSON.parse(text);
  } catch (e) {
    return { ok: false, error: "the feed is not valid JSON" };
  }
  if (doc === null || typeof doc !== "object" || Array.isArray(doc) || !Array.isArray(doc.models)) {
    return { ok: false, error: "the feed has no models list" };
  }
  if (doc.models.length > PRICE_FEED_MAX_ENTRIES) {
    return { ok: false, error: "the feed lists more than 5,000 models" };
  }
  const entries = [];
  for (const e of doc.models) {
    if (e === null || typeof e !== "object" || Array.isArray(e) || typeof e.model !== "string"
        || e.model.length < 1 || e.model.length > PRICE_FEED_MAX_NAME) continue;
    entries.push({
      model: e.model,
      creator: typeof e.creator === "string" ? e.creator : null,
      inputPrice: e.inputPrice,
      outputPrice: e.outputPrice,
    });
  }
  return {
    ok: true,
    lastUpdated: typeof doc.lastUpdated === "string" ? doc.lastUpdated.slice(0, 100) : null,
    entries,
    skipped: doc.models.length - entries.length,
  };
}

// Open-weight models are listed at 0 / 0, which means "no list price", not
// "free" — so 0 is unusable here even though the server accepts it.
const usableFeedPrice = (v) =>
  typeof v === "number" && Number.isFinite(v) && v > 0 && v <= PRICE_MAX_PER_MTOK;

function classifyModels(pageModels, entries) {
  // A Map, not an object: model names come from captured data and the feed,
  // and "constructor" or "__proto__" must be ordinary keys.
  const byName = new Map();
  for (const e of entries) {
    const key = normalizeModelName(e.model);
    if (!key) continue;
    if (!byName.has(key)) byName.set(key, []);
    byName.get(key).push(e);
  }
  return pageModels.map((m) => {
    const key = normalizeModelName(m.model);
    const matches = (key && byName.get(key)) || [];
    const current = m.price || null;
    const row = {
      model: m.model,
      current,
      feedNames: matches.map((e) => e.model),
      feedInput: null,
      feedOutput: null,
      changed: { input: false, output: false },
      status: "no-match",
      tickable: false,
    };
    if (matches.length > 1) {
      row.status = "ambiguous";
      return row;
    }
    if (matches.length === 0) return row;
    const e = matches[0];
    if (!usableFeedPrice(e.inputPrice) || !usableFeedPrice(e.outputPrice)) {
      row.status = "not-priced";
      return row;
    }
    row.feedInput = e.inputPrice;
    row.feedOutput = e.outputPrice;
    row.changed = {
      input: !current || current.input !== e.inputPrice,
      output: !current || current.output !== e.outputPrice,
    };
    row.status = row.changed.input || row.changed.output ? "importable" : "unchanged";
    row.tickable = row.status === "importable";
    return row;
  });
}

function parseCachePrice(raw) {
  if (typeof raw !== "string" || raw.trim() === "") return null;
  const v = Number(raw.trim());
  return Number.isFinite(v) && v >= 0 && v <= PRICE_MAX_PER_MTOK ? v : null;
}

function buildPriceBody(row, cacheInputs) {
  if (!row || row.status !== "importable") return { ok: false, fields: [] };
  const body = { model: row.model, input: row.feedInput, output: row.feedOutput };
  if (row.current) {
    for (const f of PRICE_CACHE_FIELDS) body[f] = row.current[f];
    return { ok: true, body };
  }
  // The feed has no cache prices; the admin types them. Never default them
  // (0, a copy of input, a provider ratio): a guessed price is worse than none.
  const fields = [];
  for (const f of PRICE_CACHE_FIELDS) {
    const v = parseCachePrice((cacheInputs || {})[f]);
    if (v === null) fields.push(f);
    else body[f] = v;
  }
  return fields.length ? { ok: false, fields } : { ok: true, body };
}

if (typeof module !== "undefined") {
  module.exports = { normalizeModelName, parseFeed, classifyModels, buildPriceBody };
}
