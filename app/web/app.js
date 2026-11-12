/* SwitchDeck UI — vanilla JS, no build step. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { page: "dashboard", me: null, devices: [], overview: null, drawer: null, timer: null, site: "all" };

async function api(method, path, body, opts = {}) {
  const headers = { "X-Requested-With": "switchdeck" };
  let payload;
  if (body instanceof Blob) { payload = body; Object.assign(headers, opts.headers || {}); }
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const r = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  if (r.status === 401 && !path.startsWith("/api/auth/")) { S.me = null; renderLogin(); throw new Error("login required"); }
  const data = await r.json().catch(() => ({}));
  if (r.status === 403 && data.must_change) { renderChangePassword(true); throw new Error("login required"); }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 4000);
}

const fmt = {
  bps(v) { v = +v || 0; if (v >= 1e9) return (v / 1e9).toFixed(2) + " Gbps"; if (v >= 1e6) return (v / 1e6).toFixed(1) + " Mbps"; if (v >= 1e3) return (v / 1e3).toFixed(0) + " Kbps"; return v.toFixed(0) + " bps"; },
  bytes(v) { v = +v || 0; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (v >= 1024 && i < 4) { v /= 1024; i++; } return v.toFixed(i ? 1 : 0) + " " + u[i]; },
  w(v) { return v == null ? "—" : (+v).toFixed(1) + " W"; },
  ago(ts) { if (!ts) return "never"; const s = Date.now() / 1000 - ts; if (s < 60) return Math.floor(s) + "s ago"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago"; },
  time(ts) { const d = new Date(ts * 1000); const today = new Date().toDateString() === d.toDateString(); return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); },
  dt(ts) { return new Date(ts * 1000).toLocaleString(); },
};

// ------------------------------------------------------------------ icons
const ICONS = {
  dash: "M4 13h6V4H4zM14 20h6v-9h-6zM4 20h6v-4H4zM14 4v4h6V4z",
  devices: "M3 7h18v6H3zM3 13h18v4H3zM7 10h.01M11 10h.01M15 10h.01",
  topo: "M12 4v5M6 15v-3h12v3M4 15h4v5H4zM10 15h4v5h-4zM16 15h4v5h-4zM9 4h6v5H9z",
  backup: "M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  fw: "M12 3v12M7 10l5 5 5-5M5 21h14",
  events: "M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2zM18 16V11a6 6 0 1 0-12 0v5l-2 2h16z",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  plus: "M12 5v14M5 12h14", refresh: "M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5", search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  x: "M18 6 6 18M6 6l12 12", bolt: "M13 2 3 14h9l-1 8 10-12h-9z", moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9", radar: "M12 12m-9 0a9 9 0 1 0 18 0 9 9 0 1 0-18 0M12 12l6-6M12 12m-4 0a4 4 0 1 0 8 0",
};
const icon = (n) => `<svg class="i" viewBox="0 0 24 24"><path d="${ICONS[n]}"/></svg>`;

// ------------------------------------------------------------------ modal
function modal(title, bodyHtml, { wide = false, actions = [] } = {}) {
  const bg = document.createElement("div");
  bg.className = "modal-bg";
  bg.innerHTML = `<div class="modal ${wide ? "wide" : ""}"><header>${esc(title)}</header><div class="mbody">${bodyHtml}</div>
    <footer>${actions.map((a, i) => `<button class="btn ${a.cls || ""}" data-i="${i}">${esc(a.label)}</button>`).join("")}<button class="btn" data-close>Close</button></footer></div>`;
  const close = () => bg.remove();
  bg.addEventListener("click", (e) => { if (e.target === bg || e.target.hasAttribute("data-close")) close(); });
  actions.forEach((a, i) => bg.querySelector(`[data-i="${i}"]`).addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { if ((await a.run(bg)) !== false) close(); } catch (err) { toast(err.message, "err"); } finally { e.target.disabled = false; }
  }));
  document.body.append(bg);
  const f = bg.querySelector("input,select,textarea");
  if (f) f.focus();
  return bg;
}
const confirmBox = (title, text, label = "Confirm", cls = "danger") =>
  new Promise((res) => {
    const m = modal(title, `<p>${text}</p>`, { actions: [{ label, cls: "primary " + cls, run: () => res(true) }] });
    m.querySelector("[data-close]").addEventListener("click", () => res(false));
  });

async function watchJob(job, label) {
  toast(`${label}: started`);
  for (let i = 0; i < 400; i++) {
    await new Promise((r) => setTimeout(r, 1500));
    const j = await api("GET", `/api/jobs/${job.id}`).catch(() => null);
    if (!j) continue;
    if (j.state === "done") { toast(`${label}: ${typeof j.result === "string" ? j.result : "done"}`, "ok"); refresh(); return j; }
    if (j.state === "failed") { toast(`${label} failed: ${j.error}`, "err"); refresh(); return j; }
  }
}

// ------------------------------------------------------------------ login
function renderLogin() {
  clearInterval(S.timer);
  $("#app").innerHTML = `<div class="login"><form class="card" id="lf">
    <div class="row" style="margin-bottom:18px"><div class="logo" style="width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#3b82f6,#22d3ee);display:grid;place-items:center;color:#fff;font-weight:800">SD</div>
    <div><h1>SwitchDeck</h1><div class="muted">Lantronix / Transition switch console</div></div></div>
    <label class="f"><span>Username</span><input class="input" name="u" autocomplete="username" value="admin"></label>
    <label class="f"><span>Password</span><input class="input" name="p" type="password" autocomplete="current-password"></label>
    <button class="btn primary" style="width:100%;justify-content:center">Sign in</button>
    <p class="muted" style="font-size:12px;margin:14px 0 0">New install? Sign in with <span class="mono">admin</span> / <span class="mono">admin</span>; you'll choose a new password right away. TACACS+ users sign in with their network account once an administrator enables it.</p></form></div>`;
  $("#lf").p.focus();
  $("#lf").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("POST", "/api/auth/login", { username: e.target.u.value, password: e.target.p.value });
      if (r.must_change) renderChangePassword(true, e.target.p.value); else boot();
    } catch (err) { toast(err.message, "err"); }
  });
}

function renderChangePassword(forced, current = "") {
  clearInterval(S.timer);
  closeDrawer();
  $("#app").innerHTML = `<div class="login"><form class="card" id="cpf">
    <h1 style="font-size:20px">Choose a new password</h1>
    <p class="muted" style="margin:4px 0 18px">${forced ? "This account is using a default or temporary password. Set your own to continue." : ""}</p>
    <label class="f"><span>Current password</span><input class="input" name="c" type="password" autocomplete="current-password" value="${esc(current)}"></label>
    <label class="f"><span>New password (10+ characters)</span><input class="input" name="n" type="password" autocomplete="new-password"></label>
    <label class="f"><span>Confirm new password</span><input class="input" name="n2" type="password" autocomplete="new-password"></label>
    <button class="btn primary" style="width:100%;justify-content:center">Save and continue</button>
    <button type="button" class="btn" id="cpout" style="width:100%;justify-content:center;margin-top:8px">Sign out</button></form></div>`;
  $("#cpf")[current ? "n" : "c"].focus();
  $("#cpout").onclick = async () => { await api("POST", "/api/auth/logout", {}).catch(() => {}); renderLogin(); };
  $("#cpf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.n.value !== f.n2.value) return toast("The new passwords don't match", "err");
    try { await api("POST", "/api/auth/password", { current: f.c.value, new: f.n.value }); toast("Password changed", "ok"); boot(); }
    catch (err) { toast(err.message, "err"); }
  });
}

const can = (role) => ({ viewer: 0, operator: 1, admin: 2 }[S.me?.role] ?? -1) >= ({ viewer: 0, operator: 1, admin: 2 }[role]);

// ------------------------------------------------------------------ shell
const PAGES = [["dashboard", "Dashboard", "dash"], ["devices", "Devices", "devices"], ["topology", "Topology", "topo"],
  ["backups", "Config Backups", "backup"], ["firmware", "Firmware", "fw"], ["events", "Alerts & Events", "events"], ["settings", "Settings", "settings"]];

async function boot() {
  try { S.me = await api("GET", "/api/auth/me"); } catch { return renderLogin(); }
  if (S.me.must_change) return renderChangePassword(true);
  document.body.dataset.role = S.me.role;
  $("#app").innerHTML = `<div class="shell"><nav class="rail"><div class="logo">SD</div>
    ${PAGES.map(([id, label, ic]) => `<button data-page="${id}">${icon(ic)}<span class="tip">${label}</span></button>`).join("")}
    <div class="spacer"></div>
    <button id="theme">${icon("moon")}<span class="tip">Light / dark</span></button>
    <button id="logout">${icon("logout")}<span class="tip">Sign out ${esc(S.me.user)} (${esc(S.me.role)}${S.me.source === "tacacs" ? ", TACACS+" : ""})</span></button></nav>
    <div class="main"><div class="topbar"><h1 id="title"></h1><span id="demo" class="pill ${S.me.demo ? "" : "hidden"}">Demo: simulated switches</span>
    <div class="grow"></div><select id="site"><option value="all">All sites</option></select>
    <button class="btn sm" id="rf">${icon("refresh")}Refresh</button></div><div class="content" id="content"></div></div></div>`;
  $$(".rail [data-page]").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
  $("#theme").onclick = () => {
    const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("sd-theme", t); } catch {}
  };
  $("#logout").onclick = async () => { await api("POST", "/api/auth/logout", {}).catch(() => {}); renderLogin(); };
  $("#rf").onclick = () => refresh(true);
  const sites = await api("GET", "/api/sites");
  $("#site").innerHTML += sites.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
  $("#site").onchange = (e) => { S.site = e.target.value; refresh(); };
  go(location.hash.slice(1) || "dashboard");
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (!document.hidden && !$(".modal-bg")) refresh(); }, 15000);
}

function go(page) {
  if (!PAGES.find((p) => p[0] === page)) page = "dashboard";
  S.page = page;
  history.replaceState(null, "", "#" + page);
  $$(".rail [data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $("#title").textContent = PAGES.find((p) => p[0] === page)[1];
  $("#content").innerHTML = `<div class="empty">Loading…</div>`;
  refresh();
}

async function refresh(manual) {
  try {
    await ({ dashboard: pDashboard, devices: pDevices, topology: pTopology, backups: pBackups, firmware: pFirmware, events: pEvents, settings: pSettings }[S.page])(manual);
    if (S.drawer) await drawerRefresh();
  } catch (e) { if (e.message !== "login required") toast(e.message, "err"); }
}

const bySite = (list) => (S.site === "all" ? list : list.filter((d) => d.site === S.site));

function strip(d) {
  return `<span class="strip">${(d.port_strip || []).map((p) => {
    const fast = /^(2\.5|5|10|25)G/i.test(p.speed || "");
    return `<i class="${p.up ? "up" : ""} ${p.media === "fiber" ? "fiber" : ""} ${fast ? "fast" : ""} ${p.admin ? "" : "off"}" title="Port ${p.id}${p.up ? " " + esc(p.speed) : ""}${p.poe ? " ⚡" : ""}"></i>`;
  }).join("")}</span>`;
}

// ------------------------------------------------------------------ dashboard
async function pDashboard() {
  const o = (S.overview = await api("GET", "/api/overview"));
  const list = bySite(o.list);
  const poePct = o.poe.budget_w ? Math.round((o.poe.used_w / o.poe.budget_w) * 100) : 0;
  const offline = list.filter((d) => d.status !== "online");
  $("#content").innerHTML = `
  ${o.discovered ? `<div class="card" style="margin-bottom:16px;border-color:var(--warn)"><div class="body row"><span class="dot pending"></span>
    <b>${o.discovered} switch${o.discovered > 1 ? "es" : ""} found on the network and waiting for adoption.</b><div class="grow" style="flex:1"></div>
    <button class="btn sm primary" onclick="go('devices')">Review</button></div></div>` : ""}
  <div class="grid kpis">
    <div class="card kpi"><div class="label">Switches</div><div class="value">${o.devices.online}<small> / ${o.devices.total} online</small></div>
      <div class="sub">${o.devices.offline ? `<span class="lvl-error">${o.devices.offline} offline</span>` : "All reachable"}</div></div>
    <div class="card kpi"><div class="label">Active ports</div><div class="value">${o.ports.up}<small> / ${o.ports.total}</small></div>
      <div class="bar"><i style="width:${o.ports.total ? (o.ports.up / o.ports.total) * 100 : 0}%"></i></div></div>
    <div class="card kpi"><div class="label">PoE draw</div><div class="value">${o.poe.used_w.toFixed(0)}<small> W of ${o.poe.budget_w.toFixed(0)} W</small></div>
      <div class="bar poe ${poePct > 85 ? "hot" : ""}"><i style="width:${poePct}%"></i></div></div>
    <div class="card kpi"><div class="label">Throughput</div><div class="value" style="font-size:22px">↓ ${fmt.bps(o.throughput.rx_bps)}</div><div class="sub">↑ ${fmt.bps(o.throughput.tx_bps)}</div></div>
    <div class="card kpi"><div class="label">Clients (MAC)</div><div class="value">${o.clients}</div><div class="sub">learned on access ports</div></div>
  </div>
  <div class="grid two" style="margin-top:16px">
    <div class="card"><header><h3>Switches</h3><button class="btn sm" onclick="go('devices')">Manage</button></header>
      <table class="t"><thead><tr><th></th><th>Name</th><th>Model</th><th>Ports</th><th>PoE</th><th>Throughput</th></tr></thead><tbody>
      ${list.map((d) => `<tr class="click" data-id="${d.id}"><td><span class="dot ${d.status}"></span></td><td><b>${esc(d.name)}</b><div class="faint mono">${esc(d.host)}</div></td>
        <td>${esc(d.model || d.family)}</td><td>${strip(d)}</td>
        <td>${d.poe ? `${fmt.w(d.poe.used_w)}${d.poe.budget_w ? `<div class="bar poe" style="width:90px;margin-top:4px"><i style="width:${(d.poe.used_w / d.poe.budget_w) * 100}%"></i></div>` : ""}` : "—"}</td>
        <td class="mono">↓${fmt.bps(d.rx_bps)}<br><span class="faint">↑${fmt.bps(d.tx_bps)}</span></td></tr>`).join("") || `<tr><td colspan="6" class="empty">No switches yet. Add one under Devices.</td></tr>`}
      </tbody></table></div>
    <div class="grid" style="align-content:start">
      <div class="card"><header><h3>Firmware compliance</h3><button class="btn sm" onclick="go('firmware')">Firmware</button></header><div class="body">
        ${o.firmware.map((f) => `<div class="row" style="justify-content:space-between;padding:5px 0"><span>${esc(f.model)}</span>
          <span class="pill">${f.consistent ? `<span class="dot online"></span>${esc(f.versions[0])}` : `<span class="dot pending"></span>${f.versions.length} versions`}</span></div>`).join("") || `<div class="muted">No data yet.</div>`}
      </div></div>
      <div class="card"><header><h3>Recent events</h3><button class="btn sm" onclick="go('events')">All</button></header><div class="body events">${evList(o.events)}</div></div>
    </div>
  </div>
  ${offline.length ? `<p class="lvl-error" style="margin-top:14px">Offline: ${offline.map((d) => esc(d.name)).join(", ")}</p>` : ""}`;
  $$("tr[data-id]").forEach((r) => r.addEventListener("click", () => openDevice(+r.dataset.id)));
}

function evList(evs) {
  if (!evs.length) return `<div class="muted">Nothing yet.</div>`;
  return evs.map((e) => `<div class="ev"><time>${fmt.time(e.ts)}</time><span class="dot ${e.level === "error" ? "offline" : e.level === "warning" ? "pending" : ""}"></span>
    <div><span class="lvl-${e.level}">${esc(e.message)}</span>${e.source && e.source !== "poller" ? ` <span class="faint">· ${esc(e.source)}</span>` : ""}</div></div>`).join("");
}

// ------------------------------------------------------------------ devices list
async function pDevices() {
  const [devs, disc] = await Promise.all([api("GET", "/api/devices"), api("GET", "/api/discovery")]);
  S.devices = devs;
  const q = (S.q || "").toLowerCase();
  const list = bySite(devs).filter((d) => !q || [d.name, d.host, d.model, d.firmware, d.mac, d.serial].join(" ").toLowerCase().includes(q));
  $("#content").innerHTML = `
  ${disc.length ? `<div class="card" style="margin-bottom:16px"><header><h3>Pending adoption</h3><span class="muted">${disc.length} found by discovery</span></header>
    <table class="t"><tbody>${disc.map((d) => `<tr><td><span class="dot pending"></span></td><td class="mono">${esc(d.host)}:${d.port}</td><td>${d.family === "lss" ? "LS Series" : "SISPM1040"}</td>
      <td class="muted">${esc(d.hint)}</td><td style="text-align:right"><button class="btn sm primary" data-adopt="${esc(d.host)}">Adopt</button>
      <button class="btn sm" data-dismiss="${esc(d.host)}">Dismiss</button></td></tr>`).join("")}</tbody></table></div>` : ""}
  <div class="row wrap" style="margin-bottom:14px">
    <div style="position:relative;flex:1;min-width:220px;max-width:380px"><input class="input" id="q" placeholder="Search name, IP, model, MAC, serial" value="${esc(S.q || "")}"></div>
    <div style="flex:1"></div>
    <button class="btn need-admin" id="scan">${icon("radar")}Discover</button>
    <button class="btn primary need-admin" id="add">${icon("plus")}Add switch</button></div>
  <div class="card"><table class="t"><thead><tr><th></th><th>Name</th><th>Model</th><th>IP</th><th>Firmware</th><th>Ports</th><th>PoE</th><th>CPU / Temp</th><th>Uptime</th><th>Backup</th></tr></thead><tbody>
  ${list.map((d) => `<tr class="click ${S.drawer === d.id ? "sel" : ""}" data-id="${d.id}">
    <td><span class="dot ${d.status}" title="${esc(d.status)}${d.last_error ? ": " + esc(d.last_error) : ""}"></span></td>
    <td><b>${esc(d.name)}</b><div class="faint" style="font-size:12px">${esc(d.site || "")}${d.adopted ? "" : ' · <span class="lvl-warning">not adopted</span>'}</div></td>
    <td>${esc(d.model || "—")}</td><td class="mono">${esc(d.host)}${d.port && ![80, 443].includes(d.port) ? ":" + d.port : ""}</td>
    <td class="mono">${esc((d.firmware || "—").split(" ")[0])}</td><td>${strip(d)} <span class="faint">${d.ports_up ?? "–"}/${d.ports_total ?? "–"}</span></td>
    <td>${d.poe ? fmt.w(d.poe.used_w) : "—"}</td><td>${d.cpu ?? "—"}% <span class="faint">/ ${d.temp ?? "—"}°C</span></td>
    <td class="muted">${esc(d.uptime || "—")}</td><td class="muted" data-bk="${d.id}">—</td></tr>`).join("") || `<tr><td colspan="10" class="empty">No switches match.</td></tr>`}
  </tbody></table></div>`;
  $("#q").addEventListener("input", (e) => { S.q = e.target.value; clearTimeout(S.qt); S.qt = setTimeout(() => { pDevices().then(() => { $("#q").focus(); $("#q").setSelectionRange(S.q.length, S.q.length); }); }, 250); });
  $("#add").onclick = () => addDevice();
  $("#scan").onclick = discoverModal;
  $$("tr[data-id]").forEach((r) => r.addEventListener("click", () => openDevice(+r.dataset.id)));
  $$("[data-adopt]").forEach((b) => b.addEventListener("click", () => { const d = disc.find((x) => x.host === b.dataset.adopt); addDevice(d); }));
  $$("[data-dismiss]").forEach((b) => b.addEventListener("click", async () => { await api("DELETE", `/api/discovery/${encodeURIComponent(b.dataset.dismiss)}`); refresh(); }));
  api("GET", "/api/backups").then((bks) => {
    const last = {};
    bks.forEach((b) => { if (!last[b.device_id]) last[b.device_id] = b.ts; });
    $$("[data-bk]").forEach((td) => { const t = last[td.dataset.bk]; td.innerHTML = t ? `<span class="${Date.now() / 1000 - t > 129600 ? "lvl-warning" : ""}">${fmt.ago(t)}</span>` : '<span class="lvl-warning">none</span>'; });
  });
}

async function addDevice(prefill = {}) {
  const [fams, sites, creds] = await Promise.all([api("GET", "/api/families"), api("GET", "/api/sites"), api("GET", "/api/credentials")]);
  const fam = prefill.family || "sispm";
  modal(prefill.host ? `Adopt ${prefill.host}` : "Add switch", `
    <div class="grid" style="grid-template-columns:1fr 1fr;gap:0 12px">
      <label class="f" style="grid-column:1/-1"><span>Family</span><select class="input" name="family">${fams.map((f) => `<option value="${f.id}" ${f.id === fam ? "selected" : ""}>${esc(f.label)} — ${esc(f.models.join(", "))}</option>`).join("")}</select></label>
      <label class="f"><span>IP address / hostname</span><input class="input" name="host" value="${esc(prefill.host || "")}" placeholder="192.168.1.10"></label>
      <label class="f"><span>Name</span><input class="input" name="name" placeholder="defaults to IP"></label>
      <label class="f"><span>Scheme</span><select class="input" name="scheme"><option ${prefill.scheme === "https" ? "" : "selected"}>http</option><option ${prefill.scheme === "https" || fam === "lss" ? "selected" : ""}>https</option></select></label>
      <label class="f"><span>API port</span><input class="input" name="port" value="${esc(prefill.port || (fam === "lss" ? 8000 : 80))}"></label>
      <label class="f" style="grid-column:1/-1"><span>Switch login</span><select class="input" name="credential_id">
        <option value="">Enter a username and password for this switch</option>${creds.map((c) => `<option value="${c.id}">Profile: ${esc(c.name)} (${esc(c.username)})</option>`).join("")}</select></label>
      <label class="f cred-own"><span>Username</span><input class="input" name="username" value="admin"></label>
      <label class="f cred-own"><span>Password</span><input class="input" name="password" type="password"></label>
      <label class="f" style="grid-column:1/-1"><span>Site</span><select class="input" name="site_id">${sites.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select></label>
    </div><p class="muted" style="font-size:12.5px;margin:0">SISPM1040-582-LRT needs firmware B7.20.0039+ for the API (B7.20.0191+ for HTTPS). The switch is polled immediately to verify credentials.</p>`,
    { actions: [{ label: "Add & verify", cls: "primary", run: async (m) => {
      const v = Object.fromEntries(new FormData(wrapForm(m)));
      const r = await api("POST", "/api/devices", v);
      if (r.status === "online") toast(`Connected: ${r.model || v.host}`, "ok");
      else toast(`Added, but not reachable yet: ${r.error}`, "err");
      refresh(); openDevice(r.id);
    } }] });
  const m = $(".modal-bg:last-child");
  const credSel = m.querySelector("[name=credential_id]");
  credSel.onchange = () => $$(".cred-own", m).forEach((el) => el.classList.toggle("hidden", !!credSel.value));
  if (creds.length) { credSel.value = creds[0].id; credSel.onchange(); }
  m.querySelector("[name=family]").onchange = (e) => {
    m.querySelector("[name=port]").value = e.target.value === "lss" ? 8000 : 80;
    m.querySelector("[name=scheme]").value = e.target.value === "lss" ? "https" : "http";
  };
}
function wrapForm(m) { const f = document.createElement("form"); $$("input,select,textarea", m).forEach((el) => { if (el.name) { const c = document.createElement("input"); c.name = el.name; c.value = el.value; f.append(c); } }); return f; }

function discoverModal() {
  modal("Discover switches", `<label class="f"><span>Subnet (CIDR, up to /22)</span><input class="input" name="cidr" placeholder="10.20.30.0/24"></label>
    <p class="muted" style="font-size:12.5px">Probes TCP 80/443 (SISPM1040 web/API) and 8000 (LS Series REST). No credentials are sent. Results appear under Pending adoption.</p>`,
    { actions: [{ label: "Scan", cls: "primary", run: async (m) => { const j = await api("POST", "/api/discovery", { cidr: m.querySelector("[name=cidr]").value }); watchJob(j, "Discovery"); } }] });
}

// ------------------------------------------------------------------ device drawer
async function openDevice(id, tab = "overview") {
  S.drawer = id; S.dtab = tab; S.port = null;
  if (!$(".drawer")) {
    const bg = document.createElement("div"); bg.className = "drawer-bg"; bg.onclick = closeDrawer;
    const dr = document.createElement("aside"); dr.className = "drawer";
    document.body.append(bg, dr);
  }
  await drawerRefresh();
}
function closeDrawer() { S.drawer = null; $(".drawer")?.remove(); $(".drawer-bg")?.remove(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$(".modal-bg")) closeDrawer(); });

async function drawerRefresh() {
  const dr = $(".drawer");
  if (!dr || !S.drawer) return;
  const d = (S.dev = await api("GET", `/api/devices/${S.drawer}`));
  const tabs = [["overview", "Overview"], ["ports", "Ports"], ["backups", "Backups"], ["events", "Events"], ...(can("admin") ? [["settings", "Settings"], ["api", "API explorer"]] : [])];
  const scroll = $(".dbody", dr)?.scrollTop || 0;
  dr.innerHTML = `<header><span class="dot ${d.status}"></span><div style="flex:1;min-width:0"><h2>${esc(d.name)}</h2>
    <div class="muted" style="font-size:12.5px">${esc(d.model || d.family)} · <span class="mono">${esc(d.host)}</span> · ${d.status === "online" ? "seen " + fmt.ago(d.last_seen) : esc(d.last_error || d.status)}</div></div>
    <button class="btn sm ${d.adopted ? "" : "primary"} need-admin" id="adopt" title="${d.adopted ? "Push the management baseline again (syslog target, NTP) and take a new baseline backup" : "Push the management baseline and take a baseline backup"}">${d.adopted ? "Re-adopt" : "Adopt"}</button>
    <button class="btn sm" id="dpoll">${icon("refresh")}Poll</button><button class="btn sm" id="dclose">${icon("x")}</button></header>
    <div class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${S.dtab === k ? "active" : ""}">${l}</button>`).join("")}</div>
    <div class="dbody" id="dbody"></div>`;
  $("#dclose").onclick = closeDrawer;
  $("#dpoll").onclick = async () => { try { await api("POST", `/api/devices/${d.id}/poll`); toast("Polled", "ok"); } catch (e) { toast(e.message, "err"); } drawerRefresh(); };
  if ($("#adopt")) $("#adopt").onclick = () => adoptModal(d);
  $$("[data-tab]", dr).forEach((b) => b.addEventListener("click", () => { S.dtab = b.dataset.tab; drawerRefresh(); }));
  await ({ overview: tOverview, ports: tPorts, backups: tBackups, events: tEvents, settings: tSettings, api: tApi })[S.dtab](d, $("#dbody"));
  $(".dbody", dr).scrollTop = scroll;
}

function adoptModal(d) {
  modal(`${d.adopted ? "Re-adopt" : "Adopt"} ${d.name}`, `<p>SwitchDeck will push its management baseline to this switch:</p>
    <ul class="muted"><li>Syslog target → this server</li><li>NTP servers from Settings</li><li>Save startup-config</li><li>Take a baseline config backup</li></ul>
    <label class="f"><span>Set system name on the switch (optional)</span><input class="input" name="name" value="${esc(d.name)}"></label>`,
    { actions: [{ label: d.adopted ? "Re-adopt" : "Adopt", cls: "primary", run: async (m) => watchJob(await api("POST", `/api/devices/${d.id}/adopt`, { name: m.querySelector("[name=name]").value }), `Adopt ${d.name}`) }] });
}

function faceplate(ports, selected) {
  const copper = ports.filter((p) => p.media !== "fiber");
  const fiber = ports.filter((p) => p.media === "fiber");
  const one = (p) => {
    const fast = /^(2\.5|5|10|25)G/i.test(p.speed || "");
    return `<div class="port ${p.media === "fiber" ? "sfp fiber" : ""} ${p.link_up ? "up" : ""} ${fast ? "fast" : ""} ${p.admin_up === false ? "admin-off" : ""} ${selected === p.id ? "sel" : ""}"
      data-port="${p.id}" title="Port ${p.id}${p.description ? " — " + esc(p.description) : ""}">
      ${(p.poe || {}).delivering ? '<span class="bolt">⚡</span>' : ""}<span class="n">${p.id}</span><span>${p.link_up ? esc(p.speed) : p.admin_up === false ? "off" : ""}</span>
      ${p.role === "uplink" ? '<span class="up-tag">UPLINK</span>' : ""}</div>`;
  };
  const rows = copper.length > 6 ? "" : "single";
  return `<div class="faceplate"><div class="pgroup ${rows}">${copper.map(one).join("")}</div>${fiber.length ? `<div class="pgroup single">${fiber.map(one).join("")}</div>` : ""}</div>
    <div class="legend"><span><b style="border-color:var(--ok);background:var(--ok-soft)"></b>1G</span><span><b style="border-color:var(--tengig)"></b>2.5G+</span>
    <span><b style="border-color:var(--fiber)"></b>SFP</span><span><b style="border-color:var(--line2)"></b>No link</span><span>⚡ PoE delivering</span></div>`;
}

async function tOverview(d, el) {
  const st = d.state;
  if (!st) { el.innerHTML = `<div class="empty">No data yet. ${esc(d.last_error || "")}</div>`; return; }
  const s = st.system;
  const poe = st.poe || {};
  el.innerHTML = `${faceplate(st.ports, S.port)}<div id="portpanel"></div>
  <div class="grid three" style="margin-top:18px">
    <div class="card kpi"><div class="label">CPU</div><div class="value">${s.cpu ?? "—"}<small>%</small></div><svg class="spark" id="sp-cpu"></svg></div>
    <div class="card kpi"><div class="label">PoE</div><div class="value">${poe.used_w != null ? poe.used_w.toFixed(1) : "—"}<small> W${poe.budget_w ? " / " + poe.budget_w : ""}</small></div><svg class="spark" id="sp-poe"></svg></div>
    <div class="card kpi"><div class="label">Throughput</div><div class="value" style="font-size:20px">↓${fmt.bps(st.rx_bps)}</div><svg class="spark" id="sp-rx"></svg></div>
  </div>
  <div class="grid two" style="margin-top:16px">
    <div class="card"><header><h3>System</h3></header><div class="body"><dl class="kv">
      <dt>Model</dt><dd>${esc(s.model)}</dd><dt>Description</dt><dd class="muted">${esc(s.description || "—")}</dd>
      <dt>System name</dt><dd>${esc(s.name || "—")}</dd><dt>Location</dt><dd>${esc(s.location || "—")}</dd>
      <dt>Firmware</dt><dd class="mono">${esc(s.firmware)}</dd><dt>Hardware</dt><dd>${esc(s.hardware || "—")}</dd>
      <dt>MAC</dt><dd class="mono">${esc(s.mac)}</dd><dt>Serial</dt><dd class="mono">${esc(s.serial)}</dd>
      <dt>Uptime</dt><dd>${esc(s.uptime)}</dd><dt>Temperature</dt><dd>${s.temp ?? "—"}°C${s.temp2 ? ` / ${s.temp2}°C` : ""}</dd>
      ${s.ram_total_kb ? `<dt>Memory</dt><dd>${Math.round((1 - s.ram_free_kb / s.ram_total_kb) * 100)}% of ${fmt.bytes(s.ram_total_kb * 1024)}</dd>` : ""}
      <dt>Clients</dt><dd>${st.mac_count} MAC addresses</dd></dl></div></div>
    <div class="card"><header><h3>Connected devices</h3><span class="muted">${st.neighbors.length}</span></header>
      <table class="t"><tbody>${st.neighbors.map((n) => `<tr><td class="mono">P${esc(n.port)}</td><td><b>${esc(n.name || n.mac)}</b><div class="faint" style="font-size:12px">${esc(n.model)}</div></td><td class="mono muted">${esc(n.ip)}</td></tr>`).join("") || `<tr><td class="empty">No neighbor data</td></tr>`}</tbody></table></div>
  </div>
  <div class="row" style="margin-top:16px;gap:8px"><button class="btn need-op" id="bk">${icon("backup")}Back up now</button><button class="btn need-op" id="sv">Save config</button><div style="flex:1"></div><button class="btn danger need-admin" id="rb">Reboot</button></div>`;
  $$("[data-port]", el).forEach((p) => p.addEventListener("click", () => { S.port = +p.dataset.port; $$(".port", el).forEach((x) => x.classList.toggle("sel", +x.dataset.port === S.port)); portPanel(d, $("#portpanel")); }));
  if (S.port) portPanel(d, $("#portpanel"));
  $("#bk").onclick = async () => watchJob(await api("POST", `/api/devices/${d.id}/backups`, {}), "Backup");
  $("#sv").onclick = async () => watchJob(await api("POST", `/api/devices/${d.id}/save`, {}), "Save config");
  $("#rb").onclick = async () => { if (await confirmBox(`Reboot ${d.name}?`, "Every PoE device on this switch will lose power until it boots (about 1–2 minutes).", "Reboot")) watchJob(await api("POST", `/api/devices/${d.id}/reboot`, {}), "Reboot"); };
  api("GET", `/api/devices/${d.id}/metrics?hours=6`).then((m) => { spark("#sp-cpu", m.map((x) => x.cpu)); spark("#sp-poe", m.map((x) => x.poe_w)); spark("#sp-rx", m.map((x) => x.rx_bps)); });
}

function spark(sel, vals) {
  const el = $(sel); if (!el) return;
  vals = vals.filter((v) => v != null);
  if (vals.length < 2) { el.innerHTML = ""; return; }
  const w = 300, h = 54, max = Math.max(...vals) || 1, min = Math.min(...vals, 0);
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 4 - ((v - min) / (max - min || 1)) * (h - 8)]);
  const line = "M" + pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join("L");
  el.setAttribute("viewBox", `0 0 ${w} ${h}`); el.setAttribute("preserveAspectRatio", "none");
  el.innerHTML = `<path class="a" d="${line}L${w},${h}L0,${h}Z"/><path class="l" d="${line}"/>`;
}

function portPanel(d, el) {
  const p = d.state.ports.find((x) => x.id === S.port);
  if (!p) { el.innerHTML = ""; return; }
  const caps = d.capabilities;
  el.innerHTML = `<div class="card" style="margin-top:14px"><header><h3>Port ${p.id}${p.description ? ` — ${esc(p.description)}` : ""}</h3>
    <span class="pill">${p.link_up ? `<span class="dot online"></span>${esc(p.speed)} ${esc(p.duplex || "")}` : `<span class="dot"></span>${p.admin_up === false ? "disabled" : "no link"}`}</span></header>
    <div class="body"><div class="grid two"><dl class="kv">
      <dt>Media</dt><dd>${esc(p.media)}</dd><dt>Role</dt><dd>${esc(p.role)}${p.role_manual ? "" : ' <span class="faint">(auto)</span>'}</dd>
      <dt>RX / TX now</dt><dd class="mono">${fmt.bps(p.rx_bps)} / ${fmt.bps(p.tx_bps)}</dd><dt>Total RX / TX</dt><dd class="mono">${fmt.bytes(p.rx_bytes)} / ${fmt.bytes(p.tx_bytes)}</dd>
      <dt>Errors / drops</dt><dd class="${p.rx_errors ? "lvl-warning" : ""}">${p.rx_errors} / ${p.rx_drops + p.tx_drops}</dd>
      ${p.poe ? `<dt>PoE</dt><dd>${esc(p.poe.status)} · ${fmt.w(p.poe.power_w)} · class ${esc(p.poe.class)} · ${esc(p.poe.priority)}</dd>` : ""}
      ${p.neighbor ? `<dt>Connected</dt><dd>${esc(p.neighbor.name)} <span class="faint">${esc(p.neighbor.model)} ${esc(p.neighbor.ip)}</span></dd>` : ""}
      ${p.sfp ? `<dt>SFP</dt><dd>${esc(p.sfp.vendor_name || "")} ${esc(p.sfp.vendor_pn || "")} · ${esc(p.sfp.tx_central_wavelength || "")}nm · RX ${esc(p.sfp.mon3_rx_pwr || "—")}</dd>` : ""}
    </dl><div>
      <label class="f need-op"><span>Description</span><div class="row"><input class="input" id="pdesc" value="${esc(p.description)}" maxlength="63"><button class="btn" id="psave">Set</button></div></label>
      <label class="f need-op"><span>Role for uplink / access counts</span><select class="input" id="prole"><option value="auto">Auto (${esc(p.role)})</option><option value="access" ${p.role_manual && p.role === "access" ? "selected" : ""}>Access</option><option value="uplink" ${p.role_manual && p.role === "uplink" ? "selected" : ""}>Uplink</option></select></label>
      <div class="row wrap need-op">
        ${p.poe && caps.includes("poe_cycle") ? `<button class="btn" id="pcyc">${icon("bolt")}Power-cycle PoE</button>` : ""}
        ${caps.includes("port_admin") ? `<button class="btn ${p.admin_up === false ? "" : "danger"}" id="ptog">${p.admin_up === false ? "Enable port" : "Disable port"}</button>` : ""}
        ${caps.includes("cable_test") && p.media !== "fiber" ? `<button class="btn" id="pcab">Cable test</button>` : ""}
      </div></div></div></div></div>`;
  const persist = () => new Promise((res) => {
    const m = modal("Save to startup-config?", "<p>Apply now only (lost at reboot), or apply and save to startup-config?</p>",
      { actions: [{ label: "Apply only", run: () => res(false) }, { label: "Apply & save", cls: "primary", run: () => res(true) }] });
    m.querySelector("[data-close]").addEventListener("click", () => res(null));
  });
  $("#psave", el).onclick = async () => { const s = await persist(); if (s === null) return; await api("POST", `/api/devices/${d.id}/ports/${p.id}`, { description: $("#pdesc", el).value, save: s }); toast("Description set", "ok"); drawerRefresh(); };
  $("#prole", el).onchange = async (e) => { await api("POST", `/api/devices/${d.id}/ports/${p.id}/role`, { role: e.target.value }); toast("Role saved", "ok"); drawerRefresh(); };
  if ($("#pcyc", el)) $("#pcyc", el).onclick = async () => { if (await confirmBox(`Power-cycle port ${p.id}?`, `${esc(p.neighbor?.name || "The attached device")} will lose power for 5 seconds and reboot.`, "Power-cycle", "")) watchJob(await api("POST", `/api/devices/${d.id}/ports/${p.id}/poe-cycle`, { seconds: 5 }), `PoE cycle P${p.id}`); };
  if ($("#ptog", el)) $("#ptog", el).onclick = async () => {
    const enable = p.admin_up === false;
    if (!enable && p.role === "uplink" && !(await confirmBox("Disable an uplink?", "This port looks like an uplink. Disabling it can cut off this switch, including management access.", "Disable anyway"))) return;
    if (!enable && !(await confirmBox(`Disable port ${p.id}?`, "The link goes down immediately.", "Disable"))) return;
    const s = await persist(); if (s === null) return;
    await api("POST", `/api/devices/${d.id}/ports/${p.id}`, { enabled: enable, save: s }); toast(`Port ${p.id} ${enable ? "enabled" : "disabled"}`, "ok"); drawerRefresh();
  };
  if ($("#pcab", el)) $("#pcab", el).onclick = async () => {
    const j = await watchJob(await api("POST", `/api/devices/${d.id}/ports/${p.id}/cable-test`, {}), "Cable test");
    if (j?.result) modal(`Cable test — port ${p.id}`, `<pre class="code">${esc(JSON.stringify(j.result, null, 2))}</pre>`);
  };
}

async function tPorts(d, el) {
  const st = d.state;
  if (!st) { el.innerHTML = `<div class="empty">No data yet.</div>`; return; }
  el.innerHTML = `<div class="card"><table class="t"><thead><tr><th>Port</th><th>Status</th><th>Description</th><th>Role</th><th>PoE</th><th>RX / TX</th><th>Errors</th><th>Connected</th></tr></thead><tbody>
  ${st.ports.map((p) => `<tr class="click" data-port="${p.id}"><td><b>${p.id}</b> <span class="faint">${esc(p.media)}</span></td>
    <td>${p.link_up ? `<span class="dot online"></span> ${esc(p.speed)}` : p.admin_up === false ? '<span class="lvl-warning">disabled</span>' : '<span class="faint">down</span>'}</td>
    <td>${esc(p.description || "")}</td><td>${p.role === "uplink" ? '<span class="pill">uplink</span>' : '<span class="faint">access</span>'}</td>
    <td>${p.poe ? `${p.poe.delivering ? "⚡ " : ""}${fmt.w(p.poe.power_w)}` : "—"}</td>
    <td class="mono">${fmt.bps(p.rx_bps)}<br><span class="faint">${fmt.bps(p.tx_bps)}</span></td><td class="${p.rx_errors ? "lvl-warning" : "faint"}">${p.rx_errors}</td>
    <td>${esc(p.neighbor?.name || "")}<div class="faint mono" style="font-size:11.5px">${esc(p.neighbor?.ip || "")}</div></td></tr>`).join("")}
  </tbody></table></div>`;
  $$("[data-port]", el).forEach((r) => r.addEventListener("click", () => { S.port = +r.dataset.port; S.dtab = "overview"; drawerRefresh(); }));
}

async function tBackups(d, el) {
  el.innerHTML = `<div class="row" style="margin-bottom:12px"><span class="muted" style="flex:1">${d.backups.length} snapshots kept. A new one is stored whenever the config changes (checked daily) and before restores or firmware pushes.</span>
    <button class="btn primary need-op" id="bk">${icon("backup")}Back up now</button></div>${backupTable(d.backups, false)}`;
  $("#bk").onclick = async () => watchJob(await api("POST", `/api/devices/${d.id}/backups`, {}), "Backup");
  wireBackupTable(el);
}

function backupTable(list, showDevice) {
  return `<div class="card"><table class="t"><thead><tr>${showDevice ? "<th>Switch</th>" : ""}<th>Taken</th><th>Trigger</th><th>Note</th><th>Fingerprint</th><th></th></tr></thead><tbody>
  ${list.map((b) => `<tr>${showDevice ? `<td><b>${esc(b.device)}</b><div class="faint" style="font-size:12px">${esc(b.model || "")}</div></td>` : ""}
    <td>${fmt.dt(b.ts)}<div class="faint" style="font-size:12px">${fmt.ago(b.ts)}</div></td><td><span class="pill">${esc(b.trigger)}</span></td><td class="muted">${esc(b.note || "")}</td>
    <td class="mono faint">${esc(b.sha.slice(0, 10))}</td><td style="text-align:right;white-space:nowrap"><button class="btn sm" data-view="${b.id}">View</button>
    <button class="btn sm" data-diff="${b.id}">Diff</button><a class="btn sm" href="/api/backups/${b.id}/download">JSON</a>
    <button class="btn sm need-admin" data-restore="${b.id}">Restore</button></td></tr>`).join("") || `<tr><td colspan="6" class="empty">No backups yet.</td></tr>`}</tbody></table></div>`;
}

function wireBackupTable(el) {
  $$("[data-view]", el).forEach((b) => b.addEventListener("click", async () => {
    const bk = await api("GET", `/api/backups/${b.dataset.view}`);
    modal(`Backup #${bk.id} — ${bk.device}`, `<pre class="code">${esc(bk.content)}</pre>`, { wide: true });
  }));
  $$("[data-diff]", el).forEach((b) => b.addEventListener("click", async () => {
    const r = await api("GET", `/api/backups/${b.dataset.diff}/diff`);
    const html = r.diff ? r.diff.split("\n").map((l) => `<span class="${l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l)}</span>`).join("\n") : esc(r.note || "Identical to the previous backup.");
    modal(`Changes in backup #${b.dataset.diff}`, `<pre class="code diff">${html}</pre>`, { wide: true });
  }));
  $$("[data-restore]", el).forEach((b) => b.addEventListener("click", () => restoreModal(+b.dataset.restore)));
}

async function restoreModal(id) {
  const bk = await api("GET", `/api/backups/${id}`);
  const snap = JSON.parse(bk.content);
  const restorable = ["sysinfo", "ports", "poe", "poe_auto_reset", "vlan", "mirror", "ntp", "syslog", "time", "di_do"].filter((k) => snap[k] && !snap[k]._unsupported);
  if (snap._family !== "sispm") { modal("Restore", "<p>Restore is available for the SISPM1040 family. For LS Series switches, use the JSON backup as a reference until the write endpoints are confirmed on your firmware.</p>"); return; }
  modal(`Restore backup #${id} to ${bk.device}`, `<p class="muted">A safety backup of the current config is taken first. Selected sections are written, then saved to startup-config.</p>
    ${restorable.map((k) => `<label class="row" style="padding:4px 0"><input type="checkbox" name="s" value="${k}" checked> ${k}</label>`).join("")}
    <label class="row" style="padding:4px 0;margin-top:6px"><input type="checkbox" name="ip"> <span>Also restore IP address <span class="lvl-warning">(can make the switch unreachable)</span></span></label>`,
    { actions: [{ label: "Restore", cls: "primary danger", run: async (m) => {
      const sections = $$("[name=s]:checked", m).map((c) => c.value);
      watchJob(await api("POST", `/api/backups/${id}/restore`, { sections, include_ip: m.querySelector("[name=ip]").checked }), "Restore");
    } }] });
}

async function tEvents(d, el) { el.innerHTML = `<div class="card"><div class="body events">${evList(d.events)}</div></div>`; }

async function tSettings(d, el) {
  const [sites, creds] = await Promise.all([api("GET", "/api/sites"), api("GET", "/api/credentials")]);
  const s = d.state?.system || {};
  el.innerHTML = `<div class="grid two">
  <div class="card"><header><h3>Inventory</h3></header><div class="body">
    <label class="f"><span>Display name</span><input class="input" name="name" value="${esc(d.name)}"></label>
    <label class="f"><span>Site</span><select class="input" name="site_id">${sites.map((x) => `<option value="${x.id}" ${x.id === d.site_id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
    <div class="grid" style="grid-template-columns:2fr 1fr 1fr;gap:0 10px"><label class="f"><span>Host</span><input class="input" name="host" value="${esc(d.host)}"></label>
    <label class="f"><span>Port</span><input class="input" name="port" value="${esc(d.port)}"></label><label class="f"><span>Scheme</span><select class="input" name="scheme"><option ${d.scheme === "http" ? "selected" : ""}>http</option><option ${d.scheme === "https" ? "selected" : ""}>https</option></select></label></div>
    <label class="f"><span>Switch login</span><select class="input" name="credential_id"><option value="">This switch's own username / password</option>
      ${creds.map((c) => `<option value="${c.id}" ${c.id === d.credential_id ? "selected" : ""}>Profile: ${esc(c.name)} (${esc(c.username)})</option>`).join("")}</select></label>
    <div class="cred-own ${d.credential_id ? "hidden" : ""}"><label class="f"><span>API username</span><input class="input" name="username" value="${esc(d.username)}"></label>
    <label class="f"><span>New API password (leave blank to keep)</span><input class="input" name="password" type="password"></label></div>
    <button class="btn primary" id="sinv">Save</button></div></div>
  <div class="card"><header><h3>On the switch</h3></header><div class="body">
    <label class="f"><span>System name</span><input class="input" name="sname" value="${esc(s.name || "")}"></label>
    <label class="f"><span>Location</span><input class="input" name="sloc" value="${esc(s.location || "")}"></label>
    <label class="f"><span>Contact</span><input class="input" name="scon" value="${esc(s.contact || "")}"></label>
    <button class="btn" id="sid">Apply to switch</button>
    <hr style="border:0;border-top:1px solid var(--line);margin:20px 0">
    <button class="btn danger" id="del">Remove from SwitchDeck</button><p class="muted" style="font-size:12px">Removes the switch and its backups from this console. Nothing changes on the switch.</p></div></div></div>`;
  const v = (n) => el.querySelector(`[name=${n}]`).value;
  el.querySelector("[name=credential_id]").onchange = (e) => $(".cred-own", el).classList.toggle("hidden", !!e.target.value);
  $("#sinv").onclick = async () => {
    const body = { name: v("name"), site_id: +v("site_id"), host: v("host"), port: +v("port"), scheme: v("scheme"), credential_id: v("credential_id") || null };
    if (!body.credential_id) { body.username = v("username"); if (v("password")) body.password = v("password"); }
    await api("PATCH", `/api/devices/${d.id}`, body); toast("Saved", "ok"); refresh();
  };
  $("#sid").onclick = async () => { await api("POST", `/api/devices/${d.id}/identity`, { name: v("sname"), location: v("sloc"), contact: v("scon") }); toast("Applied (not yet saved to startup-config)", "ok"); drawerRefresh(); };
  $("#del").onclick = async () => { if (await confirmBox(`Remove ${d.name}?`, "Its stored backups are deleted too.", "Remove")) { await api("DELETE", `/api/devices/${d.id}`); closeDrawer(); refresh(); } };
}

async function tApi(d, el) {
  let eps = { get: [], post: [] };
  try { eps = await api("GET", `/api/devices/${d.id}/endpoints`); } catch (e) { /* offline */ }
  el.innerHTML = `<p class="muted" style="margin-top:0">Call the switch API directly with SwitchDeck's stored session. Use it to confirm field names on new firmware. Every call is audited; write calls need confirmation.</p>
    <div class="row" style="margin-bottom:10px"><select class="input" id="am" style="width:100px"><option>GET</option><option>POST</option></select>
    <input class="input mono" id="ap" list="eps" placeholder="${d.family === "lss" ? "sysinfo/" : "/api/get_sysinfo"}"><datalist id="eps">${[...eps.get, ...eps.post].map((e) => `<option value="${esc(e)}">`).join("")}</datalist>
    <button class="btn primary" id="ago">Send</button></div>
    <textarea class="input mono" id="ab" rows="5" placeholder='JSON body for POST' style="display:none"></textarea>
    <pre class="code" id="ar" style="margin-top:12px">—</pre>`;
  $("#am").onchange = (e) => ($("#ab").style.display = e.target.value === "POST" ? "" : "none");
  $("#ago").onclick = async () => {
    const method = $("#am").value;
    let body;
    if (method === "POST") {
      try { body = JSON.parse($("#ab").value || "{}"); } catch { return toast("Body is not valid JSON", "err"); }
      if (!(await confirmBox("Send a write call?", `${esc(method)} ${esc($("#ap").value)} changes the switch configuration.`, "Send"))) return;
    }
    $("#ar").textContent = "…";
    try { const r = await api("POST", `/api/devices/${d.id}/raw`, { method, path: $("#ap").value, body, confirm: true }); $("#ar").textContent = JSON.stringify(r.response, null, 2); }
    catch (e) { $("#ar").textContent = e.message; }
  };
}

