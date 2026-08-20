/* GAIDE-Trace console — vanilla JS, no dependencies.
   Talks to the API with the bearer key kept in localStorage; every view is
   rendered into <main> via hash routing. */

"use strict";

const $ = (sel, el) => (el || document).querySelector(sel);
const main = $("#main");
const tooltip = $("#tooltip");

const state = {
  me: null,
  token: localStorage.getItem("gt_session") || "",
  filters: { project: "", range: "30" },   // shared by overview + sessions
  sessionsPage: 0,
};

// ---------------------------------------------------------------- helpers

function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmt(n) {
  if (n == null) return "–";
  n = Number(n);
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (Math.abs(n) >= 1e4) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString("en-US");
}

function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function timeAgo(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function rangeFrom(range) {
  if (range === "all") return "";
  const d = new Date(Date.now() - Number(range) * 86400e3);
  return d.toISOString().slice(0, 10);
}

function qs(params) {
  const p = Object.entries(params).filter(([, v]) => v !== "" && v != null);
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&") : "";
}

async function api(path, opts) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      Authorization: "Bearer " + state.token,
      ...(opts && opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts && opts.headers),
    },
  });
  if (res.status === 401) { logout(); throw new Error("unauthorized"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch (e) { /* keep statusText */ }
    throw new Error(msg);
  }
  return res;
}

const apiJSON = (path, opts) => api(path, opts).then((r) => r.json());

async function download(path) {
  const res = await api(path);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const name = (cd.match(/filename="([^"]+)"/) || [])[1] || "export.jsonl";
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------- tooltip

function showTooltip(html, x, y) {
  tooltip.innerHTML = html;
  tooltip.classList.remove("hidden");
  const r = tooltip.getBoundingClientRect();
  const px = Math.min(x + 14, window.innerWidth - r.width - 10);
  const py = Math.max(y - r.height - 12, 8);
  tooltip.style.left = px + "px";
  tooltip.style.top = py + "px";
}
const hideTooltip = () => tooltip.classList.add("hidden");

// ---------------------------------------------------------------- charts
// Marks follow the dataviz spec: bars ≤24px with 4px rounded data-end and a
// square baseline, 2px surface gaps, solid hairline grid, text in ink tokens.

// Carbon ramp steps (purple 60, teal 50), CVD-validated on the tile surface.
const SERIES = [
  { key: "prompts", label: "Prompts", color: "var(--series-1)", hex: "#8a3ffc" },
  { key: "tool_calls", label: "Tool calls", color: "var(--series-2)", hex: "#009d9a" },
];

function niceMax(v) {
  if (v <= 5) return 5;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * pow) return m * pow;
  return 10 * pow;
}

function barRect(x, y, w, h, fill) {
  // Carbon chart bars: flat, square-cornered
  if (h <= 0 && w <= 0) return "";
  return `<rect x="${x}" y="${y}" width="${Math.max(w, 0)}" height="${Math.max(h, 0)}"
    fill="${fill}"></rect>`;
}

/* Fill calendar gaps so the time axis is honest (a day with no events is a
   zero, not a missing slot). */
function fillDays(days) {
  if (days.length < 2) return days;
  const byDay = Object.fromEntries(days.map((d) => [d.day, d]));
  const out = [];
  const start = new Date(days[0].day + "T00:00:00Z");
  const end = new Date(days[days.length - 1].day + "T00:00:00Z");
  for (let t = start.getTime(); t <= end.getTime(); t += 86400e3) {
    const day = new Date(t).toISOString().slice(0, 10);
    out.push(byDay[day] || { day, prompts: 0, tool_calls: 0 });
  }
  return out;
}

/* Grouped columns per day, two series, hover band + tooltip, table twin. */
function activityChart(container, days) {
  days = fillDays(days);
  const W = 640, H = 240, padL = 46, padR = 10, padT = 10, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = niceMax(Math.max(1, ...days.map((d) => Math.max(d.prompts || 0, d.tool_calls || 0))));
  const ticks = [0, max / 2, max].map((t) => Math.round(t));
  const n = Math.max(days.length, 1);
  const band = plotW / n;
  const gap = 2;                                   // surface gap between bars
  const barW = Math.min(24, Math.max(2, (band - gap * 3) / 2));
  const groupW = barW * 2 + gap;
  const y = (v) => padT + plotH * (1 - v / max);

  let bars = "", bands = "";
  days.forEach((d, i) => {
    const gx = padL + band * i + (band - groupW) / 2;
    SERIES.forEach((s, si) => {
      const v = d[s.key] || 0;
      const by = y(v);
      bars += barRect(gx + si * (barW + gap), by, barW, padT + plotH - by, s.hex);
    });
    bands += `<rect class="hover-band" data-i="${i}" x="${padL + band * i}" y="${padT}"
               width="${band}" height="${plotH}" fill="transparent"></rect>`;
  });

  const grid = ticks.map((t) =>
    `<line x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"
       stroke="${t === 0 ? "var(--baseline)" : "var(--grid)"}" stroke-width="1"></line>
     <text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end" fill="var(--ink-3)"
       font-size="10" style="font-variant-numeric:tabular-nums">${fmt(t)}</text>`).join("");

  const labelEvery = Math.ceil(n / 8);
  const xlabels = days.map((d, i) => i % labelEvery ? "" :
    `<text x="${padL + band * i + band / 2}" y="${H - 8}" text-anchor="middle"
       fill="var(--ink-3)" font-size="10">${esc(d.day.slice(5))}</text>`).join("");

  container.innerHTML = `
    <div class="legend">${SERIES.map((s) =>
      `<span class="key"><span class="swatch" style="background:${s.color}"></span>${s.label}</span>`).join("")}
    </div>
    <div class="chart-wrap"><svg viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Prompts and tool calls per day">${grid}${bars}${xlabels}${bands}</svg></div>`;

  container.querySelectorAll(".hover-band").forEach((band_) => {
    band_.addEventListener("mousemove", (e) => {
      const d = days[Number(band_.dataset.i)];
      showTooltip(`<div class="tt-title">${esc(d.day)}</div>` +
        SERIES.map((s) => `<div class="tt-row"><span class="swatch" style="background:${s.color}"></span>
          ${s.label}<b>${fmt(d[s.key] || 0)}</b></div>`).join(""), e.clientX, e.clientY);
    });
    band_.addEventListener("mouseleave", hideTooltip);
  });
}

