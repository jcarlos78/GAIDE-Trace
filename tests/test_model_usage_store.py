"""Store-level derivation of transcript and model usage — specs/model-usage."""

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import assistant_lines, event, load_server, transcript, usage, user_line

server = load_server()

T0 = "2026-09-01T10:00:00.000Z"


def file_digests(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.jsonl"))}


class StoreTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gaide-trace-test-"))
        self.store = server.Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session_row(self, session_id):
        with self.store.connect() as db:
            return dict(db.execute("SELECT * FROM sessions WHERE session_id = ?",
                                   (session_id,)).fetchone())


class DerivedVersionUpgrade(StoreTestCase):
    """AC24 (token-totals part): an index derived by older rules is corrected
    on start without touching the archive or the transcript snapshots."""

    def seed_v031_index(self):
        self.store.ingest_events(
            [event("sess-a", "prompt.submit", T0, prompt="hi")], origin="alice")
        self.store.store_transcript(
            "sess-a",
            transcript(user_line("hi", T0),
                       assistant_lines("claude-opus-5", usage(output_tokens=500), T0,
                                       blocks=3)),
            project="p", origin="alice")
        self.assertEqual(self.session_row("sess-a")["output_tokens"], 500)
        # What v0.3.1 left behind: per-line sums and no derived_version.
        with self.store.connect() as db:
            db.execute("UPDATE sessions SET output_tokens = 1500 WHERE session_id = 'sess-a'")
            db.execute("DELETE FROM meta")
            db.commit()

    def test_stale_index_is_rederived_and_files_are_untouched(self):
        self.seed_v031_index()
        before = file_digests(self.tmp)
        self.assertTrue(before, "fixture should have written archive + transcript files")

        summary = server.Store(self.tmp).rederive_if_stale()

        self.assertIsNotNone(summary)
        self.assertEqual(summary["to"], server.DERIVED_VERSION)
        self.assertEqual(summary["transcripts"], 1)
        self.assertEqual(self.session_row("sess-a")["output_tokens"], 500)
        self.assertEqual(self.store.derived_version(), server.DERIVED_VERSION)
        self.assertEqual(file_digests(self.tmp), before)

    def test_rederivation_keeps_the_upload_time_of_each_transcript(self):
        self.seed_v031_index()
        snapshot = self.tmp / "transcripts" / "sess-a.jsonl"
        os.utime(snapshot, (1_000_000_000, 1_000_000_000))

        self.store.rederive_if_stale()

        self.assertEqual(self.session_row("sess-a")["transcript_updated_at"],
                         "2001-09-09T01:46:40+00:00")

    def test_corrupted_version_value_is_treated_as_stale(self):
        self.seed_v031_index()
        with self.store.connect() as db:
            db.execute("INSERT INTO meta (key, value) VALUES ('derived_version', 'garbage')")
            db.commit()
        self.assertEqual(self.store.derived_version(), 0)
        self.assertIsNotNone(self.store.rederive_if_stale())
        self.assertEqual(self.session_row("sess-a")["output_tokens"], 500)

    def test_fresh_data_directory_is_not_reported_as_an_upgrade(self):
        self.assertIsNone(self.store.rederive_if_stale())
        self.assertEqual(self.store.derived_version(), server.DERIVED_VERSION)

    def test_current_index_is_left_alone(self):
        self.seed_v031_index()
        self.store.rederive_if_stale()
        self.assertIsNone(self.store.rederive_if_stale())

    def test_rebuild_index_does_not_rewrite_transcripts(self):
        self.seed_v031_index()
        snapshot = self.tmp / "transcripts" / "sess-a.jsonl"
        os.utime(snapshot, (1_000_000_000, 1_000_000_000))
        before = file_digests(self.tmp)

        self.store.rebuild_index()

        self.assertEqual(snapshot.stat().st_mtime, 1_000_000_000)
        self.assertEqual(file_digests(self.tmp), before)
        self.assertEqual(self.session_row("sess-a")["output_tokens"], 500)
        self.assertEqual(self.store.derived_version(), server.DERIVED_VERSION)


if __name__ == "__main__":
    unittest.main()
