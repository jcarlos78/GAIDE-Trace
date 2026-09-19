"""Price import from the benchlm.ai feed (specs/price-import): matching,
statuses, feed validation and PUT bodies.

The logic ships as browser JS (server/webui/price-import.js), so these tests
run that exact file under Node rather than a Python copy of it (plan D-b).
Node is a test-time tool only; without it the tests skip and say why.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / "server" / "webui" / "price-import.js"
NODE = shutil.which("node")

# Reads a list of calls as JSON on stdin and prints one result per call.
# "classifyFeed" parses feed text and classifies against it in one step, so
# values JSON can't carry (1e309 -> Infinity) reach the code the way a real
# feed would deliver them.
HARNESS = r"""
const m = require(process.argv[1]);
let input = "";
process.stdin.on("data", (c) => { input += c; });
process.stdin.on("end", () => {
  const out = JSON.parse(input).map((c) => {
    try {
      if (c.fn === "classifyFeed") {
        const feed = m.parseFeed(c.args[1]);
        if (!feed.ok) throw new Error("feed refused: " + feed.error);
        return { value: m.classifyModels(c.args[0], feed.entries) };
      }
      return { value: m[c.fn](...c.args) };
    } catch (e) {
      return { error: String(e && e.message) };
    }
  });
  process.stdout.write(JSON.stringify(out));
});
"""


def run_js(calls):
    proc = subprocess.run([NODE, "-e", HARNESS, str(MODULE)], input=json.dumps(calls),
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node harness failed: {proc.stderr}")
    return json.loads(proc.stdout)


def feed(*entries, last_updated="September 15, 2026"):
    return json.dumps({"lastUpdated": last_updated, "models": list(entries)})


def entry(name, input_price=5, output_price=25, **extra):
    return {"model": name, "creator": "Some Lab", "inputPrice": input_price,
            "outputPrice": output_price, "contextWindow": 200000, "sourceType": "api", **extra}


def page_model(name, price=None):
    return {"model": name, "turns": 3, "last_ts": "2026-09-15T10:00:00+00:00", "price": price}


def price(input=5, output=25, cache_read=0.5, cache_write_5m=6.25, cache_write_1h=10):
    return {"input": input, "output": output, "cache_read": cache_read,
            "cache_write_5m": cache_write_5m, "cache_write_1h": cache_write_1h,
            "updated_at": "2026-09-14T10:00:00+00:00", "updated_by": "alice"}


@unittest.skipIf(NODE is None, "node is not on PATH: the price-import logic is browser JS and "
                               "these tests run it under Node (specs/price-import plan D-b)")
class JSTestCase(unittest.TestCase):

    def call(self, fn, *args):
        [result] = run_js([{"fn": fn, "args": list(args)}])
        if "error" in result:
            raise AssertionError(f"{fn} raised: {result['error']}")
        return result["value"]

    def classify(self, page, *entries):
        return self.call("classifyFeed", page, feed(*entries))

    def one_row(self, model, *entries, current=None):
        [row] = self.classify([page_model(model, current)], *entries)
        return row


class Harness(JSTestCase):

    def test_harness_reports_errors_as_data(self):
        [result] = run_js([{"fn": "noSuchFunction", "args": []}])
        self.assertIn("error", result)


class Normalization(JSTestCase):
    """AC4 — the normalized-name rule from the spec's Terms."""

    CASES = [
        ("Claude Opus 5", "claude-opus-5"),
        ("claude-opus-5", "claude-opus-5"),
        ("Gemini 3.7 Flash", "gemini-3-7-flash"),
        ("gemini-3.7-flash", "gemini-3-7-flash"),
        ("Claude Opus 4.5", "claude-opus-4-5"),
        ("claude-opus-4-5-20251101", "claude-opus-4-5"),
        ("Claude Opus 4.7 (Adaptive)", "claude-opus-4-7-adaptive"),
        ("  --Weird__Name!! ", "weird-name"),
        ("model-2025110", "model-2025110"),        # 7 digits: not a date suffix
        ("model-202511012", "model-202511012"),    # 9 digits: not a date suffix
        ("20251101", "20251101"),                  # no separator: the whole name
    ]

    def test_cases(self):
        results = run_js([{"fn": "normalizeModelName", "args": [raw]} for raw, _ in self.CASES])
        for (raw, expected), result in zip(self.CASES, results):
            with self.subTest(raw=raw):
                self.assertEqual(result.get("value"), expected, result)


