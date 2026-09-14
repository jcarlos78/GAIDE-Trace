"""Model usage over HTTP — specs/model-usage AC8, AC9, AC11, AC12, AC15–AC17,
AC19, AC21–AC23, AC26–AC28, AC30."""

import json
import math
import unittest
from urllib.parse import quote

from fixtures import assistant_lines, event, load_server, transcript, usage
from server_harness import ServerHarness

server = load_server()

DAY1 = "2026-09-01T10:00:00.000Z"
DAY2 = "2026-09-02T10:00:00.000Z"
DAY10 = "2026-09-10T10:00:00.000Z"

OPUS_PRICE = {"model": "claude-opus-5", "input": 5.0, "output": 25.0,
              "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10.0}


class ApiTestCase(unittest.TestCase):

    def setUp(self):
        self.h = ServerHarness()

    def tearDown(self):
        self.h.close()

    def get(self, path, role="member_user"):
        status, body = self.h.as_role(role, "GET", path)
        self.assertEqual(status, 200, body)
        return body

    def set_price(self, price, role="admin_user"):
        return self.h.as_role(role, "PUT", "/api/v1/models/prices", body=price)

    def seed(self):
        """s-mixed: opus x2 + sonnet x1 on day 1 (project p).
        s-sonnet: sonnet x1 on day 2 (project q).
        ag: Antigravity, gemini x3 via model.turn events on day 10 (project p)."""
        self.assertEqual(self.h.put_transcript("s-mixed", transcript(
            assistant_lines("claude-opus-5",
                            usage(input_tokens=1_000_000, output_tokens=200_000,
                                  cache_read=2_000_000, cache_write=300_000,
                                  cache_5m=100_000, cache_1h=200_000),
                            DAY1, blocks=3),
            assistant_lines("claude-opus-5", usage(output_tokens=100_000, speed="fast"), DAY1),
            assistant_lines("claude-sonnet-5", usage(output_tokens=10), DAY1),
        ), project="p"), 200)
        self.assertEqual(self.h.put_transcript("s-sonnet", transcript(
            assistant_lines("claude-sonnet-5", usage(output_tokens=40), DAY2),
        ), project="q"), 200)
        status, _ = self.h.post_events([
            event("ag", "model.turn", DAY10, source="antigravity", project="p",
                  model="gemini-3.7-flash") for _ in range(3)])
        self.assertEqual(status, 200)


class SessionsApi(ApiTestCase):

    def test_ac8_list_carries_dominant_model_and_count(self):
        self.seed()
        rows = {s["session_id"]: s for s in self.get("/api/v1/sessions")["sessions"]}
        self.assertEqual((rows["s-mixed"]["dominant_model"], rows["s-mixed"]["model_count"]),
                         ("claude-opus-5", 2))
        self.assertEqual((rows["ag"]["dominant_model"], rows["ag"]["model_count"]),
                         ("gemini-3.7-flash", 1))

    def test_ac8_detail_carries_per_model_breakdown_with_cost(self):
        self.seed()
        self.set_price(OPUS_PRICE)
        body = self.get("/api/v1/sessions/s-mixed")
        rows = {r["model"]: r for r in body["models"]}
        opus = rows["claude-opus-5"]
        self.assertEqual((opus["turns"], opus["output_tokens"], opus["premium_turns"],
                          opus["source"], opus["cost_status"]),
                         (2, 300_000, 1, "transcript", "priced"))
        self.assertIsNotNone(opus["cost"])
        self.assertEqual((rows["claude-sonnet-5"]["cost"], rows["claude-sonnet-5"]["cost_status"]),
                         (None, "unpriced"))
        total = body["models_total"]
        self.assertEqual((total["turns"], total["unpriced_models"], total["premium_turns"]),
                         (3, 1, 1))
        self.assertAlmostEqual(total["cost_total"], opus["cost"])

    def test_ac9_model_filter_matches_any_session_using_the_model(self):
        self.seed()
        found = {s["session_id"] for s in
                 self.get("/api/v1/sessions?model=claude-sonnet-5")["sessions"]}
        self.assertEqual(found, {"s-mixed", "s-sonnet"})  # dominant in only one

    def test_ac9_model_filter_combines_with_project(self):
        self.seed()
        body = self.get("/api/v1/sessions?model=claude-sonnet-5&project=q")
        self.assertEqual([s["session_id"] for s in body["sessions"]], ["s-sonnet"])
        self.assertEqual(body["total"], 1)


class OverviewApi(ApiTestCase):

    def test_ac11_per_model_rows_and_per_day_series(self):
        self.seed()
        models = self.get("/api/v1/overview")["models"]
        rows = {r["model"]: r for r in models["rows"]}
        self.assertEqual(set(rows), {"claude-opus-5", "claude-sonnet-5", "gemini-3.7-flash"})
        sonnet = rows["claude-sonnet-5"]
        self.assertEqual((sonnet["sessions"], sonnet["turns"], sonnet["output_tokens"]),
                         (2, 2, 50))
        self.assertAlmostEqual(sum(r["share"] for r in rows.values()), 1.0)
        self.assertAlmostEqual(rows["gemini-3.7-flash"]["share"], 3 / 7)
        per_day = {(d["day"], d["model"]): d["turns"] for d in models["per_day"]}
        self.assertEqual(per_day[("2026-09-01", "claude-opus-5")], 2)
        self.assertEqual(per_day[("2026-09-10", "gemini-3.7-flash")], 3)

    def test_ac12_window_filters_by_turn_time_not_session_time(self):
        self.h.put_transcript("long", transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=1), DAY1),
            assistant_lines("claude-opus-5", usage(output_tokens=2), DAY10),
        ))
        models = self.get("/api/v1/overview?from=2026-09-05")["models"]
        (row,) = models["rows"]
        self.assertEqual((row["turns"], row["output_tokens"]), (1, 2))
        self.assertEqual({d["day"] for d in models["per_day"]}, {"2026-09-10"})
        early = self.get("/api/v1/overview?to=2026-09-05")["models"]["rows"]
        self.assertEqual(early[0]["output_tokens"], 1)

    def test_project_filter_applies_to_model_rows(self):
        self.seed()
        rows = self.get("/api/v1/overview?project=q")["models"]["rows"]
        self.assertEqual([(r["model"], r["turns"]) for r in rows], [("claude-sonnet-5", 1)])

    def test_ac14_per_day_series_keeps_top_models_and_aggregates_the_rest(self):
        lines = []
        for i in range(7):  # model-0 has 7 turns ... model-6 has 1
            for _ in range(7 - i):
                lines += assistant_lines(f"model-{i}", usage(output_tokens=1), DAY1)
        self.h.put_transcript("many", transcript(lines))
        models = self.get("/api/v1/overview")["models"]
        self.assertEqual(len(models["rows"]), 7)
        series = {d["model"]: d["turns"] for d in models["per_day"]}
        top = {f"model-{i}" for i in range(server.CHART_SERIES)}
        self.assertEqual(set(series) - {None}, top)
        self.assertEqual(series[None], sum(7 - i for i in range(server.CHART_SERIES, 7)))
        self.assertEqual(models["series"], [f"model-{i}" for i in range(server.CHART_SERIES)])

    def test_ac14_chart_series_are_the_leaders_of_the_current_view(self):
        early = [assistant_lines(f"model-{i}", usage(output_tokens=1), DAY1)
                 for i in range(server.CHART_SERIES) for _ in range(10)]
        self.h.put_transcript("early", transcript(*early), project="p")
        self.h.put_transcript("late", transcript(
            *[assistant_lines("newcomer", usage(output_tokens=1), DAY10) for _ in range(3)]),
            project="q")
        everything = self.get("/api/v1/overview")["models"]
        self.assertEqual(everything["series"], [f"model-{i}" for i in range(server.CHART_SERIES)])
        for path in ("/api/v1/overview?from=2026-09-05", "/api/v1/overview?project=q"):
            view = self.get(path)["models"]
            self.assertEqual(view["series"], ["newcomer"], path)
            self.assertEqual(view["per_day"],
                             [{"day": "2026-09-10", "model": "newcomer", "turns": 3}], path)

    def test_model_rows_are_capped_with_an_omitted_count(self):
        extra = 7
        n = server.MAX_MODEL_ROWS + extra
        self.h.put_transcript("flood", transcript(
            *[assistant_lines(f"flood-{i:04d}", usage(output_tokens=1), DAY1) for i in range(n)]))
        self.set_price({**OPUS_PRICE, "model": "priced-but-unseen"})
        models = self.get("/api/v1/overview")["models"]
        self.assertEqual((len(models["rows"]), models["models_omitted"]),
                         (server.MAX_MODEL_ROWS, extra))
        self.assertEqual((models["turns"], models["output_tokens"]), (n, n))
        listed = self.get("/api/v1/models")
        self.assertEqual(listed["omitted"], extra)
        names = {m["model"] for m in listed["models"]}
        self.assertEqual(len(names), server.MAX_MODEL_ROWS + 1)
        self.assertIn("priced-but-unseen", names)

        # Pricing a model outside the top rows lists its real usage, and it is
        # no longer counted as omitted.
        outside = f"flood-{n - 1:04d}"
        self.assertNotIn(outside, names)
        self.set_price({**OPUS_PRICE, "model": outside})
        listed = self.get("/api/v1/models")
        entry = {m["model"]: m for m in listed["models"]}[outside]
        self.assertEqual((entry["turns"], entry["sessions"]), (1, 1))
        self.assertEqual(listed["omitted"], extra - 1)

    def test_token_sums_beyond_64_bits_do_not_take_the_overview_down(self):
        # One 256 MB upload can legally hold ~3.8e18 tokens; three of them sum
        # past SQLite's INTEGER range, where SUM() raises instead of wrapping.
        near_max = 3_800_000_000_000_000_000
        with self.h.store.connect() as db:
            for sid in ("big-1", "big-2", "big-3"):
                db.execute("""INSERT INTO sessions (session_id, project, first_ts, last_ts,
                                input_tokens, output_tokens, cache_read_tokens,
                                cache_creation_tokens) VALUES (?,?,?,?,?,?,?,?)""",
                           (sid, "p", DAY1, DAY1, near_max, near_max, near_max, near_max))
                db.execute("""INSERT INTO model_turns (session_id, turn_key, model, source, ts,
                                input_tokens, output_tokens, cache_read_tokens,
                                cache_write_5m_tokens, cache_write_1h_tokens)
                              VALUES (?,?,?,?,?,?,?,?,?,?)""",
                           (sid, "k", "claude-opus-5", "transcript", DAY1,
                            near_max, near_max, near_max, near_max, 0))
            db.commit()
        body = self.get("/api/v1/overview")
        (row,) = body["models"]["rows"]
        self.assertAlmostEqual(row["output_tokens"], 3 * near_max, delta=near_max * 1e-9)
        self.assertAlmostEqual(body["totals"]["output_tokens"], 3 * near_max, delta=near_max * 1e-9)
        self.assertEqual(self.get("/api/v1/sessions/big-1")["models"][0]["turns"], 1)

    def test_session_detail_model_rows_are_capped_with_an_omitted_count(self):
        n = server.MAX_MODEL_ROWS + 3
        self.h.put_transcript("flood", transcript(
            *[assistant_lines(f"flood-{i:04d}", usage(output_tokens=1), DAY1) for i in range(n)]))
        body = self.get("/api/v1/sessions/flood")
        self.assertEqual((len(body["models"]), body["models_omitted"]), (server.MAX_MODEL_ROWS, 3))
        self.assertEqual(body["models_total"]["turns"], n)

    def test_empty_window_has_empty_model_block(self):
        models = self.get("/api/v1/overview")["models"]
        self.assertEqual((models["rows"], models["per_day"], models["cost_total"]), ([], [], None))