function activityTable(container, days) {
  container.innerHTML = `<div class="table-scroll"><table>
    <thead><tr><th>Day</th><th class="num">Prompts</th><th class="num">Tool calls</th></tr></thead>
    <tbody>${days.map((d) => `<tr><td>${esc(d.day)}</td>
      <td class="num">${fmt(d.prompts || 0)}</td><td class="num">${fmt(d.tool_calls || 0)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

/* Horizontal bars, single series (slot 1), value at the tip. */
function toolsChart(container, rows) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  const rowH = 26, barH = 12, labelW = 120, valW = 52;
  const W = 420, H = rows.length * rowH + 6;
  let svg = "";
  rows.forEach((r, i) => {
    const y0 = i * rowH + (rowH - barH) / 2;
    const w = Math.max(2, (W - labelW - valW) * (r.n / max));
    svg += `
      <text x="${labelW - 10}" y="${y0 + barH - 2}" text-anchor="end"
        fill="var(--ink-2)" font-size="11">${esc(r.tool.length > 15 ? r.tool.slice(0, 14) + "…" : r.tool)}</text>
      ${barRect(labelW, y0, w, barH, "#8a3ffc")}
      <text x="${labelW + w + 8}" y="${y0 + barH - 2}" fill="var(--ink-3)" font-size="10"
        style="font-variant-numeric:tabular-nums">${fmt(r.n)}</text>
      <rect class="hover-band" data-i="${i}" x="0" y="${i * rowH}" width="${W}" height="${rowH}"
        fill="transparent"></rect>`;
  });
  container.innerHTML = `<div class="chart-wrap"><svg viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Tool calls by tool">${svg}</svg></div>`;
  container.querySelectorAll(".hover-band").forEach((b) => {
    b.addEventListener("mousemove", (e) => {
      const r = rows[Number(b.dataset.i)];
      showTooltip(`<div class="tt-title">${esc(r.tool)}</div>
        <div class="tt-row">calls<b>${fmt(r.n)}</b></div>`, e.clientX, e.clientY);
    });
    b.addEventListener("mouseleave", hideTooltip);
  });
}

function toolsTable(container, rows) {
  container.innerHTML = `<div class="table-scroll"><table>
    <thead><tr><th>Tool</th><th class="num">Calls</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${esc(r.tool)}</td><td class="num">${fmt(r.n)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

/* Wire a chart card's chart/table toggle. */
function chartCard(title, renderChart, renderTable) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="card-head"><span class="card-title">${esc(title)}</span>
      <div class="card-tools"><button class="toggle-table" type="button">table</button></div>
    </div><div class="card-body"></div>`;
  const body = $(".card-body", card);
  const btn = $(".toggle-table", card);
  let showingTable = false;
  const render = () => (showingTable ? renderTable(body) : renderChart(body));
  btn.addEventListener("click", () => {
    showingTable = !showingTable;
    btn.classList.toggle("active", showingTable);
    btn.textContent = showingTable ? "chart" : "table";
    render();
  });
  render();
  return card;
}

// ---------------------------------------------------------------- filters row

async function filtersRow(onChange, extra) {
  const wrap = document.createElement("div");
  wrap.className = "filters";
  const projects = await apiJSON("/api/v1/projects").catch(() => []);
  wrap.innerHTML = `
    <div class="field"><label class="microlabel">Project</label>
      <select id="f-project"><option value="">all projects</option>
        ${projects.map((p) => `<option ${p === state.filters.project ? "selected" : ""}
          value="${esc(p)}">${esc(p)}</option>`).join("")}
      </select></div>
    <div class="field"><label class="microlabel">Window</label>
      <div class="range-presets">
        ${["7", "30", "90", "all"].map((r) => `<button type="button" data-r="${r}"
          class="${state.filters.range === r ? "active" : ""}">${r === "all" ? "all time" : r + "d"}</button>`).join("")}
      </div></div>
    ${extra || ""}`;
  $("#f-project", wrap).addEventListener("change", (e) => {
    state.filters.project = e.target.value;
    onChange();
  });
  wrap.querySelectorAll(".range-presets button").forEach((b) =>
    b.addEventListener("click", () => {
      state.filters.range = b.dataset.r;
      wrap.querySelectorAll(".range-presets button").forEach((x) =>
        x.classList.toggle("active", x === b));
      onChange();
    }));
  return wrap;
}

// ---------------------------------------------------------------- views

async function viewOverview() {
  main.innerHTML = "";
  main.appendChild(await filtersRow(viewOverview));

  const params = { project: state.filters.project, from: rangeFrom(state.filters.range) };
  const o = await apiJSON("/api/v1/overview" + qs(params));
  const t = o.totals;

  const failNote = t.tool_calls
    ? `${((t.failures / (t.tool_calls + t.failures)) * 100).toFixed(1)}% of tool calls`
    : "";
  const kpis = [
    ["Sessions", t.sessions, `${t.projects || 0} project${t.projects === 1 ? "" : "s"} · ${t.origins || 0} member${t.origins === 1 ? "" : "s"}`],
    ["Prompts", t.prompts, ""],
    ["Tool calls", t.tool_calls, ""],
    ["Tool failures", t.failures, failNote, t.failures > 0],
    ["Output tokens", t.output_tokens, t.input_tokens != null ? fmt(t.input_tokens) + " input" : ""],
  ];
  const kpiRow = document.createElement("div");
  kpiRow.className = "kpi-row";
  kpiRow.innerHTML = kpis.map(([label, v, note, bad]) => `
    <div class="card kpi"><span class="microlabel">${label}</span>
      <div class="kpi-value">${fmt(v || 0)}</div>
      <div class="kpi-note${bad ? " bad" : ""}">${esc(note || "")}</div></div>`).join("");
  main.appendChild(kpiRow);

  const grid = document.createElement("div");
  grid.className = "grid-2";
  if (o.per_day.length) {
    grid.appendChild(chartCard("Activity — per day",
      (el) => activityChart(el, o.per_day), (el) => activityTable(el, o.per_day)));
  }
  if (o.top_tools.length) {
    grid.appendChild(chartCard("Tool usage",
      (el) => toolsChart(el, o.top_tools), (el) => toolsTable(el, o.top_tools)));
  }
  main.appendChild(grid);

  if (!o.per_day.length) {
    main.insertAdjacentHTML("beforeend", `<div class="card"><div class="empty">
      <span class="glyph">◉</span>no events in this window —
      connect a project with <code>install.sh --server</code> and start a session</div></div>`);
  }

  const twoCol = document.createElement("div");
  twoCol.className = "grid-2";
  twoCol.appendChild(breakdownCard("Projects", o.projects, "project"));
  twoCol.appendChild(breakdownCard("People", o.origins, "origin"));
  main.appendChild(twoCol);

  const last = o.projects.map((p) => p.last_ts).sort().pop();
  $("#last-signal").textContent = last ? "last event " + timeAgo(last) : "";
}

function breakdownCard(title, rows, keyField) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<div class="card-head"><span class="card-title">${title}</span></div>
    <div class="table-scroll"><table>
      <thead><tr><th>${keyField}</th><th class="num">Sessions</th>
        <th class="num">Prompts</th><th class="num">Tool calls</th><th>Last active</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td>${esc(r[keyField] || "—")}</td>
        <td class="num">${fmt(r.sessions)}</td>
        <td class="num">${fmt(r.prompts || 0)}</td>
        <td class="num">${fmt(r.tool_calls || 0)}</td>
        <td>${timeAgo(r.last_ts)}</td></tr>`).join("") ||
        `<tr><td colspan="5" class="empty">no data yet</td></tr>`}
      </tbody></table></div>`;
  return card;
}

