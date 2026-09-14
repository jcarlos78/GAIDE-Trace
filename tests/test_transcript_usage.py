"""Transcript token accounting — specs/model-usage AC1–AC3."""

import io
import unittest

from fixtures import assistant_lines, load_server, transcript, usage, user_line

server = load_server()

T0 = "2026-09-01T10:00:00.000Z"


class PerMessageCounting(unittest.TestCase):

    def test_ac1_message_split_across_lines_counts_once(self):
        u = usage(input_tokens=10, output_tokens=500, cache_read=2000, cache_write=300)
        data = transcript(
            user_line("hi", T0),
            assistant_lines("claude-opus-5", u, T0, blocks=4),
        )
        stats = server.summarize_transcript(data)
        self.assertEqual(stats["input_tokens"], 10)
        self.assertEqual(stats["output_tokens"], 500)
        self.assertEqual(stats["cache_read_tokens"], 2000)
        self.assertEqual(stats["cache_creation_tokens"], 300)

    def test_ac1_distinct_messages_are_summed(self):
        data = transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=100), T0, blocks=3),
            assistant_lines("claude-opus-5", usage(output_tokens=40), T0, blocks=2),
        )
        self.assertEqual(server.summarize_transcript(data)["output_tokens"], 140)

    def test_ac2_messages_without_id_count_once_per_line(self):
        data = transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=7), T0,
                            msg_id=None, blocks=3),
        )
        self.assertEqual(server.summarize_transcript(data)["output_tokens"], 21)

    def test_ac3_placeholder_model_contributes_nothing(self):
        data = transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=100), T0),
            assistant_lines("<synthetic>", usage(input_tokens=5, output_tokens=9), T0),
        )
        stats = server.summarize_transcript(data)
        self.assertEqual(stats["output_tokens"], 100)
        self.assertEqual(stats["input_tokens"], 0)
        self.assertEqual(stats["models"], "claude-opus-5")

    def test_ac3_only_placeholder_means_no_models(self):
        data = transcript(assistant_lines("<synthetic>", usage(), T0))
        self.assertIsNone(server.summarize_transcript(data)["models"])

    def test_malformed_lines_are_skipped_but_counted_as_lines(self):
        data = transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=3), T0),
            malformed=True,
        )
        stats = server.summarize_transcript(data)
        self.assertEqual(stats["output_tokens"], 3)
        self.assertEqual(stats["lines"], 2)


class DefensiveParsing(unittest.TestCase):
    """The transcript format is an external contract; odd input must never
    fail an upload or corrupt totals."""

    def test_json_lines_that_are_not_objects_are_skipped(self):
        data = b"5\n[]\nnull\n\"text\"\n" + transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=4), T0))
        stats = server.summarize_transcript(data)
        self.assertEqual(stats["output_tokens"], 4)
        self.assertEqual(stats["lines"], 5)

    def test_non_integer_usage_values_count_as_zero(self):
        bad = {"input_tokens": "12", "output_tokens": True,
               "cache_read_input_tokens": -40, "cache_creation_input_tokens": 3.5}
        data = transcript(
            assistant_lines("claude-opus-5", bad, T0),
            assistant_lines("claude-opus-5", usage(input_tokens=1, output_tokens=2), T0),
        )
        stats = server.summarize_transcript(data)
        self.assertEqual((stats["input_tokens"], stats["output_tokens"],
                          stats["cache_read_tokens"], stats["cache_creation_tokens"]),
                         (1, 2, 0, 0))

    def test_non_dict_usage_counts_as_zero(self):
        lines = assistant_lines("claude-opus-5", usage(), T0)
        lines[0]["message"]["usage"] = "oops"
        self.assertEqual(server.summarize_transcript(transcript(lines))["output_tokens"], 0)

    def test_first_line_of_a_message_wins_if_repeats_disagree(self):
        # Observed Claude Code behaviour is identical usage on every line of a
        # message; this pins what happens if that ever changes.
        first = assistant_lines("claude-opus-5", usage(output_tokens=10), T0, msg_id="m1")
        later = assistant_lines("claude-opus-5", usage(output_tokens=99), T0, msg_id="m1")
        stats = server.summarize_transcript(transcript(first, later))
        self.assertEqual(stats["output_tokens"], 10)

    def test_usage_on_a_message_without_model_is_not_counted(self):
        lines = assistant_lines("claude-opus-5", usage(output_tokens=50), T0)
        del lines[0]["message"]["model"]
        data = transcript(lines,
                          assistant_lines("claude-opus-5", usage(output_tokens=5), T0))
        stats = server.summarize_transcript(data)
        self.assertEqual(stats["output_tokens"], 5)

    def test_file_object_and_bytes_give_the_same_result(self):
        data = transcript(
            user_line("hi", T0),
            assistant_lines("claude-opus-5", usage(output_tokens=8), T0, blocks=2),
            assistant_lines("claude-sonnet-5", usage(output_tokens=3), T0, msg_id=None),
            malformed=True,
        )
        self.assertEqual(server.summarize_transcript(data),
                         server.summarize_transcript(io.BytesIO(data)))