class CostApi(ApiTestCase):

    def test_ac15_cost_formula_is_exact_and_unrounded(self):
        self.seed()
        self.set_price(OPUS_PRICE)
        rows = {r["model"]: r for r in self.get("/api/v1/overview")["models"]["rows"]}
        expected = (1_000_000 * 5.0 + 300_000 * 25.0 + 2_000_000 * 0.5
                    + 100_000 * 6.25 + 200_000 * 10.0) / 1_000_000
        self.assertEqual(rows["claude-opus-5"]["cost"], expected)

    def test_ac15_odd_prices_are_not_rounded(self):
        self.h.put_transcript("s", transcript(
            assistant_lines("m", usage(output_tokens=1), DAY1)))
        self.set_price({"model": "m", "input": 0, "output": 1 / 3, "cache_read": 0,
                        "cache_write_5m": 0, "cache_write_1h": 0})
        (row,) = self.get("/api/v1/overview")["models"]["rows"]
        self.assertEqual(row["cost"], (1 / 3) / 1_000_000)

    def test_ac16_unpriced_models_are_null_and_excluded_from_totals(self):
        self.seed()
        self.set_price(OPUS_PRICE)
        models = self.get("/api/v1/overview")["models"]
        rows = {r["model"]: r for r in models["rows"]}
        self.assertIsNone(rows["claude-sonnet-5"]["cost"])
        self.assertEqual(rows["claude-sonnet-5"]["cost_status"], "unpriced")
        self.assertEqual(models["cost_total"], rows["claude-opus-5"]["cost"])
        self.assertEqual(models["unpriced_models"], 1)

    def test_ac17_models_without_token_data_are_not_counted_as_unpriced(self):
        self.seed()
        self.set_price({**OPUS_PRICE, "model": "gemini-3.7-flash"})
        models = self.get("/api/v1/overview")["models"]
        gemini = {r["model"]: r for r in models["rows"]}["gemini-3.7-flash"]
        self.assertEqual((gemini["cost"], gemini["cost_status"], gemini["turns_without_tokens"]),
                         (None, "no_tokens", 3))
        self.assertIsNone(gemini["output_tokens"])
        self.assertEqual(models["no_token_models"], 1)
        self.assertEqual(models["unpriced_models"], 2)  # opus + sonnet, not gemini

    def test_ac19_premium_speed_turns_are_reported(self):
        self.seed()
        models = self.get("/api/v1/overview")["models"]
        self.assertEqual(models["premium_turns"], 1)
        rows = {r["model"]: r for r in models["rows"]}
        self.assertEqual(rows["claude-opus-5"]["premium_turns"], 1)

    def test_premium_turns_bound_the_estimate_only_when_priced(self):
        self.seed()
        self.assertEqual(self.get("/api/v1/overview")["models"]["premium_priced_turns"], 0)
        self.set_price(OPUS_PRICE)
        self.assertEqual(self.get("/api/v1/overview")["models"]["premium_priced_turns"], 1)