async function viewSessions() {
  main.innerHTML = "";
  const extra = `
    <div class="field"><label class="microlabel">Session id</label>
      <input id="f-q" placeholder="search…" value=""></div>`;
  main.appendChild(await filtersRow(() => { state.sessionsPage = 0; loadSessions(); }, extra));
  const holder = document.createElement("div");
  holder.className = "card";
  main.appendChild(holder);
  let qTimer;
  $("#f-q").addEventListener("input", () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { state.sessionsPage = 0; loadSessions(); }, 250);
  });

  async function loadSessions() {
    const limit = 50;
    const params = {
      project: state.filters.project,
      from: rangeFrom(state.filters.range),
      q: ($("#f-q") || {}).value || "",
      limit, offset: state.sessionsPage * limit,
    };
    const data = await apiJSON("/api/v1/sessions" + qs(params));
    if (!data.sessions.length) {
      holder.innerHTML = `<div class="empty"><span class="glyph">◉</span>no sessions match</div>`;
      return;
    }
    holder.innerHTML = `<div class="table-scroll"><table>
      <thead><tr><th>Session</th><th>Project</th><th>Member</th><th>Last active</th>
        <th class="num">Prompts</th><th class="num">Tools</th><th class="num">Fail</th>
        <th class="num">Out tokens</th><th>Transcript</th></tr></thead>
      <tbody>${data.sessions.map((s) => `<tr class="click" data-sid="${esc(s.session_id)}">
        <td><span class="mono-id">${esc(s.session_id.slice(0, 8))}…</span></td>
        <td>${esc(s.project || "—")}</td>
        <td>${esc(s.origin || "—")}</td>
        <td>${fmtTime(s.last_ts)}</td>
        <td class="num">${fmt(s.prompts)}</td>
        <td class="num">${fmt(s.tool_calls)}</td>
        <td class="num">${s.failures ? `<span style="color:var(--serious)">${fmt(s.failures)}</span>` : "0"}</td>
        <td class="num">${fmt(s.output_tokens)}</td>
        <td>${s.transcript_bytes ? '<span class="badge ok">✓ stored</span>' : '<span class="badge">events only</span>'}</td>
      </tr>`).join("")}</tbody></table></div>
      <div class="pager">
        <span>${data.total} session${data.total === 1 ? "" : "s"}</span>
        <button class="btn btn-ghost" id="pg-prev" ${state.sessionsPage ? "" : "disabled"}>‹ prev</button>
        <button class="btn btn-ghost" id="pg-next"
          ${(state.sessionsPage + 1) * limit < data.total ? "" : "disabled"}>next ›</button>
      </div>`;
    holder.querySelectorAll("tr.click").forEach((tr) =>
      tr.addEventListener("click", () => { location.hash = "#/session/" + tr.dataset.sid; }));
    $("#pg-prev", holder).addEventListener("click", () => { state.sessionsPage--; loadSessions(); });
    $("#pg-next", holder).addEventListener("click", () => { state.sessionsPage++; loadSessions(); });
  }
  await loadSessions();
}