// ------------------------------------------------------------------ topology
async function pTopology() {
  const devs = bySite(await api("GET", "/api/devices"));
  const full = await Promise.all(devs.map((d) => api("GET", `/api/devices/${d.id}`).catch(() => d)));
  const colW = Math.max(200, 1100 / Math.max(full.length, 1));
  const leafRows = Math.max(1, ...full.map((d) => (d.state?.neighbors || []).length));
  const H = 190 + leafRows * 34;
  const W = Math.max(colW * full.length, 600);
  let svg = `<svg viewBox="0 0 ${W} ${H}" style="min-width:${Math.min(W, 1600)}px"><rect class="node online" x="${W / 2 - 80}" y="10" width="160" height="44" rx="8"/>
    <text x="${W / 2}" y="30" text-anchor="middle" font-weight="600">SwitchDeck</text><text class="sub" x="${W / 2}" y="46" text-anchor="middle">management</text>`;
  full.forEach((d, i) => {
    const cx = colW * i + colW / 2;
    svg += `<path class="edge" d="M${W / 2},54 C${W / 2},90 ${cx},70 ${cx},110"/>`;
    svg += `<g data-id="${d.id}" style="cursor:pointer"><rect class="node ${d.status}" x="${cx - 85}" y="110" width="170" height="50" rx="8"/>
      <text x="${cx}" y="130" text-anchor="middle" font-weight="600">${esc(d.name.slice(0, 22))}</text><text class="sub" x="${cx}" y="148" text-anchor="middle">${esc((d.model || d.family).slice(0, 24))}</text></g>`;
    (d.state?.neighbors || []).forEach((n, j) => {
      const y = 190 + j * 34;
      svg += `<path class="edge" d="M${cx - 60},160 V${y + 4} H${cx - 50}"/><circle class="leaf" cx="${cx - 44}" cy="${y + 4}" r="5"/>
        <text x="${cx - 34}" y="${y + 8}">${esc((n.name || n.mac || "").slice(0, 18))}</text><text class="sub" x="${cx + 80}" y="${y + 8}" text-anchor="end">P${esc(n.port)}</text>`;
    });
  });
  svg += "</svg>";
  $("#content").innerHTML = `<div class="card topo"><header><h3>Physical topology</h3><span class="muted">Switch neighbor tables (Transition DMS device list / LLDP)</span></header><div class="body" style="overflow:auto">${full.length ? svg : '<div class="empty">No switches.</div>'}</div></div>`;
  $$("g[data-id]").forEach((g) => g.addEventListener("click", () => openDevice(+g.dataset.id)));
}