class PricesApi(ApiTestCase):

    def test_ac21_lists_seen_and_priced_models(self):
        self.seed()
        self.set_price({**OPUS_PRICE, "model": "not-seen-yet"})
        self.set_price(OPUS_PRICE)
        listed = {m["model"]: m for m in self.get("/api/v1/models")["models"]}
        self.assertEqual(set(listed), {"claude-opus-5", "claude-sonnet-5",
                                       "gemini-3.7-flash", "not-seen-yet"})
        self.assertIsNone(listed["claude-sonnet-5"]["price"])
        self.assertEqual(listed["not-seen-yet"]["turns"], 0)
        self.assertEqual(listed["claude-opus-5"]["turns"], 2)
        self.assertEqual(listed["claude-opus-5"]["price"]["output"], 25.0)

    def test_ac21_admin_can_update_and_clear_a_price(self):
        self.assertEqual(self.set_price(OPUS_PRICE)[0], 200)
        self.assertEqual(self.set_price({**OPUS_PRICE, "output": 30})[0], 200)
        price = {m["model"]: m for m in self.get("/api/v1/models")["models"]}["claude-opus-5"]["price"]
        self.assertEqual(price["output"], 30)
        status, _ = self.h.as_role("admin_user", "DELETE",
                                   "/api/v1/models/prices?model=" + quote("claude-opus-5"))
        self.assertEqual(status, 200)
        self.assertEqual(self.get("/api/v1/models")["models"], [])

    def test_clearing_an_unknown_price_is_404(self):
        status, _ = self.h.as_role("admin_user", "DELETE", "/api/v1/models/prices?model=nope")
        self.assertEqual(status, 404)

    def test_model_names_with_url_characters_round_trip(self):
        name = "vendor/model:v1@2026"
        self.assertEqual(self.set_price({**OPUS_PRICE, "model": name})[0], 200)
        self.assertIn(name, {m["model"] for m in self.get("/api/v1/models")["models"]})
        status, _ = self.h.as_role("admin_user", "DELETE",
                                   "/api/v1/models/prices?model=" + quote(name, safe=""))
        self.assertEqual(status, 200)

    def test_ac22_records_who_changed_a_price_and_when(self):
        self.set_price(OPUS_PRICE)
        price = self.get("/api/v1/models")["models"][0]["price"]
        self.assertEqual(price["updated_by"], "root")
        self.assertTrue(price["updated_at"].startswith("20"))

    def test_ac23_prices_survive_rebuild_index_and_restart(self):
        self.seed()
        self.set_price(OPUS_PRICE)
        self.h.store.rebuild_index()
        restarted = server.Store(self.h.data_dir)
        with restarted.connect() as db:
            row = db.execute("SELECT output FROM model_prices WHERE model = ?",
                             ("claude-opus-5",)).fetchone()
        self.assertEqual(row["output"], 25.0)


