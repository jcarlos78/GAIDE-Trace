# Connecting the project's MCP servers in your tool

`.mcp.json` at the repo root is the **canonical reference** for the MCP servers this project uses. The MCP protocol is an open standard, but each tool stores its configuration in its own place, so use `.mcp.json` as the source and mirror the entries you need:

| Tool | Where MCP servers are configured |
| --- | --- |
| **Claude Code** | Reads `.mcp.json` natively — nothing to do. Approve the server when prompted. |
| **Google Antigravity** | UI: the MCP store / "Manage MCP servers" panel (Agent settings). Add the server's command and args from `.mcp.json`. |
| **Cursor** | `.cursor/mcp.json` in the repo (same JSON shape) or Cursor Settings → MCP. |
| **Codex CLI** | `~/.codex/config.toml`, `[mcp_servers.<name>]` sections mirroring command/args/env. |
| **Zed** | Settings → `context_servers`, mirroring command/args/env. |

Notes:

- GAIDE-Trace deliberately integrates with nothing: capture is stdlib-only, the store is local files, and the team server is self-hosted. The single entry is `playwright`, used by the `verifier` skill to exercise the server's web console end-to-end (`python3 server/gaide_trace_server.py serve`, then drive `http://127.0.0.1:8321/`).
- Servers named with a leading `_` in `.mcp.json` are disabled — enable by renaming, then mirror.
- Env vars come from your local environment; never write real values into any of these config files (Constitution Principle 6). In particular, a GAIDE-Trace server API key (`gtr_…`) belongs in `.gaide-trace/config.json` of the traced project — gitignored, mode 600 — never in `.mcp.json`.