// ------------------------------------------------------------------ backups page
async function pBackups() {
  const list = await api("GET", "/api/backups");
  const devs = bySite(await api("GET", "/api/devices"));
  const ids = new Set(devs.map((d) => d.id));
  $("#content").innerHTML = `<div class="row" style="margin-bottom:14px"><p class="muted" style="flex:1;margin:0">Configuration snapshots are normalized JSON pulled through each switch's API, with volatile fields removed so diffs show real changes only.</p>
    <button class="btn primary need-op" id="all">${icon("backup")}Back up all now</button></div>${backupTable(list.filter((b) => ids.has(b.device_id)), true)}`;
  $("#all").onclick = async () => watchJob(await api("POST", "/api/backups/run-all", {}), "Backup all");
  wireBackupTable($("#content"));
}

// ------------------------------------------------------------------ firmware
async function pFirmware() {
  const [fw, devs] = await Promise.all([api("GET", "/api/firmware"), api("GET", "/api/devices")]);
  const groups = {};
  bySite(devs).forEach((d) => (groups[d.model || d.family] ||= []).push(d));
  $("#content").innerHTML = `<div class="grid two">
  <div class="card"><header><h3>Fleet versions</h3></header><table class="t"><thead><tr><th>Model</th><th>Switch</th><th>Firmware</th></tr></thead><tbody>
    ${Object.entries(groups).map(([m, ds]) => { const vs = new Set(ds.map((d) => d.firmware)); return ds.map((d, i) => `<tr>${i === 0 ? `<td rowspan="${ds.length}"><b>${esc(m)}</b><div class="${vs.size > 1 ? "lvl-warning" : "faint"}" style="font-size:12px">${vs.size > 1 ? vs.size + " versions" : "consistent"}</div></td>` : ""}
      <td><span class="dot ${d.status}"></span> ${esc(d.name)}</td><td class="mono">${esc(d.firmware || "—")}</td></tr>`).join(""); }).join("")}
  </tbody></table></div>
  <div class="card"><header><h3>Firmware library</h3><button class="btn sm primary need-admin" id="up">${icon("plus")}Upload</button></header>
    <table class="t"><tbody>${fw.map((f) => `<tr><td><b>${esc(f.version || f.filename.split("/")[1])}</b><div class="faint mono" style="font-size:11.5px">${esc(f.filename.split("/")[1])} · ${fmt.bytes(f.size)}</div>
      <div class="faint" style="font-size:12px">${esc(f.models.join(", ") || "any model")}</div></td>
      <td style="text-align:right;white-space:nowrap"><button class="btn sm primary need-admin" data-push="${f.id}">Push…</button> <button class="btn sm danger need-admin" data-del="${f.id}">Delete</button></td></tr>`).join("") || '<tr><td class="empty">Upload images downloaded from Lantronix support.</td></tr>'}</tbody></table>
    <div class="body muted" style="font-size:12.5px">Switches pull images over HTTP from this server's firmware port. A config backup is taken before each upgrade, and switches are upgraded one at a time.</div></div></div>`;
  $("#up").onclick = () => modal("Upload firmware", `<label class="f"><span>Image file</span><input class="input" type="file" name="file"></label>
      <label class="f"><span>Version label</span><input class="input" name="version" placeholder="B7.20.0215"></label>
      <label class="f"><span>Models (comma-separated)</span><input class="input" name="models" placeholder="SISPM1040-582-LRT"></label>`,
    { actions: [{ label: "Upload", cls: "primary", run: async (m) => {
      const file = m.querySelector("[name=file]").files[0]; if (!file) throw new Error("choose a file");
      await api("POST", "/api/firmware", file, { headers: { "X-Filename": encodeURIComponent(file.name), "X-Version": encodeURIComponent(m.querySelector("[name=version]").value), "X-Models": encodeURIComponent(m.querySelector("[name=models]").value), "Content-Type": "application/octet-stream" } });
      toast("Uploaded", "ok"); refresh();
    } }] });
  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => { if (await confirmBox("Delete image?", "This removes the file from the library.", "Delete")) { await api("DELETE", `/api/firmware/${b.dataset.del}`); refresh(); } }));
  $$("[data-push]").forEach((b) => b.addEventListener("click", () => {
    const f = fw.find((x) => x.id === +b.dataset.push);
    const elig = devs.filter((d) => d.family === "sispm" && (!f.models.length || f.models.includes(d.model)));
    modal(`Push ${f.version || f.filename}`, `<p class="muted">Only SISPM1040 switches${f.models.length ? " of the listed models" : ""} are shown. Each switch reboots as part of the upgrade.</p>
      ${elig.map((d) => `<label class="row" style="padding:5px 0"><input type="checkbox" name="d" value="${d.id}"> <span class="dot ${d.status}"></span> ${esc(d.name)} <span class="faint mono">${esc(d.firmware)}</span></label>`).join("") || '<div class="empty">No eligible switches.</div>'}`,
      { actions: [{ label: "Upgrade selected", cls: "primary", run: async (m) => {
        const ids = $$("[name=d]:checked", m).map((c) => +c.value); if (!ids.length) throw new Error("select at least one switch");
        watchJob(await api("POST", `/api/firmware/${f.id}/push`, { device_ids: ids }), "Firmware push");
      } }] });
  }));
}