class PricesAuthorization(ApiTestCase):
    """AC26, AC27 — negative criteria."""

    def price_table(self):
        with self.h.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM model_prices ORDER BY model")]

    def test_ac26_non_admins_cannot_change_prices(self):
        self.set_price(OPUS_PRICE)
        before = self.price_table()
        for role in ("member_user", "member_key", "agent_key"):
            for method, path, body in (
                    ("PUT", "/api/v1/models/prices", {**OPUS_PRICE, "output": 0}),
                    ("PUT", "/api/v1/models/prices", {**OPUS_PRICE, "model": "new-model"}),
                    ("DELETE", "/api/v1/models/prices?model=claude-opus-5", None)):
                status, _ = self.h.as_role(role, method, path, body=body)
                self.assertEqual(status, 403, (role, method, body))
        status, _ = self.h.request("PUT", "/api/v1/models/prices", body=OPUS_PRICE)
        self.assertEqual(status, 401)
        self.assertEqual(self.price_table(), before)

    def test_admin_key_can_change_prices(self):
        self.assertEqual(self.set_price(OPUS_PRICE, role="admin_key")[0], 200)

    def test_ac27_agent_key_cannot_read_model_data(self):
        self.seed()
        for path in ("/api/v1/models", "/api/v1/overview", "/api/v1/sessions",
                     "/api/v1/sessions/s-mixed"):
            status, _ = self.h.as_role("agent_key", "GET", path)
            self.assertEqual(status, 403, path)


