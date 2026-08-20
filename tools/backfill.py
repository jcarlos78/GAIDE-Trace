#!/usr/bin/env python3
"""
GAIDE-Trace backfill — push an existing local store to the team server.

Use it when a project adopted the server after tracing locally for a while,
or to re-sync after a long offline stretch. Safe to run repeatedly: the
server dedupes events by trace_id and transcript uploads simply overwrite
the snapshot.

Usage:
  python3 tools/backfill.py /path/to/project/.gaide-trace \
      --server https://trace.example.com --token gtr_... [--project NAME]

Server/token default to GAIDE_TRACE_SERVER / GAIDE_TRACE_TOKEN or the
store's config.json, so after `install.sh --server` a bare
`python3 tools/backfill.py <store>` is enough.
"""

import argparse
import gzip
import json
import os
import sys
import urllib.request
from pathlib import Path

BATCH = 500


def post(url, token, data, method="POST", gzipped=False):
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    if gzipped:
        headers["Content-Encoding"] = "gzip"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def main():
    ap = argparse.ArgumentParser(description="Backfill a local trace store to the server")
    ap.add_argument("store", help="path to a .gaide-trace directory")
    ap.add_argument("--server", default=os.environ.get("GAIDE_TRACE_SERVER"))
    ap.add_argument("--token", default=os.environ.get("GAIDE_TRACE_TOKEN"))
    ap.add_argument("--project", default=os.environ.get("GAIDE_TRACE_PROJECT"))
    args = ap.parse_args()

    store = Path(args.store)
    if not store.is_dir():
        sys.exit(f"error: {store} is not a directory")

    server, token = args.server, args.token
    cfg_file = store / "config.json"
    if not (server and token) and cfg_file.is_file():
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        server = server or cfg.get("server")
        token = token or cfg.get("token")
    if not (server and token):
        sys.exit("error: need --server and --token (or config.json / env vars)")
    server = server.rstrip("/")
    project = args.project or store.resolve().parent.name

    # ---- events ----
    sent = dup = 0
    batch = []

    def flush():
        nonlocal sent, dup, batch
        if not batch:
            return
        res = post(f"{server}/api/v1/events", token,
                   json.dumps(batch, ensure_ascii=False).encode("utf-8"))
        sent += res.get("inserted", 0)
        dup += res.get("duplicates", 0)
        batch = []

    for f in sorted((store / "events").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec.setdefault("project", project)
            batch.append(rec)
            if len(batch) >= BATCH:
                flush()
    flush()
    print(f"events: {sent} uploaded, {dup} already on server")

    # ---- transcripts ----
    n = 0
    for f in sorted((store / "transcripts").glob("*.jsonl")):
        body = gzip.compress(f.read_bytes())
        url = (f"{server}/api/v1/transcripts/{f.stem}"
               f"?project={urllib.request.quote(project)}")
        post(url, token, body, method="PUT", gzipped=True)
        n += 1
        print(f"  transcript {f.stem} ({f.stat().st_size:,} bytes)")
    print(f"transcripts: {n} uploaded")


if __name__ == "__main__":
    main()
