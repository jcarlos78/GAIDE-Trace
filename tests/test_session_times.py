"""A session's first/last event time must survive any arrival order.

Regression: when a transcript snapshot created the session row before its
first event, SQLite's scalar MIN/MAX returned NULL for every later event, so
the session never got a time range and fell out of every windowed view.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import assistant_lines, event, load_server, transcript, usage

server = load_server()

T1 = "2026-09-01T10:00:00+00:00"
T2 = "2026-09-01T11:00:00+00:00"
T3 = "2026-09-01T12:00:00+00:00"


class SessionTimeRange(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gaide-trace-test-"))
        self.store = server.Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def times(self, session_id):
        with self.store.connect() as db:
            row = db.execute("SELECT first_ts, last_ts FROM sessions WHERE session_id = ?",
                             (session_id,)).fetchone()
        return row["first_ts"], row["last_ts"]

    def upload(self, session_id):
        self.store.store_transcript(session_id, transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=1), T1)), "p", "alice")

    def test_transcript_before_events_still_gets_a_time_range(self):
        self.upload("s1")
        self.store.ingest_events([event("s1", "prompt.submit", T2)], origin="alice")
        self.store.ingest_events([event("s1", "turn.end", T3),
                                  event("s1", "session.start", T1)], origin="alice")
        self.assertEqual(self.times("s1"), (T1, T3))

    def test_events_before_transcript_keep_their_time_range(self):
        self.store.ingest_events([event("s1", "session.start", T1),
                                  event("s1", "turn.end", T3)], origin="alice")
        self.upload("s1")
        self.assertEqual(self.times("s1"), (T1, T3))

    def test_stale_index_with_lost_time_ranges_is_repaired_on_start(self):
        self.store.ingest_events([event("s1", "session.start", T1),
                                  event("s1", "turn.end", T3)], origin="alice")
        with self.store.connect() as db:  # what the defect left behind
            db.execute("UPDATE sessions SET first_ts = NULL, last_ts = NULL")
            db.execute("UPDATE meta SET value = '1' WHERE key = 'derived_version'")
            db.commit()

        self.assertIsNotNone(self.store.rederive_if_stale())

        self.assertEqual(self.times("s1"), (T1, T3))


if __name__ == "__main__":
    unittest.main()