const EVENT_GLYPHS = {
  SessionStart: ["▶", "", "session start"],
  UserPromptSubmit: ["❯", "prompt", "prompt"],
  PostToolUse: ["⚙", "", ""],
  PostToolUseFailure: ["✕", "fail", "tool failure"],
  Stop: ["◀", "stop", "turn end"],
  SubagentStart: ["◌", "agent", "subagent start"],
  SubagentStop: ["◌", "agent", "subagent stop"],
  PreCompact: ["≋", "", "context compaction"],
  SessionEnd: ["■", "", "session end"],
};

async function viewSession(sid) {
  main.innerHTML = `<div class="card"><div class="empty">loading session…</div></div>`;
  const data = await apiJSON("/api/v1/sessions/" + encodeURIComponent(sid));
  const s = data.session;
  main.innerHTML = "";

  const head = document.createElement("div");
  head.className = "card session-head";
  head.innerHTML = `
    <div>
      <span class="microlabel">Session</span>
      <div class="mono-strong">${esc(s.session_id)}</div>
      <div class="session-meta">
        <div><span class="microlabel">Project</span><span class="val">${esc(s.project || "—")}</span></div>
        <div><span class="microlabel">Member</span><span class="val">${esc(s.origin || "—")}</span></div>
        <div><span class="microlabel">Started</span><span class="val">${fmtTime(s.first_ts)}</span></div>
        <div><span class="microlabel">Last event</span><span class="val">${fmtTime(s.last_ts)}</span></div>
        <div><span class="microlabel">Prompts</span><span class="val">${fmt(s.prompts)}</span></div>
        <div><span class="microlabel">Tool calls</span><span class="val">${fmt(s.tool_calls)}</span></div>
        <div><span class="microlabel">Failures</span><span class="val">${fmt(s.failures)}</span></div>
        <div><span class="microlabel">Tokens in / out</span>
          <span class="val">${fmt(s.input_tokens)} / ${fmt(s.output_tokens)}</span></div>
        ${s.models ? `<div><span class="microlabel">Models</span><span class="val">${esc(s.models)}</span></div>` : ""}
      </div>
    </div>
    <div class="form-actions">
      ${data.has_transcript ? `<button class="btn" id="dl-transcript">↓ transcript.jsonl</button>` : ""}
      <button class="btn" id="dl-events">↓ events.jsonl</button>
      <button class="btn" id="dl-events-csv">↓ events.csv</button>
    </div>`;
  main.appendChild(head);
  if (data.has_transcript) {
    $("#dl-transcript").addEventListener("click", () =>
      download(`/api/v1/sessions/${encodeURIComponent(sid)}/transcript`));
  }
  $("#dl-events").addEventListener("click", () =>
    download(`/api/v1/export${qs({ session: sid, format: "jsonl" })}`));
  $("#dl-events-csv").addEventListener("click", () =>
    download(`/api/v1/export${qs({ session: sid, format: "csv" })}`));

  const tl = document.createElement("div");
  tl.className = "timeline";
  tl.innerHTML = data.events.map((e, i) => {
    const [glyph, cls, tag] = EVENT_GLYPHS[e.event] || ["·", "", e.event];
    let body = "";
    if (e.event === "UserPromptSubmit") {
      body = `<div class="tl-card prompt"><div class="tl-tag">prompt${e.agent_type ? " · " + esc(e.agent_type) : ""}</div>
        <div class="tl-text">${esc(e.prompt || "")}</div></div>`;
    } else if (e.event === "PostToolUse" || e.event === "PostToolUseFailure") {
      const fail = e.event === "PostToolUseFailure";
      body = `<div class="tl-card${fail ? " fail" : ""}">
        <div class="tl-tool" data-i="${i}">
          <span class="chev">›</span>
          <span class="tool-name">${esc(e.tool_name || "tool")}</span>
          ${fail ? '<span class="badge warn">✕ failed</span>' : ""}
          ${e.agent_type ? `<span class="badge">${esc(e.agent_type)}</span>` : ""}
        </div>
        <div class="tl-io">
          ${e.tool_input ? `<span class="microlabel">input</span><pre>${esc(e.tool_input)}</pre>` : ""}
          ${e.tool_response ? `<span class="microlabel">response</span><pre>${esc(e.tool_response)}</pre>` : ""}
        </div></div>`;
    } else if (e.event === "Stop" || e.event === "SubagentStop") {
      body = e.last_assistant_message
        ? `<div class="tl-card stop"><div class="tl-tag">${esc(tag)}${e.agent_type ? " · " + esc(e.agent_type) : ""}</div>
           <div class="tl-text">${esc(e.last_assistant_message)}</div></div>`
        : `<div class="tl-tag" style="padding:8px 0">${esc(tag)}</div>`;
    } else {
      body = `<div class="tl-tag" style="padding:8px 0">${esc(tag)}
        ${e.model ? `· ${esc(e.model)}` : ""}</div>`;
    }
    return `<div class="tl-item">
      <div class="tl-time">${fmtTime(e.ts).split(", ")[1] || fmtTime(e.ts)}</div>
      <div class="tl-rail"><div class="tl-glyph ${cls}">${glyph}</div></div>
      <div class="tl-body">${body}</div></div>`;
  }).join("");
  main.appendChild(tl);
  tl.querySelectorAll(".tl-tool").forEach((el) =>
    el.addEventListener("click", () => el.classList.toggle("open")));
}

