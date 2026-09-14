"""`serve` start-up behaviour, exercised as a real process — specs/model-usage
AC24 (the operator sees the re-derivation in the log)."""

import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fixtures import REPO, assistant_lines, load_server, transcript, usage

server = load_server()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def startup_log(data_dir: Path, timeout=15):
    """Run `serve` until it prints its console URL; return everything it
    printed before that."""
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "server" / "gaide_trace_server.py"),
         "--data", str(data_dir), "serve", "--host", "127.0.0.1",
         "--port", str(free_port())],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    deadline = time.monotonic() + timeout
    try:
        for line in proc.stdout:
            lines.append(line)
            if "console:" in line or time.monotonic() > deadline:
                break
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()
    return "".join(lines)


class StartupRederivation(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gaide-trace-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stale_index_is_upgraded_before_serving_and_logged(self):
        store = server.Store(self.tmp)
        store.store_transcript("sess-a", transcript(
            assistant_lines("claude-opus-5", usage(output_tokens=9),
                            "2026-09-01T10:00:00Z", blocks=2)), "p", "alice")
        with store.connect() as db:
            db.execute("DELETE FROM meta")
            db.commit()

        log = startup_log(self.tmp)

        self.assertIn(f"derived-index schema 0 -> {server.DERIVED_VERSION}: "
                      "1 transcripts re-indexed", log)
        self.assertLess(log.index("derived-index schema"), log.index("console:"))

    def test_fresh_install_logs_no_upgrade(self):
        log = startup_log(self.tmp)
        self.assertIn("console:", log)
        self.assertNotIn("derived-index schema", log)


if __name__ == "__main__":
    unittest.main()
