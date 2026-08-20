# GAIDE-Trace Server — team deployment guide

The server turns GAIDE-Trace from a per-machine store into a **team-central
interaction ledger**: every member's hooks keep writing locally first (nothing
is ever lost to a network problem), and additionally ship each record to one
central server that offers:

- **Durability** — events land in a raw JSONL archive *and* a SQLite index;
  the index is always rebuildable from the archive.
- **A web console** — dashboard, per-session timelines, transcript downloads,
  filtered exports (JSONL/CSV), API key management.
- **Team identity** — each record is tagged with the key that shipped it, so
  activity is attributable per member and per project.

Everything is Python standard library. There is **nothing to `pip install`**
on the server — if the machine has Python 3.9+, it can run the server.

```
developer machines                         your server
┌────────────────────────┐                ┌─────────────────────────────┐
│ Claude Code session    │                │ gaide_trace_server.py       │
│  └─ hooks/trace_hook   │   HTTPS        │  ├─ data/archive/  (JSONL)  │
│      1. local store ✓  │ ─────────────▶ │  ├─ data/transcripts/       │
│      2. outbox → ship  │  retry-safe    │  ├─ data/trace.db  (index)  │
│         (at-least-once)│                │  └─ web console  :8321      │
└────────────────────────┘                └─────────────────────────────┘
```

## 1. Quick start (any machine, 60 seconds)

```bash
git clone https://github.com/jcarlos78/GAIDE-Trace
cd GAIDE-Trace
python3 server/gaide_trace_server.py serve
```

Open `http://localhost:8321/` and sign in with the default account
**`admin` / `admin`** — the console forces you to set a new password before
anything else works. From there, everything is managed visually:

- **Users** — create accounts for the team (each gets a temporary password,
  shown once, that must be changed on first login), reset passwords, disable.
- **Projects** — register a project; the server generates an ingest-only key
  and a ready-to-paste **install prompt** (see §2).
- **API keys** — advanced: raw bearer keys for scripts/CI.

Locked out? Reset any account from the server shell:

```bash
python3 server/gaide_trace_server.py user reset admin   # prints a temp password
python3 server/gaide_trace_server.py user list
```

Two kinds of credentials:

| Credential | Who uses it | Can |
|---|---|---|
| username + password | humans, in the console | member: browse + export + register projects · admin: + users, keys, removals |
| `agent` API key | hooks / install prompts | ingest only — leaked, it can add data but never read |
| `member`/`admin` API key | scripts, CI | scripted API access (create via **API keys** or `key create`) |

## 2. Connect a project

The easy path: open **Projects** in the console, register the project, click
**install prompt** and send the text to whoever works on that repo. They paste
it into Claude Code at the project root and the agent does the rest — clones
this repo, runs `install.sh` with the right `--server`/`--token`/`--name`, and
backfills any pre-existing local history. The key embedded in the prompt is
ingest-only and can be rotated from the same page at any time.

Manual equivalent:

```bash
./install.sh /path/to/your-project \
  --server https://trace.example.com --token gtr_... [--name my-project]
```

This registers the hooks as before **and** writes the connection to
`<project>/.gaide-trace/config.json` (mode 600, inside the gitignored store —
the token never enters the code repo). Environment variables
`GAIDE_TRACE_SERVER` / `GAIDE_TRACE_TOKEN` / `GAIDE_TRACE_PROJECT` override it.

### No-data-loss model

1. Every event is appended to the **local store first**, unconditionally.
2. It is then queued in `.gaide-trace/outbox/` and shipped in batches.
3. If the server is down or you are offline, the outbox simply accumulates
   and is flushed by later hook fires — delivery is **at-least-once**, and the
   server dedupes by `trace_id`, so retries are always safe.
4. Transcript snapshots are uploaded (gzipped) at every `Stop`/`SessionEnd`;
   each upload supersedes the previous one, same as the local semantics.

### Backfill history

Adopted the server after weeks of local tracing? Push the whole store:

```bash
python3 tools/backfill.py /path/to/your-project/.gaide-trace
# (uses config.json; or pass --server/--token explicitly)
```

Idempotent — run it as often as you like.

## 3. Deploy on a VPS (systemd, no container)

Requirements: a Linux VPS with Python 3.9+ and a DNS record pointing at it.

```bash
# as root
useradd --system --create-home --home-dir /var/lib/gaide-trace gaide-trace
git clone https://github.com/jcarlos78/GAIDE-Trace /opt/gaide-trace
cp /opt/gaide-trace/deploy/gaide-trace-server.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gaide-trace-server

# the one-time admin key is in the journal:
journalctl -u gaide-trace-server | grep gtr_
```

The unit binds to `127.0.0.1:8321` and is sandboxed (`ProtectSystem=strict`,
only its data dir writable). Put **Caddy** in front for automatic HTTPS:

```bash
apt install caddy                          # Debian/Ubuntu
cp /opt/gaide-trace/deploy/Caddyfile.example /etc/caddy/Caddyfile
# edit the domain, then:
systemctl reload caddy
```

Done: `https://trace.example.com` serves the console and the ingest API with
a Let's Encrypt certificate that renews itself. (nginx + certbot works the
same way — proxy to `127.0.0.1:8321`.)

Updating:

```bash
cd /opt/gaide-trace && git pull && systemctl restart gaide-trace-server
```

## 4. Deploy as a container

```bash
git clone https://github.com/jcarlos78/GAIDE-Trace && cd GAIDE-Trace

# plain, on localhost:8321 (LAN / behind your own proxy):
docker compose -f deploy/docker-compose.yml up -d

# or with automatic TLS via the bundled Caddy service:
TRACE_DOMAIN=trace.example.com \
  docker compose -f deploy/docker-compose.yml --profile tls up -d

docker compose -f deploy/docker-compose.yml logs server | grep gtr_   # admin key
```

Data lives in the `trace-data` named volume. The image is `python:3.12-slim`
plus the repo's `server/` directory — no dependencies to patch.

Kubernetes or any other orchestrator: run the same image with `/data` on a
persistent volume and one replica (SQLite is single-writer by design at this
scale).

## 5. Backups

The entire state is one directory (`data/`, or the `trace-data` volume):

- `archive/events/*.jsonl` — source of truth for the event layer
- `transcripts/*.jsonl` — full-fidelity transcript snapshots
- `trace.db` — derived index (rebuildable: `gaide_trace_server.py rebuild-index`)

Because the truth layer is append-only plain files, any file-level backup
works and stays consistent:

```bash
# example: nightly rsync
rsync -a /var/lib/gaide-trace/data/ backup-host:/backups/gaide-trace/
```

If `trace.db` is ever lost or corrupted, `rebuild-index` reconstructs it from
the JSONL archive.

## 6. API (for your own tooling)

All endpoints under `/api/v1/`, auth via `Authorization: Bearer <token>` —
either an API key (`gtr_...`) or a console session (`gts_...`, obtained from
the login endpoint).

| Method & path | Role | Purpose |
|---|---|---|
| `POST /api/v1/auth/login` | — | `{username, password}` → session token (+ `must_change_password`) |
| `POST /api/v1/auth/password` | member | change own password (required on first login) |
| `POST /api/v1/auth/logout` | member | revoke the session |
| `POST /api/v1/events` | agent | ingest a JSON array of event records (idempotent by `trace_id`; gzip body supported) |
| `PUT /api/v1/transcripts/<session_id>?project=` | agent | store/replace a transcript snapshot (gzip supported) |
| `GET /api/v1/overview?project&from&to` | member | aggregates: totals, per-day series, top tools, per-project/member |
| `GET /api/v1/sessions?project&origin&from&to&q&limit&offset` | member | session list |
| `GET /api/v1/sessions/<id>` | member | session meta + full event timeline |
| `GET /api/v1/sessions/<id>/transcript` | member | raw transcript download |
| `GET /api/v1/export?format=jsonl\|csv&project&session&origin&event&from&to` | member | filtered event export |
| `GET /api/v1/projects/manage`, `POST /api/v1/projects`, `POST .../<id>/rotate` | member | registered projects + install-prompt keys |
| `DELETE /api/v1/projects/<id>` | admin | remove registration (data kept) |
| `GET/POST /api/v1/users`, `POST .../<id>/reset`, `DELETE .../<id>` | admin | console accounts |
| `GET/POST /api/v1/keys`, `DELETE /api/v1/keys/<id>` | admin | raw API-key management |
| `GET /healthz` | — | liveness |

The export format is the same schema as the local store
(`schema/event.schema.json`) plus `origin` (key name) and `received_at` —
`analysis/load_trace.py` loads server exports unchanged.

## 7. Security notes

- **Always front with TLS** for anything beyond localhost — passwords and
  tokens travel as bearer credentials.
- Passwords are stored as PBKDF2-SHA256 (600k iterations); logins throttle
  after 5 straight failures. A password change revokes every open session of
  that account.
- API keys are stored only as SHA-256 hashes; a lost key is revoked, never
  recovered. Exception: a *registered project's* agent key is kept readable
  server-side so the install prompt can be re-copied and rotated from the
  console — an accepted tradeoff because those keys are ingest-only (they can
  add data, never read it).
- Give hooks `agent` keys where possible: an exfiltrated agent key can only
  *add* data, never read it.
- The event layer is redacted at capture (see `docs/ARCHITECTURE.md` D5);
  transcript snapshots are raw by design — treat the server's data directory
  with the same care as the code it describes, and apply your institution's
  ethics/consent requirements before sharing datasets.