class Matching(JSTestCase):
    """AC4 — exact normalized match against exactly one feed entry."""

    def test_display_name_matches_api_id(self):
        row = self.one_row("claude-opus-5", entry("Claude Opus 5"))
        self.assertEqual((row["status"], row["feedNames"]), ("importable", ["Claude Opus 5"]))

    def test_dotted_version_matches_hyphenated(self):
        row = self.one_row("gemini-3.7-flash", entry("Gemini 3.7 Flash"))
        self.assertEqual(row["status"], "importable")

    def test_dated_suffix_matches(self):
        row = self.one_row("claude-opus-4-5-20251101", entry("Claude Opus 4.5"))
        self.assertEqual(row["status"], "importable")

    def test_two_feed_entries_with_one_name_are_ambiguous(self):
        row = self.one_row("claude-opus-5", entry("Claude Opus 5"), entry("claude-opus-5", 6, 30))
        self.assertEqual(row["status"], "ambiguous")
        self.assertEqual(sorted(row["feedNames"]), ["Claude Opus 5", "claude-opus-5"])
        self.assertFalse(row["tickable"])

    def test_suffixed_variant_does_not_match_its_base(self):
        row = self.one_row("claude-opus-4-7", entry("Claude Opus 4.7 (Adaptive)"))
        self.assertEqual((row["status"], row["feedNames"]), ("no-match", []))

    def test_base_still_matches_next_to_its_variant(self):
        row = self.one_row("claude-opus-4-7", entry("Claude Opus 4.7 (Adaptive)", 9, 9),
                           entry("Claude Opus 4.7"))
        self.assertEqual((row["status"], row["feedNames"]), ("importable", ["Claude Opus 4.7"]))

    def test_names_that_normalize_to_nothing_never_match(self):
        row = self.one_row("???", entry("!!!"))
        self.assertEqual(row["status"], "no-match")

    def test_object_prototype_names_are_ordinary_names(self):
        rows = self.classify([page_model("constructor"), page_model("__proto__"),
                              page_model("hasOwnProperty")], entry("Claude Opus 5"))
        self.assertEqual([r["status"] for r in rows], ["no-match"] * 3)


class Statuses(JSTestCase):
    """AC3 — one status per page model; AC5 — changed values; D-e — tickable."""

    def test_every_page_model_gets_one_row_in_order(self):
        rows = self.classify([page_model("b-model"), page_model("a-model"), page_model("c")],
                             entry("A Model"))
        self.assertEqual([r["model"] for r in rows], ["b-model", "a-model", "c"])

    def test_unpriced_model_with_feed_price_is_importable(self):
        row = self.one_row("claude-opus-5", entry("Claude Opus 5", 5, 25))
        self.assertEqual((row["status"], row["tickable"]), ("importable", True))
        self.assertEqual((row["feedInput"], row["feedOutput"]), (5, 25))
        self.assertEqual(row["changed"], {"input": True, "output": True})

    def test_priced_model_with_different_prices_is_importable(self):
        row = self.one_row("claude-opus-5", entry("Claude Opus 5", 4, 20), current=price())
        self.assertEqual((row["status"], row["tickable"]), ("importable", True))
        self.assertEqual(row["changed"], {"input": True, "output": True})

    def test_only_the_changed_value_is_flagged(self):
        row = self.one_row("claude-opus-5", entry("Claude Opus 5", 5, 30), current=price())
        self.assertEqual(row["changed"], {"input": False, "output": True})

    def test_equal_prices_are_unchanged_and_not_tickable(self):
        row = self.one_row("claude-opus-5", entry("Claude Opus 5", 5, 25), current=price())
        self.assertEqual((row["status"], row["tickable"]), ("unchanged", False))

    def test_no_match(self):
        row = self.one_row("claude-opus-5", entry("GPT 9"))
        self.assertEqual((row["status"], row["tickable"]), ("no-match", False))

    def test_upper_bound_is_importable(self):
        row = self.one_row("m", entry("M", 10000, 10000))
        self.assertEqual(row["status"], "importable")

    def test_unusable_feed_prices_are_not_priced(self):
        bad = {"null": None, "zero": 0, "negative": -1, "string": "5",
               "over limit": 10001, "bool": True}
        for label, value in bad.items():
            for field in ("input", "output"):
                with self.subTest(value=label, field=field):
                    prices = (value, 25) if field == "input" else (5, value)
                    row = self.one_row("claude-opus-5", entry("Claude Opus 5", *prices))
                    self.assertEqual((row["status"], row["tickable"]), ("not-priced", False))
                    self.assertEqual(row["feedNames"], ["Claude Opus 5"])

    def test_non_finite_feed_price_is_not_priced(self):
        text = feed(entry("Claude Opus 5")).replace('"inputPrice": 5', '"inputPrice": 1e309')
        [row] = self.call("classifyFeed", [page_model("claude-opus-5")], text)
        self.assertEqual(row["status"], "not-priced")

    def test_missing_feed_price_is_not_priced(self):
        e = entry("Claude Opus 5")
        del e["outputPrice"]
        row = self.one_row("claude-opus-5", e)
        self.assertEqual(row["status"], "not-priced")