async function viewExport() {
  main.innerHTML = "";
  const card = document.createElement("div");
  card.className = "card";
  const projects = await apiJSON("/api/v1/projects").catch(() => []);
  card.innerHTML = `
    <div class="card-head"><span class="card-title">Export the ledger</span></div>
    <p style="margin-bottom:16px;color:var(--ink-3)">Filtered slices of the event
      layer, ready for pandas / spreadsheets. Raw transcripts are downloaded per
      session from its detail page.</p>
    <div class="form-grid">
      <div class="field"><label class="microlabel">Project</label>
        <select id="x-project"><option value="">all</option>
          ${projects.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("")}</select></div>
      <div class="field"><label class="microlabel">Event type</label>
        <select id="x-event"><option value="">all</option>
          ${Object.keys(EVENT_GLYPHS).map((e) => `<option>${e}</option>`).join("")}</select></div>
      <div class="field"><label class="microlabel">From (UTC date)</label>
        <input id="x-from" type="date"></div>
      <div class="field"><label class="microlabel">To (UTC date)</label>
        <input id="x-to" type="date"></div>
      <div class="field"><label class="microlabel">Session id (optional)</label>
        <input id="x-session" placeholder="full id"></div>
      <div class="field"><label class="microlabel">Member (optional)</label>
        <input id="x-origin" placeholder="key name"></div>
    </div>
    <div class="form-actions">
      <button class="btn btn-accent" id="x-jsonl">↓ Export JSONL</button>
      <button class="btn" id="x-csv">↓ Export CSV</button>
    </div>`;
  main.appendChild(card);
  const params = (format) => qs({
    format,
    project: $("#x-project").value,
    event: $("#x-event").value,
    from: $("#x-from").value,
    to: $("#x-to").value ? $("#x-to").value + "T23:59:59" : "",
    session: $("#x-session").value.trim(),
    origin: $("#x-origin").value.trim(),
  });
  $("#x-jsonl").addEventListener("click", () => download("/api/v1/export" + params("jsonl")));
  $("#x-csv").addEventListener("click", () => download("/api/v1/export" + params("csv")));
}