class TurnExtraction(unittest.TestCase):
    """Per-turn attribution that model statistics are built on (AC4, AC18,
    AC19, AC30)."""

    def turns(self, *groups):
        return server.transcript_turns(transcript(*groups))[0]

    def test_one_turn_per_distinct_message_with_its_model(self):
        turns = self.turns(
            assistant_lines("claude-opus-5", usage(output_tokens=5), T0, msg_id="a", blocks=3),
            assistant_lines("claude-sonnet-5", usage(output_tokens=7), T0, msg_id="b", blocks=2),
        )
        self.assertEqual([(t["key"], t["model"], t["output_tokens"]) for t in turns],
                         [("a", "claude-opus-5", 5), ("b", "claude-sonnet-5", 7)])

    def test_turn_timestamp_is_normalized_to_utc_iso(self):
        (turn,) = self.turns(assistant_lines("claude-opus-5", usage(), "2026-09-01T10:00:00.250Z"))
        self.assertEqual(turn["ts"], "2026-09-01T10:00:00.250000+00:00")

    def test_cache_writes_use_the_tier_split_when_present(self):
        (turn,) = self.turns(assistant_lines(
            "claude-opus-5", usage(cache_write=300, cache_5m=100, cache_1h=200), T0))
        self.assertEqual((turn["cache_write_5m_tokens"], turn["cache_write_1h_tokens"]), (100, 200))

    def test_cache_writes_without_a_split_count_as_5m(self):
        (turn,) = self.turns(assistant_lines("claude-opus-5", usage(cache_write=300), T0))
        self.assertEqual((turn["cache_write_5m_tokens"], turn["cache_write_1h_tokens"]), (300, 0))

    def test_premium_speed_is_flagged(self):
        turns = self.turns(
            assistant_lines("claude-opus-5", usage(speed="fast"), T0),
            assistant_lines("claude-opus-5", usage(speed="standard"), T0),
            assistant_lines("claude-opus-5", usage(speed=None), T0),
        )
        self.assertEqual([t["premium"] for t in turns], [True, False, False])

    def test_out_of_range_timestamps_do_not_raise(self):
        for ts in ("0001-01-01T00:00:00+05:00", "9999-12-31T23:00:00-05:00", "not a time"):
            (turn,) = self.turns(assistant_lines("claude-opus-5", usage(), ts))
            self.assertIsNone(turn["ts"], ts)

    def test_absurd_token_counts_are_discarded_not_stored(self):
        # Beyond any real message; 2**63 does not even fit one SQLite INTEGER.
        (turn,) = self.turns(assistant_lines(
            "claude-opus-5", usage(input_tokens=2**63, output_tokens=server.MAX_TOKENS_PER_TURN + 1,
                                   cache_read=server.MAX_TOKENS_PER_TURN), T0))
        self.assertEqual((turn["input_tokens"], turn["output_tokens"], turn["cache_read_tokens"]),
                         (0, 0, server.MAX_TOKENS_PER_TURN))

    def test_overlong_message_ids_are_bounded_but_still_deduplicate(self):
        long_id = "m" * 200_000
        turns = self.turns(
            assistant_lines("claude-opus-5", usage(output_tokens=3), T0, msg_id=long_id, blocks=3),
            assistant_lines("claude-opus-5", usage(output_tokens=4), T0, msg_id=long_id + "x"))
        self.assertEqual(len(turns), 2)
        self.assertTrue(all(len(t["key"]) <= server.MAX_MODEL_NAME for t in turns))
        self.assertNotEqual(turns[0]["key"], turns[1]["key"])

    def test_overlong_model_names_are_truncated(self):
        (turn,) = self.turns(assistant_lines("m" * 5000, usage(), T0))
        self.assertEqual(turn["model"], "m" * server.MAX_MODEL_NAME)


if __name__ == "__main__":
    unittest.main()