class PriceValidation(ApiTestCase):
    """AC28 — negative criteria."""

    def assert_rejected(self, payload, field):
        status, body = self.set_price(payload)
        self.assertEqual(status, 400, payload)
        self.assertIn(field, body["error"])
        self.assertEqual(self.get("/api/v1/models")["models"], [])

    def test_ac28_negative_price(self):
        self.assert_rejected({**OPUS_PRICE, "input": -1}, "input")

    def test_ac28_non_numeric_price(self):
        self.assert_rejected({**OPUS_PRICE, "output": "25"}, "output")
        self.assert_rejected({**OPUS_PRICE, "output": True}, "output")
        self.assert_rejected({**OPUS_PRICE, "output": None}, "output")

    def test_ac28_non_finite_price(self):
        for bad in (math.nan, math.inf):
            status, body = self.h.raw_request(
                "PUT", "/api/v1/models/prices", self.h.tokens["admin_user"],
                json.dumps({**OPUS_PRICE, "cache_read": bad}).encode())
            self.assertEqual(status, 400, bad)
            self.assertIn(b"cache_read", body)

    def test_ac28_price_above_ceiling(self):
        self.assert_rejected({**OPUS_PRICE, "cache_write_1h": server.MAX_PRICE_PER_MTOK + 1},
                             "cache_write_1h")

    def test_ac28_huge_integer_price(self):
        self.assert_rejected({**OPUS_PRICE, "cache_write_1h": 10 ** 400}, "cache_write_1h")

    def test_ac28_oversized_number_literal_is_a_400_not_a_dropped_connection(self):
        body = b'{"model": "m", "input": 1' + b"0" * 5000 + b"}"
        status, _ = self.h.raw_request("PUT", "/api/v1/models/prices",
                                       self.h.tokens["admin_user"], body)
        self.assertEqual(status, 400)

    def test_ac33_unparseable_event_batches_are_a_400(self):
        for body in (b'[{"trace_id": "t", "ts": 1' + b"0" * 5000 + b"}]", b"[" * 100_000):
            status, _ = self.h.raw_request("POST", "/api/v1/events",
                                           self.h.tokens["agent_key"], body)
            self.assertEqual(status, 400)

    def test_ac33_non_string_event_fields_do_not_fail_the_batch(self):
        status, body = self.h.post_events([
            event("odd", "prompt.submit", DAY1, prompt="kept"),
            {"trace_id": "t-odd", "ts": 10 ** 30, "event": {"nested": True},
             "session_id": "odd", "model": 5, "prompt": ["a", "list"]}])
        self.assertEqual((status, body["inserted"]), (200, 2))
        detail = self.get("/api/v1/sessions/odd")
        self.assertEqual(len(detail["events"]), 2)

    def test_ac33_transcript_lines_that_break_the_json_parser_do_not_fail_the_upload(self):
        data = (b'{"message": {"model": "m", "id": "a", "usage": {"output_tokens": 1'
                + b"0" * 5000 + b"}}}\n" + b"[" * 100_000 + b"\n"
                + transcript(assistant_lines("claude-opus-5", usage(output_tokens=7), DAY1)))
        self.assertEqual(self.h.put_transcript("hostile-json", data), 200)
        rows = self.get("/api/v1/sessions/hostile-json")["models"]
        self.assertEqual([(r["model"], r["output_tokens"]) for r in rows], [("claude-opus-5", 7)])

    def test_clearing_with_an_overlong_model_name_is_rejected(self):
        status, _ = self.h.as_role("admin_user", "DELETE",
                                   "/api/v1/models/prices?model=" + "x" * 5000)
        self.assertEqual(status, 400)

    def test_ac28_missing_price_field(self):
        payload = dict(OPUS_PRICE)
        del payload["cache_write_5m"]
        self.assert_rejected(payload, "cache_write_5m")

    def test_ceiling_itself_and_zero_are_accepted(self):
        status, _ = self.set_price({**OPUS_PRICE, "input": 0,
                                    "output": server.MAX_PRICE_PER_MTOK})
        self.assertEqual(status, 200)

    def test_ac30_bad_model_names_are_rejected(self):
        for bad in ("", "x" * (server.MAX_MODEL_NAME + 1), "<synthetic>", 5, None):
            status, body = self.set_price({**OPUS_PRICE, "model": bad})
            self.assertEqual(status, 400, bad)
            self.assertIn("model", body["error"])

    def test_invalid_json_is_rejected(self):
        status, _ = self.h.raw_request("PUT", "/api/v1/models/prices",
                                       self.h.tokens["admin_user"], b"{nope")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