// ------------------------------------------------------------------ events
async function pEvents() {
  const tab = S.etab || "events";
  const rows = tab === "events" ? await api("GET", "/api/events?limit=400") : await api("GET", "/api/audit");
  const lvl = S.elvl || "all";
  $("#content").innerHTML = `<div class="tabs" style="margin:-8px 0 14px"><button data-t="events" class="${tab === "events" ? "active" : ""}">Events</button><button data-t="audit" class="${tab === "audit" ? "active" : ""}">Audit log</button></div>
  ${tab === "events" ? `<div class="row" style="margin-bottom:12px">${["all", "error", "warning", "info"].map((l) => `<button class="btn sm ${l === lvl ? "primary" : ""}" data-l="${l}">${l}</button>`).join("")}</div>
    <div class="card"><table class="t"><thead><tr><th>Time</th><th>Level</th><th>Switch</th><th>Source</th><th>Message</th></tr></thead><tbody>
    ${rows.filter((e) => lvl === "all" || e.level === lvl).map((e) => `<tr><td class="muted" style="white-space:nowrap">${fmt.time(e.ts)}</td><td class="lvl-${e.level}">${e.level}</td><td>${esc(e.device || "—")}</td><td class="faint">${esc(e.source)}</td><td>${esc(e.message)}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">No events.</td></tr>'}</tbody></table></div>`
  : `<div class="card"><table class="t"><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>
    ${rows.map((a) => `<tr><td class="muted" style="white-space:nowrap">${fmt.time(a.ts)}</td><td>${esc(a.user)}</td><td><b>${esc(a.action)}</b></td><td>${esc(a.target)}</td><td class="mono faint" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.detail)}</td></tr>`).join("")}</tbody></table></div>`}`;
  $$("[data-t]").forEach((b) => b.addEventListener("click", () => { S.etab = b.dataset.t; refresh(); }));
  $$("[data-l]").forEach((b) => b.addEventListener("click", () => { S.elvl = b.dataset.l; refresh(); }));
}

// ------------------------------------------------------------------ settings
async function pSettings(manual) {
  if ($("#settings-root") && !manual) return; // don't clobber forms on auto-refresh
  const admin = can("admin");
  const [s, sites, tokens, users, tac, creds] = await Promise.all([
    api("GET", "/api/settings"), api("GET", "/api/sites"),
    admin ? api("GET", "/api/tokens") : [], admin ? api("GET", "/api/users") : [],
    admin ? api("GET", "/api/auth/tacacs") : null, admin ? api("GET", "/api/credentials") : []]);
  const local = S.me.source !== "tacacs";
  $("#content").innerHTML = `<div id="settings-root" class="grid two">
  <div class="grid" style="align-content:start">
    <div class="card"><header><h3>Your account</h3><span class="pill">${esc(S.me.user)} · ${esc(S.me.role)}${local ? "" : " · TACACS+"}</span></header><div class="body">
      ${local ? `<label class="f"><span>Current password</span><input class="input" type="password" id="pc"></label>
      <label class="f"><span>New password (10+ characters)</span><input class="input" type="password" id="pn"></label>
      <button class="btn" id="pwb">Change password</button>` : `<p class="muted" style="margin:0">Your password is managed on the TACACS+ server, and your role comes from it at each sign-in.</p>`}</div></div>
    ${admin ? `
    <div class="card"><header><h3>Users</h3><button class="btn sm primary" id="adduser">${icon("plus")}Local user</button></header>
      <table class="t"><thead><tr><th>User</th><th>Role</th><th>Source</th><th>Last login</th><th></th></tr></thead><tbody>
      ${users.map((u) => `<tr><td><b>${esc(u.username)}</b>${u.full_name ? `<div class="faint" style="font-size:12px">${esc(u.full_name)}</div>` : ""}${u.disabled ? ' <span class="pill lvl-error">disabled</span>' : ""}${u.must_change ? ' <span class="pill lvl-warning">must change pw</span>' : ""}</td>
        <td>${esc(u.role)}</td><td class="muted">${u.source === "tacacs" ? "TACACS+" : "local"}</td><td class="muted">${fmt.ago(u.last_login)}</td>
        <td style="text-align:right;white-space:nowrap">${u.username === S.me.user ? '<span class="faint">you</span>' : `<button class="btn sm" data-edituser="${esc(u.username)}">Edit</button>`}</td></tr>`).join("")}
      </tbody></table>
      <div class="body muted" style="font-size:12.5px">Roles: <b>viewer</b> sees everything; <b>operator</b> also power-cycles PoE, changes ports, saves configs and takes backups; <b>admin</b> also adopts, reboots, restores, upgrades firmware and manages settings. Keep at least one local admin as break-glass access.</div></div>
    <div class="card"><header><h3>TACACS+ authentication</h3><span class="pill">${tac.enabled ? '<span class="dot online"></span>enabled' : '<span class="dot"></span>off'}</span></header><div class="body" id="tacform">
      <label class="row" style="margin-bottom:12px"><input type="checkbox" name="enabled" ${tac.enabled ? "checked" : ""}> Allow sign-in with TACACS+ accounts</label>
      <label class="f"><span>Servers, in order (host or host:port, one per line; default port 49)</span><textarea class="input mono" name="servers" rows="2">${esc(tac.servers.join("\n"))}</textarea></label>
      <div class="grid" style="grid-template-columns:2fr 1fr 1fr;gap:0 10px">
        <label class="f"><span>Shared key ${tac.key_set ? "(set — leave blank to keep)" : ""}</span><input class="input" name="key" type="password" placeholder="${tac.key_set ? "••••••••" : ""}"></label>
        <label class="f"><span>Login type</span><select class="input" name="mode"><option value="pap" ${tac.mode === "pap" ? "selected" : ""}>PAP</option><option value="ascii" ${tac.mode === "ascii" ? "selected" : ""}>ASCII</option></select></label>
        <label class="f"><span>Timeout (s)</span><input class="input" name="timeout" type="number" value="${esc(tac.timeout)}"></label></div>
      <div class="grid" style="grid-template-columns:1fr 1fr 1fr;gap:0 10px">
        <label class="f"><span>Admin at priv-lvl ≥</span><input class="input" name="admin_level" type="number" value="${esc(tac.admin_level)}"></label>
        <label class="f"><span>Operator at priv-lvl ≥</span><input class="input" name="operator_level" type="number" value="${esc(tac.operator_level)}"></label>
        <label class="f"><span>Role if no priv-lvl</span><select class="input" name="default_role">${["viewer", "operator", "admin"].map((r) => `<option ${tac.default_role === r ? "selected" : ""}>${r}</option>`).join("")}</select></label></div>
      <div class="grid" style="grid-template-columns:1fr 1fr;gap:0 10px">
        <label class="f"><span>Authorization service</span><input class="input" name="service" value="${esc(tac.service)}"></label>
        <label class="f"><span>Local accounts</span><select class="input" name="local_fallback">
          <option value="always" ${tac.local_fallback === "always" ? "selected" : ""}>Always allowed (checked first)</option>
          <option value="unreachable" ${tac.local_fallback === "unreachable" ? "selected" : ""}>Only when TACACS+ is unreachable</option></select></label></div>
      <div class="row wrap"><button class="btn primary" id="tacsave">Save</button><button class="btn" id="tactest">Test a login…</button></div>
      <p class="muted" style="font-size:12.5px;margin-bottom:0">Add SwitchDeck as a network device (client) on your TACACS+ server with the same shared key. Setup steps for Cisco ISE and tac_plus are in docs/AUTHENTICATION.md.</p></div></div>` : ""}
  </div>
  <div class="grid" style="align-content:start">
    ${admin ? `
    <div class="card"><header><h3>Switch credential profiles</h3><button class="btn sm primary" id="addcred">${icon("plus")}Profile</button></header>
      <table class="t"><tbody>${creds.map((c) => `<tr><td><b>${esc(c.name)}</b><div class="faint" style="font-size:12px">${esc(c.notes || "")}</div></td><td class="mono">${esc(c.username)}</td><td class="muted">${c.devices} switches</td>
        <td style="text-align:right"><button class="btn sm" data-editcred="${c.id}">Edit</button></td></tr>`).join("") || '<tr><td class="empty">No profiles. Each switch uses its own login.</td></tr>'}</tbody></table>
      <div class="body muted" style="font-size:12.5px">One shared, encrypted login for many switches (for example a dedicated service account). Change the password here once and every switch using the profile picks it up on its next poll.</div></div>
    <div class="card"><header><h3>Management baseline</h3></header><div class="body">
      <label class="f"><span>Address switches use to reach SwitchDeck (syslog target, firmware downloads)</span><input class="input" id="ph" value="${esc(s.public_host || "")}" placeholder="auto: ${esc(s.detected_host)}"></label>
      <label class="f"><span>NTP servers pushed at adoption (comma-separated, up to 5)</span><input class="input" id="ntp" value="${esc((s.ntp_servers || []).join(", "))}"></label>
      <label class="f"><span>Backups kept per switch</span><input class="input" id="ret" type="number" value="${esc(s.backup_retention)}"></label>
      <p class="muted" style="font-size:12.5px">Polling every ${s.poll_interval}s · change-detect backups every ${Math.round(s.backup_interval / 3600)}h · syslog UDP ${s.syslog_port} · firmware HTTP ${s.firmware_port}</p>
      <button class="btn primary" id="sset">Save</button></div></div>
    <div class="card"><header><h3>Sites</h3><button class="btn sm" id="asite">${icon("plus")}Add site</button></header>
      <table class="t"><tbody>${sites.map((x) => `<tr><td>${esc(x.name)}</td><td class="muted">${x.devices} switches</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><header><h3>Integrations · Dashboards</h3><button class="btn sm primary" id="ntok">${icon("plus")}New API token</button></header>
      <div class="body"><p class="muted" style="margin-top:0">Read-only bearer tokens for Home Assistant or other dashboards. They can read <span class="mono">/api/v1/summary</span>, <span class="mono">/api/v1/devices</span> and <span class="mono">/api/v1/events</span> and nothing else.</p></div>
      <table class="t"><tbody>${tokens.map((t) => `<tr><td><b>${esc(t.name)}</b><div class="faint mono" style="font-size:11.5px">${esc(t.prefix)}…</div></td><td class="muted">used ${fmt.ago(t.last_used)}</td><td style="text-align:right"><button class="btn sm danger" data-rev="${t.id}">Revoke</button></td></tr>`).join("") || '<tr><td class="empty">No tokens yet.</td></tr>'}</tbody></table>
      <div class="body"><b>Home Assistant RESTful sensor</b><p class="muted" style="font-size:12.5px;margin:6px 0">Put <span class="mono">switchdeck_auth: "Bearer sdk_…"</span> in Home Assistant <span class="mono">secrets.yaml</span>, then add to configuration (or a package):</p>
      <pre class="code" id="hayaml"></pre><button class="btn sm" id="cpy" style="margin-top:8px">Copy YAML</button></div></div>` : `<div class="card"><div class="body muted">Server settings, users and integrations are managed by administrators.</div></div>`}
  </div></div>`;

  if (local) $("#pwb").onclick = async () => { await api("POST", "/api/auth/password", { current: $("#pc").value, new: $("#pn").value }); toast("Password changed", "ok"); $("#pc").value = $("#pn").value = ""; };
  if (!admin) return;

  const yaml = haYaml(location.protocol + "//" + (s.public_host ? s.public_host + ":" + location.port : location.host));
  $("#hayaml").textContent = yaml;
  $("#cpy").onclick = () => navigator.clipboard.writeText(yaml).then(() => toast("Copied", "ok"));
  $("#sset").onclick = async () => {
    await api("PUT", "/api/settings", { public_host: $("#ph").value.trim(), ntp_servers: $("#ntp").value.split(",").map((x) => x.trim()).filter(Boolean).slice(0, 5), backup_retention: +$("#ret").value || 60 });
    toast("Settings saved", "ok"); pSettings(true);
  };
  $("#asite").onclick = () => modal("Add site", `<label class="f"><span>Name</span><input class="input" name="n"></label>`, { actions: [{ label: "Add", cls: "primary", run: async (m) => { await api("POST", "/api/sites", { name: m.querySelector("[name=n]").value }); pSettings(true); } }] });
  $("#ntok").onclick = () => modal("New API token", `<label class="f"><span>Name</span><input class="input" name="n" value="Home Assistant"></label>`, { actions: [{ label: "Create", cls: "primary", run: async (m) => {
    const r = await api("POST", "/api/tokens", { name: m.querySelector("[name=n]").value });
    setTimeout(() => modal("Copy your token now", `<p>This is the only time it's shown.</p><pre class="code">${esc(r.token)}</pre><p class="muted" style="font-size:12.5px">secrets.yaml line:</p><pre class="code">switchdeck_auth: "Bearer ${esc(r.token)}"</pre>`), 50);
    pSettings(true);
  } }] });
  $$("[data-rev]").forEach((b) => b.addEventListener("click", async () => { if (await confirmBox("Revoke token?", "Anything using it stops working immediately.", "Revoke")) { await api("DELETE", `/api/tokens/${b.dataset.rev}`); pSettings(true); } }));

  // users
  const roleSel = (cur) => `<select class="input" name="role">${["viewer", "operator", "admin"].map((r) => `<option ${cur === r ? "selected" : ""}>${r}</option>`).join("")}</select>`;
  $("#adduser").onclick = () => modal("Add local user", `<label class="f"><span>Username</span><input class="input" name="username"></label>
    <label class="f"><span>Full name (optional)</span><input class="input" name="full_name"></label>
    <label class="f"><span>Role</span>${roleSel("viewer")}</label>
    <label class="f"><span>Temporary password (10+ characters)</span><input class="input" name="password" type="password"></label>
    <label class="row"><input type="checkbox" name="must_change" checked> Require a new password at first sign-in</label>`,
    { actions: [{ label: "Add user", cls: "primary", run: async (m) => {
      const v = (n) => m.querySelector(`[name=${n}]`);
      await api("POST", "/api/users", { username: v("username").value, full_name: v("full_name").value, role: v("role").value, password: v("password").value, must_change: v("must_change").checked });
      toast("User added", "ok"); pSettings(true);
    } }] });
  $$("[data-edituser]").forEach((b) => b.addEventListener("click", () => {
    const u = users.find((x) => x.username === b.dataset.edituser);
    const tac = u.source === "tacacs";
    modal(`User ${u.username}`, `${tac ? `<p class="muted">TACACS+ account. Its role (${esc(u.role)}) is refreshed from the TACACS+ server at each sign-in; you can disable or remove it here.</p>` : `
      <label class="f"><span>Full name</span><input class="input" name="full_name" value="${esc(u.full_name || "")}"></label>
      <label class="f"><span>Role</span>${roleSel(u.role)}</label>
      <label class="f"><span>Reset password (leave blank to keep; user must change it at next sign-in)</span><input class="input" name="password" type="password"></label>`}
      <label class="row"><input type="checkbox" name="disabled" ${u.disabled ? "checked" : ""}> Disabled (signs the user out and blocks sign-in)</label>`,
      { actions: [{ label: "Delete user", cls: "danger", run: async () => {
        if (!(await confirmBox(`Delete ${u.username}?`, "Their sessions end immediately.", "Delete"))) return false;
        await api("DELETE", `/api/users/${encodeURIComponent(u.username)}`); toast("User deleted", "ok"); pSettings(true);
      } }, { label: "Save", cls: "primary", run: async (m) => {
        const v = (n) => m.querySelector(`[name=${n}]`);
        const body = { disabled: v("disabled").checked };
        if (!tac) { body.full_name = v("full_name").value; body.role = v("role").value; if (v("password").value) body.password = v("password").value; }
        await api("PATCH", `/api/users/${encodeURIComponent(u.username)}`, body); toast("Saved", "ok"); pSettings(true);
      } }] });
  }));

  // TACACS+
  const tf = (n) => $(`#tacform [name=${n}]`);
  const tacBody = () => ({ enabled: tf("enabled").checked, servers: tf("servers").value, key: tf("key").value, mode: tf("mode").value,
    timeout: +tf("timeout").value || 5, admin_level: +tf("admin_level").value, operator_level: +tf("operator_level").value,
    default_role: tf("default_role").value, service: tf("service").value || "shell", local_fallback: tf("local_fallback").value });
  $("#tacsave").onclick = async () => {
    const body = tacBody();
    if (body.enabled && body.local_fallback === "unreachable" && !(await confirmBox("Make TACACS+ authoritative?", "Local accounts (including admin) will only work while no TACACS+ server answers. Test a TACACS+ admin login first.", "Save", ""))) return;
    try { await api("PUT", "/api/auth/tacacs", body); toast("TACACS+ settings saved", "ok"); pSettings(true); } catch (e) { toast(e.message, "err"); }
  };
  $("#tactest").onclick = () => modal("Test a TACACS+ login", `<p class="muted">Uses the values in the form, saved or not. Nothing is changed and no session is created.</p>
    <label class="f"><span>Username</span><input class="input" name="u"></label><label class="f"><span>Password</span><input class="input" name="p" type="password"></label><pre class="code" id="tres">—</pre>`,
    { actions: [{ label: "Test", cls: "primary", run: async (m) => {
      const r = await api("POST", "/api/auth/tacacs/test", { ...tacBody(), username: m.querySelector("[name=u]").value, password: m.querySelector("[name=p]").value });
      m.querySelector("#tres").textContent = r.ok ? `ACCEPTED by ${r.server}\nattributes: ${JSON.stringify(r.attributes)}\nSwitchDeck role: ${r.role}` : `${r.result.toUpperCase()}${r.detail ? ": " + r.detail : r.message ? ": " + r.message : ""}`;
      return false;
    } }] });

  // credential profiles
  const credForm = (c = {}) => `<label class="f"><span>Profile name</span><input class="input" name="name" value="${esc(c.name || "")}" placeholder="e.g. Switch service account"></label>
    <label class="f"><span>Username on the switches</span><input class="input" name="username" value="${esc(c.username || "")}"></label>
    <label class="f"><span>Password ${c.id ? "(leave blank to keep)" : ""}</span><input class="input" name="password" type="password"></label>
    <label class="f"><span>Notes</span><input class="input" name="notes" value="${esc(c.notes || "")}"></label>`;
  const credVals = (m) => Object.fromEntries(["name", "username", "password", "notes"].map((n) => [n, m.querySelector(`[name=${n}]`).value]));
  $("#addcred").onclick = () => modal("New credential profile", credForm(), { actions: [{ label: "Create", cls: "primary", run: async (m) => { await api("POST", "/api/credentials", credVals(m)); toast("Profile created", "ok"); pSettings(true); } }] });
  $$("[data-editcred]").forEach((b) => b.addEventListener("click", () => {
    const c = creds.find((x) => x.id === +b.dataset.editcred);
    modal(`Profile ${c.name}`, credForm(c) + `<p class="muted" style="font-size:12.5px">Used by ${c.devices} switch(es).</p>`, { actions: [
      { label: "Delete", cls: "danger", run: async () => { await api("DELETE", `/api/credentials/${c.id}`); toast("Deleted", "ok"); pSettings(true); } },
      { label: "Save", cls: "primary", run: async (m) => { const v = credVals(m); if (!v.password) delete v.password; await api("PATCH", `/api/credentials/${c.id}`, v); toast("Saved", "ok"); pSettings(true); } }] });
  }));
}