async function viewKeys() {
  if (state.me.role !== "admin") { location.hash = "#/overview"; return; }
  main.innerHTML = "";
  const create = document.createElement("div");
  create.className = "card";
  create.innerHTML = `
    <div class="card-head"><span class="card-title">Issue a key</span></div>
    <div class="form-grid">
      <div class="field"><label class="microlabel">Name (person or machine)</label>
        <input id="k-name" placeholder="alice"></div>
      <div class="field"><label class="microlabel">Role</label>
        <select id="k-role">
          <option value="agent">agent — ingest only (for hooks)</option>
          <option value="member" selected>member — ingest + console + export</option>
          <option value="admin">admin — everything</option>
        </select></div>
    </div>
    <div class="form-actions"><button class="btn btn-accent" id="k-create">Create key</button></div>
    <div id="k-reveal"></div>`;
  main.appendChild(create);

  const listCard = document.createElement("div");
  listCard.className = "card";
  main.appendChild(listCard);

  async function loadKeys() {
    const keys = await apiJSON("/api/v1/keys");
    listCard.innerHTML = `
      <div class="card-head"><span class="card-title">Issued keys</span></div>
      <div class="table-scroll"><table>
        <thead><tr><th>Name</th><th>Role</th><th>Created</th><th>Last seen</th>
          <th class="num">Sessions</th><th>Status</th><th></th></tr></thead>
        <tbody>${keys.map((k) => `<tr>
          <td style="color:var(--ink)">${esc(k.name)}</td>
          <td><span class="badge">${esc(k.role)}</span></td>
          <td>${fmtTime(k.created_at)}</td>
          <td>${k.last_seen_at ? timeAgo(k.last_seen_at) : "never"}</td>
          <td class="num">${fmt(k.sessions)}</td>
          <td>${k.revoked_at ? '<span class="badge warn">revoked</span>' : '<span class="badge ok">active</span>'}</td>
          <td>${k.revoked_at ? "" : `<button class="btn btn-ghost btn-danger" data-id="${k.id}">revoke</button>`}</td>
        </tr>`).join("")}</tbody></table></div>`;
    listCard.querySelectorAll("button[data-id]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm(`Revoke this key? Hooks using it will stop shipping (local capture continues).`)) return;
        await api("/api/v1/keys/" + b.dataset.id, { method: "DELETE" });
        loadKeys();
      }));
  }
  await loadKeys();

  $("#k-create").addEventListener("click", async () => {
    const name = $("#k-name").value.trim();
    if (!name) return;
    const res = await apiJSON("/api/v1/keys", {
      method: "POST",
      body: JSON.stringify({ name, role: $("#k-role").value }),
    });
    $("#k-reveal").innerHTML = `<div class="token-reveal">
      <span class="microlabel">shown once</span><code>${esc(res.token)}</code>
      <button class="btn" id="k-copy">copy</button></div>`;
    $("#k-copy").addEventListener("click", () =>
      navigator.clipboard.writeText(res.token));
    $("#k-name").value = "";
    loadKeys();
  });
}

// ---------------------------------------------------------------- projects

function installPrompt(p) {
  const origin = location.origin;
  return `Conecte este projeto ao GAIDE-Trace (o coletor de traces de sessões de IA do time).

Execute no diretório raiz do projeto:

1. Baixe (ou atualize) o instalador:
   git clone https://github.com/jcarlos78/GAIDE-Trace ~/.gaide-trace-installer 2>/dev/null || git -C ~/.gaide-trace-installer pull

2. Conecte o projeto ao servidor do time:
   ~/.gaide-trace-installer/install.sh "$PWD" --server ${origin} --token ${p.agent_token} --name ${p.name}

3. Se já existir histórico local em .gaide-trace/, envie-o ao servidor:
   python3 ~/.gaide-trace-installer/tools/backfill.py "$PWD/.gaide-trace"

4. Confirme que .gaide-trace/config.json aponta para ${origin} e me informe o resultado.
   A captura é local-first: os dados ficam no projeto e são enviados ao servidor com retry automático.`;
}

function promptReveal(p) {
  return `<div class="prompt-reveal" data-name="${esc(p.name)}">
    <div class="card-head"><span class="card-title">Install prompt — ${esc(p.name)}</span>
      <button class="btn copy-prompt" data-name="${esc(p.name)}">Copy prompt</button></div>
    <p class="helper-text">Paste this into Claude Code (or any coding agent) at the
      project root. The embedded key is ingest-only.</p>
    <textarea class="prompt-text" rows="14" readonly>${esc(installPrompt(p))}</textarea>
  </div>`;
}