class FeedEntries(JSTestCase):
    """AC13 — malformed entries are skipped, never break the preview."""

    def test_malformed_entries_are_skipped_and_siblings_survive(self):
        text = feed(1, "x", None, [], {}, {"model": ""}, {"model": "x" * 129}, {"model": 5},
                    {"model": ["Claude Opus 5"]}, entry("Claude Opus 5"))
        result = self.call("parseFeed", text)
        self.assertTrue(result["ok"], result)
        self.assertEqual([e["model"] for e in result["entries"]], ["Claude Opus 5"])
        self.assertEqual(result["skipped"], 9)
        [row] = self.call("classifyFeed", [page_model("claude-opus-5")], text)
        self.assertEqual(row["status"], "importable")

    def test_128_character_name_is_kept(self):
        result = self.call("parseFeed", feed(entry("x" * 128)))
        self.assertEqual(len(result["entries"]), 1)

    def test_last_updated_is_kept_only_as_a_string(self):
        self.assertEqual(self.call("parseFeed", feed(last_updated="Sep 15"))["lastUpdated"],
                         "Sep 15")
        for value in (None, 5, {"a": 1}):
            with self.subTest(value=value):
                self.assertIsNone(self.call("parseFeed", feed(last_updated=value))["lastUpdated"])


class FeedDocument(JSTestCase):
    """AC12 (parse side) and AC15 — documents that are refused whole."""

    def assert_refused(self, text, reason):
        result = self.call("parseFeed", text)
        self.assertFalse(result["ok"], result)
        self.assertIn(reason, result["error"])

    def test_invalid_json(self):
        self.assert_refused("<html>not json</html>", "JSON")

    def test_deeply_nested_json(self):
        # Well-formed, so JSON.parse may recurse all the way down; whatever it
        # does, parseFeed must answer with a refusal, not let an error escape.
        result = self.call("parseFeed", "[" * 100_000 + "]" * 100_000)
        self.assertFalse(result["ok"])

    def test_top_level_not_an_object(self):
        for text in ("[]", "null", '"models"', "5"):
            with self.subTest(text=text):
                self.assert_refused(text, "models")

    def test_models_missing_or_not_an_array(self):
        for text in ("{}", '{"models": {}}', '{"models": "x"}', '{"models": null}'):
            with self.subTest(text=text):
                self.assert_refused(text, "models")

    def test_more_than_5000_entries_is_refused(self):
        self.assert_refused(feed(*[entry(f"m{i}") for i in range(5001)]), "5,000")

    def test_5000_entries_are_accepted(self):
        result = self.call("parseFeed", feed(*[entry(f"m{i}") for i in range(5000)]))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["entries"]), 5000)

    def test_oversized_document_is_refused_before_parsing(self):
        text = feed(entry("m", note="x" * (5 * 1024 * 1024)))
        self.assert_refused(text, "MB")


class PriceBody(JSTestCase):
    """AC8 / AC9 — what Apply sends, and what blocks it."""

    def row(self, current=None):
        return self.one_row("claude-opus-5-20260101", entry("Claude Opus 5", 4, 20),
                            current=current)

    def test_priced_row_keeps_its_cache_prices(self):
        result = self.call("buildPriceBody", self.row(price()), {})
        self.assertEqual(result, {"ok": True, "body": {
            "model": "claude-opus-5-20260101", "input": 4, "output": 20,
            "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10}})

    def test_priced_row_ignores_cache_inputs(self):
        result = self.call("buildPriceBody", self.row(price()),
                           {"cache_read": "99", "cache_write_5m": "99", "cache_write_1h": "99"})
        self.assertEqual(result["body"]["cache_read"], 0.5)

    def test_unpriced_row_without_cache_inputs_lists_all_three(self):
        for inputs in ({}, {"cache_read": "", "cache_write_5m": "", "cache_write_1h": ""}):
            with self.subTest(inputs=inputs):
                result = self.call("buildPriceBody", self.row(), inputs)
                self.assertEqual(result, {"ok": False, "fields":
                                          ["cache_read", "cache_write_5m", "cache_write_1h"]})

    def test_unpriced_row_rejects_invalid_cache_inputs(self):
        for bad in ("-1", "abc", " ", "10001", "Infinity", "1e400"):
            with self.subTest(value=bad):
                result = self.call("buildPriceBody", self.row(), {
                    "cache_read": bad, "cache_write_5m": "6.25", "cache_write_1h": "10"})
                self.assertEqual(result, {"ok": False, "fields": ["cache_read"]})

    def test_unpriced_row_with_valid_cache_inputs_builds_a_body(self):
        result = self.call("buildPriceBody", self.row(), {
            "cache_read": "0", "cache_write_5m": " 6.25 ", "cache_write_1h": "10"})
        self.assertEqual(result, {"ok": True, "body": {
            "model": "claude-opus-5-20260101", "input": 4, "output": 20,
            "cache_read": 0, "cache_write_5m": 6.25, "cache_write_1h": 10}})

    def test_rows_that_are_not_importable_build_no_body(self):
        unchanged = self.one_row("claude-opus-5", entry("Claude Opus 5"), current=price())
        no_match = self.one_row("claude-opus-5", entry("GPT 9"))
        for row in (unchanged, no_match):
            with self.subTest(status=row["status"]):
                self.assertFalse(self.call("buildPriceBody", row, {})["ok"])


if __name__ == "__main__":
    unittest.main()