function haYaml(base) {
  const keys = ["devices_total", "devices_online", "devices_offline", "ports_total", "ports_active", "access_ports_total", "access_ports_active",
    "uplinks_total", "uplinks_up", "uplinks_down", "poe_used_w", "poe_budget_w", "poe_pct", "poe_ports_powered", "cameras", "clients",
    "rx_mbps", "tx_mbps", "backups_current", "backups_stale", "config_changes_24h", "alerts_24h", "errors_24h", "firmware_mismatch_models",
    "offline_devices", "models", "sites", "firmware", "devices", "updated"];
  return `rest:
  - resource: ${base}/api/v1/summary
    headers:
      Authorization: !secret switchdeck_auth
    verify_ssl: false
    scan_interval: 60
    timeout: 15
    sensor:
      - name: "Lantronix Switches"
        unique_id: switchdeck_exec
        icon: mdi:switch
        value_template: "{{ value_json.health }}"
        json_attributes:
${keys.map((k) => "          - " + k).join("\n")}
      - name: "Lantronix Switches Online"
        unique_id: switchdeck_devices_online
        value_template: "{{ value_json.devices_online }}"
        unit_of_measurement: "switches"
      - name: "Lantronix Active Access Ports"
        unique_id: switchdeck_access_ports_active
        value_template: "{{ value_json.access_ports_active }}"
        unit_of_measurement: "ports"
      - name: "Lantronix PoE Draw"
        unique_id: switchdeck_poe_used
        value_template: "{{ value_json.poe_used_w }}"
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement`;
}

boot();