async function viewProjects() {
  main.innerHTML = "";
  const create = document.createElement("div");
  create.className = "card";
  create.innerHTML = `
    <div class="card-head"><span class="card-title">Register a project</span></div>
    <p class="helper-text" style="margin-bottom:16px">Registering creates an
      ingest-only key for the project and generates a copy-paste install prompt
      for the team.</p>
    <div class="filters">
      <div class="field"><label class="microlabel">Project name</label>
        <input id="p-name" placeholder="my-app" spellcheck="false"></div>
      <button class="btn btn-accent" id="p-create">Register project</button>
    </div>
    <div id="p-new"></div>`;
  main.appendChild(create);

  const listCard = document.createElement("div");
  listCard.className = "card";
  main.appendChild(listCard);

  async function loadProjects() {
    const rows = await apiJSON("/api/v1/projects/manage");
    if (!rows.length) {
      listCard.innerHTML = `<div class="empty"><span class="glyph">◉</span>
        no projects registered yet</div>`;
      return;
    }
    listCard.innerHTML = `
      <div class="card-head"><span class="card-title">Registered projects</span></div>
      <div class="table-scroll"><table>
        <thead><tr><th>Project</th><th>Created</th><th>By</th>
          <th class="num">Sessions</th><th>Last active</th><th>Key</th><th></th></tr></thead>
        <tbody>${rows.map((p) => `<tr>
          <td style="color:var(--text-primary)">${esc(p.name)}</td>
          <td>${fmtTime(p.created_at)}</td>
          <td>${esc(p.created_by || "—")}</td>
          <td class="num">${fmt(p.sessions)}</td>
          <td>${p.last_ts ? timeAgo(p.last_ts) : "no data yet"}</td>
          <td>${p.key_revoked ? '<span class="badge warn">revoked</span>'
                              : '<span class="badge ok">active</span>'}</td>
          <td style="text-align:right">
            <button class="btn btn-ghost p-prompt" data-id="${p.id}">install prompt</button>
            <button class="btn btn-ghost p-rotate" data-id="${p.id}">rotate key</button>
            ${state.me.role === "admin"
              ? `<button class="btn btn-ghost btn-danger p-del" data-id="${p.id}">remove</button>` : ""}
          </td></tr>
          <tr class="prompt-row hidden" data-id="${p.id}"><td colspan="7"></td></tr>`).join("")}
        </tbody></table></div>`;
    const byId = Object.fromEntries(rows.map((p) => [String(p.id), p]));
    listCard.querySelectorAll(".p-prompt").forEach((b) =>
      b.addEventListener("click", () => {
        const row = listCard.querySelector(`.prompt-row[data-id="${b.dataset.id}"]`);
        row.classList.toggle("hidden");
        if (!row.classList.contains("hidden")) {
          row.firstElementChild.innerHTML = promptReveal(byId[b.dataset.id]);
          wireCopy(row);
        }
      }));
    listCard.querySelectorAll(".p-rotate").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Rotate this project's key? Machines using the old one stop " +
                     "shipping until they reinstall with the new prompt.")) return;
        await apiJSON(`/api/v1/projects/${b.dataset.id}/rotate`, { method: "POST" });
        loadProjects();
      }));
    listCard.querySelectorAll(".p-del").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Remove this project registration and revoke its key? " +
                     "Already-ingested trace data is kept.")) return;
        await api(`/api/v1/projects/${b.dataset.id}`, { method: "DELETE" });
        loadProjects();
      }));
  }

  function wireCopy(scope) {
    scope.querySelectorAll(".copy-prompt").forEach((b) =>
      b.addEventListener("click", () => {
        navigator.clipboard.writeText(scope.querySelector(".prompt-text").value);
        b.textContent = "Copied ✓";
        setTimeout(() => { b.textContent = "Copy prompt"; }, 1600);
      }));
  }

  $("#p-create").addEventListener("click", async () => {
    const name = $("#p-name").value.trim();
    if (!name) return;
    try {
      const p = await apiJSON("/api/v1/projects", {
        method: "POST", body: JSON.stringify({ name }),
      });
      $("#p-name").value = "";
      $("#p-new").innerHTML = promptReveal(p);
      wireCopy($("#p-new"));
      loadProjects();
    } catch (err) { alert(err.message); }
  });

  await loadProjects();
}

// ---------------------------------------------------------------- users

async function viewUsers() {
  if (state.me.role !== "admin") { location.hash = "#/overview"; return; }
  main.innerHTML = "";
  const create = document.createElement("div");
  create.className = "card";
  create.innerHTML = `
    <div class="card-head"><span class="card-title">Add a team member</span></div>
    <div class="filters">
      <div class="field"><label class="microlabel">Username</label>
        <input id="u-name" placeholder="alice" spellcheck="false"></div>
      <div class="field"><label class="microlabel">Role</label>
        <select id="u-role">
          <option value="member" selected>member — console + export</option>
          <option value="admin">admin — everything</option>
        </select></div>
      <button class="btn btn-accent" id="u-create">Create account</button>
    </div>
    <div id="u-reveal"></div>`;
  main.appendChild(create);

  const listCard = document.createElement("div");
  listCard.className = "card";
  main.appendChild(listCard);

  function tempPwReveal(res) {
    return `<div class="token-reveal">
      <span class="microlabel">temporary password for ${esc(res.username)} (shown once)</span>
      <code>${esc(res.temp_password)}</code>
      <span class="helper-text">they set their own on first login</span></div>`;
  }

  async function loadUsers() {
    const users = await apiJSON("/api/v1/users");
    listCard.innerHTML = `
      <div class="card-head"><span class="card-title">Accounts</span></div>
      <div class="table-scroll"><table>
        <thead><tr><th>Username</th><th>Role</th><th>Status</th><th>Last login</th>
          <th>Created</th><th></th></tr></thead>
        <tbody>${users.map((u) => `<tr>
          <td style="color:var(--text-primary)">${esc(u.username)}</td>
          <td><span class="badge">${esc(u.role)}</span></td>
          <td>${u.disabled_at ? '<span class="badge warn">disabled</span>'
              : u.must_change_password ? '<span class="badge">must change pw</span>'
              : '<span class="badge ok">active</span>'}</td>
          <td>${u.last_login_at ? timeAgo(u.last_login_at) : "never"}</td>
          <td>${fmtTime(u.created_at)}</td>
          <td style="text-align:right">
            <button class="btn btn-ghost u-reset" data-id="${u.id}">reset password</button>
            ${u.disabled_at ? "" :
              `<button class="btn btn-ghost btn-danger u-del" data-id="${u.id}">disable</button>`}
          </td></tr>`).join("")}</tbody></table></div>`;
    listCard.querySelectorAll(".u-reset").forEach((b) =>
      b.addEventListener("click", async () => {
        const res = await apiJSON(`/api/v1/users/${b.dataset.id}/reset`, { method: "POST" });
        $("#u-reveal").innerHTML = tempPwReveal(res);
        loadUsers();
      }));
    listCard.querySelectorAll(".u-del").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Disable this account? They can be re-enabled with a password reset.")) return;
        try {
          await api(`/api/v1/users/${b.dataset.id}`, { method: "DELETE" });
        } catch (err) { alert(err.message); }
        loadUsers();
      }));
  }

  $("#u-create").addEventListener("click", async () => {
    const username = $("#u-name").value.trim();
    if (!username) return;
    try {
      const res = await apiJSON("/api/v1/users", {
        method: "POST",
        body: JSON.stringify({ username, role: $("#u-role").value }),
      });
      $("#u-name").value = "";
      $("#u-reveal").innerHTML = tempPwReveal(res);
      loadUsers();
    } catch (err) { alert(err.message); }
  });

  await loadUsers();
}

