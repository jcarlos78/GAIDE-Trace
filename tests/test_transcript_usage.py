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


if __name__ == "__main__":
    unittest.main()