// ---------------------------------------------------------------- router / auth

const VIEWS = { overview: viewOverview, sessions: viewSessions, projects: viewProjects,
                export: viewExport, users: viewUsers, keys: viewKeys };

async function route() {
  if (!state.me) return;
  const hash = location.hash || "#/overview";
  const m = hash.match(/^#\/session\/(.+)$/);
  hideTooltip();
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", hash.startsWith(a.getAttribute("href")) ||
      (m && a.dataset.view === "sessions")));
  try {
    if (m) await viewSession(decodeURIComponent(m[1]));
    else await (VIEWS[hash.slice(2)] || viewOverview)();
  } catch (err) {
    if (err.message !== "unauthorized") {
      main.innerHTML = `<div class="card"><div class="empty">
        <span class="glyph">✕</span>${esc(err.message)}</div></div>`;
    }
  }
}

function showLogin(step) {
  $("#shell").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-step-cred").classList.toggle("hidden", step === "pw");
  $("#login-step-pw").classList.toggle("hidden", step !== "pw");
  (step === "pw" ? $("#pw-current") : $("#login-user")).focus();
}

function logout() {
  if (state.token) {
    fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { Authorization: "Bearer " + state.token },
    }).catch(() => {});
  }
  localStorage.removeItem("gt_session");
  state.token = "";
  state.me = null;
  showLogin("cred");
}

function enterShell(me) {
  state.me = me;
  $("#login").classList.add("hidden");
  $("#login-error").textContent = "";
  $("#shell").classList.remove("hidden");
  $("#who").innerHTML = `${esc(me.name)} <span class="role">· ${esc(me.role)}</span>`;
  $("#server-version").textContent = "v" + me.version;
  $("#nav-keys").classList.toggle("hidden", me.role !== "admin");
  $("#nav-users").classList.toggle("hidden", me.role !== "admin");
  route();
}

async function doLogin() {
  const username = $("#login-user").value.trim();
  const password = $("#login-pass").value;
  if (!username || !password) return;
  $("#login-error").textContent = "";
  try {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    state.token = data.token;
    localStorage.setItem("gt_session", data.token);
    if (data.must_change_password) {
      $("#pw-current").value = password;   // they just proved they know it
      showLogin("pw");
    } else {
      enterShell(data);
    }
  } catch (err) {
    $("#login-error").textContent = err.message;
  } finally {
    $("#login-pass").value = "";
  }
}

async function doChangePassword() {
  const current = $("#pw-current").value;
  const pw = $("#pw-new").value;
  if (pw.length < 8) { $("#login-error").textContent = "new password needs at least 8 characters"; return; }
  if (pw !== $("#pw-confirm").value) { $("#login-error").textContent = "passwords do not match"; return; }
  $("#login-error").textContent = "";
  try {
    const res = await apiJSON("/api/v1/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: current, new_password: pw }),
    });
    state.token = res.token;               // password change re-issues the session
    localStorage.setItem("gt_session", res.token);
    ["#pw-current", "#pw-new", "#pw-confirm"].forEach((s) => { $(s).value = ""; });
    enterShell(await apiJSON("/api/v1/me"));
  } catch (err) {
    if (err.message !== "unauthorized") $("#login-error").textContent = err.message;
  }
}

async function restoreSession() {
  if (!state.token) return showLogin("cred");
  try {
    const me = await apiJSON("/api/v1/me");
    if (me.must_change_password) {
      $("#pw-current").value = "";
      showLogin("pw");
    } else {
      enterShell(me);
    }
  } catch (err) { /* apiJSON already logged out on 401 */ }
}

window.addEventListener("hashchange", route);
$("#logout").addEventListener("click", logout);
$("#login-btn").addEventListener("click", doLogin);
$("#pw-btn").addEventListener("click", doChangePassword);
["#login-user", "#login-pass"].forEach((s) =>
  $(s).addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); }));
["#pw-current", "#pw-new", "#pw-confirm"].forEach((s) =>
  $(s).addEventListener("keydown", (e) => { if (e.key === "Enter") doChangePassword(); }));

restoreSession();
