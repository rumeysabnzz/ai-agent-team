/* BOLBÉ AI Komuta Merkezi — arayüz */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (b) => b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`;
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
const fmtUsd = (n) => `$${Number(n || 0).toFixed(2)}`;
const fileUrl = (area, name, download = false) => `/api/files/${area}/${encodeURIComponent(name)}${download ? "?download=1" : ""}`;
const KIND_ICON = { image: "🖼", video: "🎬", audio: "🎧", text: "📄", pdf: "📕", other: "📦" };
const ACT_COLOR = { upload: "var(--cyan)", report: "var(--gold)", run: "var(--purple)", done: "var(--green)", media: "var(--cyan)", approval: "var(--gold)", alert: "var(--red)", error: "var(--red)", delete: "var(--orange)", info: "var(--muted)" };
const RUN_STATE = { queued: ["Sırada", "orange"], running: ["Çalışıyor", "cyan"], done: ["Tamamlandı", "green"], failed: ["Hata", "red"] };
const TOOL_LABEL = {
  web_search: "Web'de arıyor", list_files: "Dosyalara bakıyor", read_file: "Dosya okuyor", save_report: "Rapor kaydediyor",
  save_media_from_url: "Medyayı kaydediyor", submit_for_approval: "Onaya gönderiyor", raise_alert: "Uyarı veriyor",
  virale_accounts_list: "Virale hesaplarını alıyor", virale_accounts_add: "Rakip hesabı ekliyor", virale_accounts_get: "Hesap verisi alıyor",
  virale_videos_list: "İçerikleri tarıyor", virale_videos_get: "İçerik ayrıntısı alıyor", virale_recommendations: "Öneri alıyor",
  virale_image_generate: "Görsel üretiyor", virale_carousel_generate: "Carousel üretiyor", virale_video_generate: "Video üretiyor",
  virale_generation_status: "Üretimi kontrol ediyor", virale_ai_balance: "Bakiyeyi kontrol ediyor",
};

const state = { teams: [], agents: {}, live: {}, feed: [], es: null };

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 401 && !path.startsWith("/api/login")) { showLogin(); throw new Error("Giriş gerekli"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* metin değil */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.text();
}
const sendJSON = (method, path, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function renderMarkdown(md) {
  if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(md || ""));
  return `<pre class="markdown">${esc(md)}</pre>`;
}
const chip = (label, color) => `<span class="chip chip-${color}">${esc(label)}</span>`;
const runChip = (status) => { const [l, c] = RUN_STATE[status] || [status, "gray"]; return chip(l, c); };
const agentName = (id) => state.agents[id]?.name || id;
const teamOf = (agentId) => state.teams.find((t) => t.agents.some((a) => a.id === agentId));

/* ------------------------------------------------------------ giriş */

function showLogin() {
  if (state.es) { state.es.close(); state.es = null; }
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#password").focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await sendJSON("POST", "/api/login", { password: $("#password").value });
    $("#password").value = "";
    startApp();
  } catch (ex) { $("#login-error").textContent = ex.message; }
});

const logout = async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); };
$("#logout").addEventListener("click", logout);
$("#logout-m").addEventListener("click", logout);

/* ------------------------------------------------------------ tema (açık / koyu) */

const themeLabel = () => (document.documentElement.dataset.theme === "dark" ? "☀ Açık tema" : "☾ Koyu tema");
const syncThemeButtons = () => {
  $("#theme-toggle").textContent = document.documentElement.dataset.theme === "dark" ? "☀" : "☾";
  $("#theme-toggle").title = themeLabel();
  $("#theme-m").textContent = themeLabel();
};
const toggleTheme = () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  if (dark) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
  try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch {}
  $('meta[name="theme-color"]').content = dark ? "#0d0d16" : "#f7f5f0";
  syncThemeButtons();
  window.dispatchEvent(new Event("themechange"));
};
$("#theme-toggle").addEventListener("click", toggleTheme);
$("#theme-m").addEventListener("click", toggleTheme);
syncThemeButtons();
$("#nav-more").addEventListener("click", () => ($("#more-sheet").hidden = false));
$("#more-sheet").addEventListener("click", () => ($("#more-sheet").hidden = true));

/* ------------------------------------------------------------ canlı olaylar */

function describe(ev) {
  switch (ev.type) {
    case "agent_start": return "Çalışmaya başladı";
    case "thinking": return ev.text;
    case "text": return ev.text;
    case "tool": return `${TOOL_LABEL[ev.tool] || ev.tool}${ev.detail && ev.tool === "web_search" ? ": " + ev.detail.replace(/^\{"query":\s*"|"\}$/g, "") : ""}`;
    case "tool_error": return `Araç hatası: ${ev.detail}`;
    case "agent_note": return ev.text;
    case "agent_done": return "Tamamladı";
    default: return "";
  }
}
const evClass = (t) => ({ thinking: "k-thinking", tool: "k-tool", tool_error: "k-error", agent_done: "k-done" }[t] || "");

function connectStream() {
  if (state.es) state.es.close();
  const es = new EventSource("/api/stream");
  state.es = es;
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    handleEvent(ev);
  };
}

function handleEvent(ev) {
  if (ev.agent) {
    if (ev.type === "agent_done") delete state.live[ev.agent];
    else state.live[ev.agent] = { run: ev.run_id, text: describe(ev), time: ev.time };
  }
  if (ev.type === "run_end") {
    for (const [k, v] of Object.entries(state.live)) if (v.run === ev.run_id) delete state.live[k];
    toast(ev.status === "done" ? "Çalıştırma tamamlandı" : `Çalıştırma hata verdi: ${ev.error || ""}`);
    refreshBadge();
  }
  if (ev.type === "approval") { toast(`Onaya yeni içerik: ${ev.item.title}`); refreshBadge(); }
  if (ev.type === "alert") toast(`⚠ ${ev.alert.level.toUpperCase()}: ${ev.alert.title}`);
  if (ev.agent && ["thinking", "text", "tool", "tool_error", "agent_start", "agent_done", "agent_note"].includes(ev.type)) {
    state.feed.unshift(ev);
    state.feed.length = Math.min(state.feed.length, 120);
  }
  document.dispatchEvent(new CustomEvent("live", { detail: ev }));
}

async function refreshBadge() {
  try {
    const items = await api("/api/approvals");
    const n = items.filter((i) => i.status === "pending").length;
    const b = $("#badge-onay");
    b.hidden = !n; b.textContent = n;
  } catch { /* yok */ }
}

/* ------------------------------------------------------------ yönlendirme */

const routes = { "": pageHome, akislar: pageWorkflows, calisma: pageRun, onay: pageApprovals, raporlar: pageReports,
  galeri: pageGallery, dosyalar: pageFiles, ayarlar: pageSettings };
let cleanup = null;
const params = () => new URLSearchParams(location.hash.split("?")[1] || "");

function route() {
  const key = location.hash.replace(/^#\/?/, "").split("?")[0];
  const page = routes[key] || pageHome;
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (key || "home") || (key === "calisma" && a.dataset.route === "akislar")));
  $("#more-sheet").hidden = true;
  if (cleanup) { cleanup(); cleanup = null; }
  view.innerHTML = "";
  window.scrollTo(0, 0);
  cleanup = page() || null;
}
window.addEventListener("hashchange", route);

async function startApp() {
  try { await api("/api/me"); } catch { return; }
  const ov = await api("/api/overview").catch(() => null);
  if (ov) {
    state.teams = ov.teams;
    state.agents = Object.fromEntries(ov.teams.flatMap((t) => t.agents.map((a) => [a.id, { ...a, team: t.id, color: t.color }])));
    for (const r of ov.live) if (r.current_agent) state.live[r.current_agent] = { run: r.id, text: "Çalışıyor", time: r.created };
  }
  $("#login").hidden = true;
  $("#app").hidden = false;
  connectStream();
  refreshBadge();
  route();
}

function onLive(fn) {
  const h = (e) => fn(e.detail);
  document.addEventListener("live", h);
  return () => document.removeEventListener("live", h);
}

/* ------------------------------------------------------------ ana ekran */

function pageHome() {
  view.innerHTML = `
    <section class="hero">
      <div class="brand-mark">BOLBÉ SKINCARE</div>
      <div class="hero-sub">AI TAKIM KOMUTA MERKEZİ</div>
      <div class="status-row" id="status-row"></div>
    </section>
    <section class="stats-bar" id="stats"></section>
    <section class="hub-area" id="hub"></section>
    <section class="grid-2">
      <div class="card"><div class="card-title">📡 Canlı akış <span class="muted" style="font-weight:400;font-size:.8rem;margin-left:auto">ajanların şu an yaptıkları</span></div>
        <div class="feed" id="feed"></div></div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card" id="alerts-card"><div class="card-title">⚠ Erken uyarılar</div><div id="alerts"></div></div>
        <div class="card"><div class="card-title">⏳ Son çalıştırmalar <a href="#/akislar" class="btn btn-sm" style="margin-left:auto">Akışlar →</a></div><div class="run-list" id="runs"></div></div>
      </div>
    </section>
    <section class="card" style="margin-top:16px"><div class="card-title">🗂 Aktivite</div><div id="activity" class="activity-log"></div></section>`;

  const drawFeed = () => {
    const el = $("#feed"); if (!el) return;
    el.innerHTML = state.feed.length ? state.feed.slice(0, 60).map((ev) => `
      <div class="feed-row ${evClass(ev.type)}"><span class="log-time">${fmtTime(ev.time)}</span>
      <div><span class="who" style="color:${state.agents[ev.agent]?.color || "var(--gold)"}">${esc(agentName(ev.agent))}</span>
      <div class="what">${esc(describe(ev))}</div></div></div>`).join("")
      : `<div class="empty">Şu an çalışan ajan yok. <a href="#/akislar">Akışlar</a> sayfasından bir iş başlatabilirsiniz.</div>`;
  };

  const load = async () => {
    let d;
    try { d = await api("/api/overview"); } catch (e) { return toast(e.message); }
    if (!$("#stats")) return;
    const pct = d.budget.limit ? Math.min(100, (d.budget.spent / d.budget.limit) * 100) : 0;
    const anyLive = Object.keys(state.live).length > 0;
    $("#status-row").innerHTML = `
      <span class="pill ${d.integrations.claude ? "ok" : "bad"} ${anyLive ? "live" : ""}"><span class="dot"></span>${d.integrations.claude ? (anyLive ? "Ekip çalışıyor" : "Ekip hazır") : "Claude API anahtarı yok"}</span>
      <span class="pill ${d.integrations.virale ? "ok" : "warn"}"><span class="dot"></span>${d.integrations.virale ? "Virale bağlı" : "Virale bağlı değil"}</span>
      <span class="pill ${pct >= 90 ? "bad" : pct >= 70 ? "warn" : "ok"}"><span class="dot"></span>Bütçe ${fmtUsd(d.budget.spent)} / ${fmtUsd(d.budget.limit)}</span>`;
    const s = d.stats;
    $("#stats").innerHTML = [["#/onay", s.pending, "Onay bekleyen", "--brand"], ["#/raporlar", s.reports, "Rapor", "--purple"], ["#/galeri", s.media, "Görsel / video", "--cyan"], ["#/", s.alerts, "Okunmamış uyarı", "--red"]]
      .map(([h, n, l, c]) => `<a class="stat-item" href="${h}" style="--stat-c:var(${c})"><div class="stat-num">${n}</div><div class="stat-label">${l}</div></a>`).join("");
    renderHub();
    $("#alerts").innerHTML = d.alerts.length ? d.alerts.map(alertRow).join("") + (s.alerts ? `<button class="btn btn-sm" id="alerts-read">Tümünü okundu say</button>` : "") : `<div class="empty">Uyarı yok</div>`;
    $("#alerts-read")?.addEventListener("click", async () => { await api("/api/alerts/read", { method: "POST" }); load(); });
    $("#runs").innerHTML = d.runs.length ? d.runs.map(runRow).join("") : `<div class="empty">Henüz çalıştırma yok</div>`;
    $("#activity").innerHTML = d.activity.length ? d.activity.map((a) => `
      <div class="log-entry"><span class="log-time">${fmtTime(a.time)}</span><span class="log-dot" style="background:${ACT_COLOR[a.kind] || ACT_COLOR.info}"></span>
      <span class="log-msg">${esc(a.message)}</span></div>`).join("") : `<div class="empty">Aktivite yok</div>`;
  };

  drawFeed(); load();
  const offLive = onLive((ev) => {
    updateAgentRows();
    if (ev.agent) drawFeed();
    if (["run_end", "approval", "alert", "run_start"].includes(ev.type)) load();
  });
  const timer = setInterval(load, 30000);
  const onResize = () => drawConnections();
  window.addEventListener("resize", onResize);
  return () => { offLive(); clearInterval(timer); window.removeEventListener("resize", onResize); };
}

function alertRow(a) {
  const c = { kritik: "red", "önemli": "orange", bilgi: "cyan" }[a.level] || "gray";
  return `<div class="alert-row" style="--lvl:var(--${c === "gray" ? "muted" : c})${a.read ? ";opacity:.6" : ""}"><span class="lvl">${chip(a.level, c)}</span>
    <div><b>${esc(a.title)}</b><p>${esc(a.detail)}</p><small class="muted">${esc(a.agent)} · ${fmtDate(a.time)}</small></div></div>`;
}

function runRow(r) {
  const who = r.current_agent ? ` · ${esc(agentName(r.current_agent))}` : "";
  const icon = r.status === "running" || r.status === "queued" ? `<span class="spinner"></span>` : `<span>${r.kind === "task" ? "◆" : "⟳"}</span>`;
  return `<a class="run-row" href="#/calisma?id=${r.id}">${icon}<div><b>${esc(r.title)}</b>
    <small>${fmtDate(r.created)}${who}${r.trigger === "schedule" ? " · zamanlanmış" : ""}${r.error ? " · " + esc(r.error) : ""}</small></div>
    <span class="cost">${fmtUsd(r.cost)}</span>${runChip(r.status)}</a>`;
}

function renderHub() {
  const hub = $("#hub");
  if (!hub) return;
  hub.innerHTML = `
    <svg class="connections-svg" id="conn-svg"></svg>
    <div class="hub-center">
      <div class="hub-ring"></div><div class="hub-ring"></div><div class="hub-ring"></div>
      <div class="hub-icon">🧭</div><div class="hub-name">BOLBÉ AI</div><div class="hub-role">4 takım · 12 ajan</div>
    </div>
    <div class="teams-grid">${state.teams.map((t, i) => `
      <div class="team-node pos-${i}" data-team="${t.id}">
        <div class="node-card" style="--card-color:${t.color}"><div class="node-inner">
          <div class="node-header"><span class="node-icon">${t.icon}</span><span class="node-title">${esc(t.name)}</span><span class="team-chip"></span></div>
          <div class="node-summary">${esc(t.summary)}</div>
          ${t.agents.map((a) => `<div class="agent-row" data-agent="${a.id}" style="color:${t.color}">
            <span class="agent-dot" style="background:${t.color}"></span><span class="agent-name">${esc(a.name)}</span>${chip(a.tag, "gray")}
            <span class="agent-act"></span></div>`).join("")}
          <div class="node-actions"><a class="btn btn-sm" href="#/akislar?takim=${t.id}">Akışlar →</a></div>
        </div></div>
      </div>`).join("")}</div>`;
  updateAgentRows();
  requestAnimationFrame(drawConnections);
}

function updateAgentRows() {
  $$(".agent-row[data-agent]").forEach((row) => {
    const live = state.live[row.dataset.agent];
    row.classList.toggle("active", !!live);
    row.querySelector(".agent-act").textContent = live ? live.text : "";
  });
  $$(".team-node").forEach((node) => {
    const busy = $$(".agent-row.active", node).length > 0;
    node.querySelector(".node-card").classList.toggle("busy", busy);
    node.querySelector(".team-chip").innerHTML = busy ? chip("Çalışıyor", "cyan") : chip("Hazır", "gray");
  });
}

function drawConnections() {
  const svg = $("#conn-svg"), hub = $("#hub");
  if (!svg || !hub || getComputedStyle(svg).display === "none") return;
  const box = hub.getBoundingClientRect();
  const cx = box.width / 2, cy = box.height / 2;
  svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
  svg.innerHTML = $$(".team-node", hub).map((node) => {
    const r = node.getBoundingClientRect();
    const x = r.left - box.left + r.width / 2, y = r.top - box.top + r.height / 2;
    const color = getComputedStyle(node.querySelector(".node-card")).getPropertyValue("--card-color").trim() || "#e9e186";
    const mx = (cx + x) / 2 + (y - cy) * 0.15, my = (cy + y) / 2 - (x - cx) * 0.15;
    const d = `M${cx},${cy} Q${mx},${my} ${x},${y}`;
    return `<path class="conn-path" d="${d}" stroke="${color}"/>
      <circle r="3" fill="${color}" opacity=".85"><animateMotion dur="${3 + Math.random() * 2}s" repeatCount="indefinite" path="${d}"/></circle>`;
  }).join("");
}

/* ------------------------------------------------------------ akışlar */

function pageWorkflows() {
  const focusTeam = params().get("takim");
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Akışlar</h1>
      <p class="page-desc">Takımların düzenli işleri. Zamanı gelince kendiliğinden başlarlar; istediğiniz an elle de başlatabilirsiniz.</p></div>
      <button class="btn btn-gold" id="toggle-task">◆ Ajana görev ver</button></div>
    <div class="card" id="task-card" hidden style="margin-bottom:16px">
      <form class="task-form" id="task-form">
        <div class="form-row">
          <div class="field"><label for="t-agent">Ajan</label><select id="t-agent">${state.teams.map((t) => `<optgroup label="${esc(t.name)}">${t.agents.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</optgroup>`).join("")}</select></div>
          <div class="field"><label for="t-title">Başlık</label><input type="text" id="t-title" required minlength="2" maxlength="160" placeholder="Ör. Bariyer serumu lansman metinleri"></div>
        </div>
        <div class="field"><label for="t-prompt">Görev</label><textarea id="t-prompt" required minlength="5" placeholder="Ör. Yeni bariyer serumumuz için 3 farklı kancalı reel açıklaması yaz; ton bilimsel ama sıcak olsun."></textarea></div>
        <div class="field"><label>Ek dosyalar (isteğe bağlı)</label><div class="attach-list" id="t-attach"><span class="muted">Yükleniyor…</span></div></div>
        <div style="display:flex;gap:10px;justify-content:flex-end"><button type="button" class="btn btn-ghost" id="cancel-task">Vazgeç</button><button type="submit" class="btn btn-gold" id="t-submit">Görevi başlat</button></div>
      </form>
    </div>
    <div class="wf-grid" id="wf-grid"></div>
    <div class="card" style="margin-top:16px"><div class="card-title">⏳ Çalıştırma geçmişi</div><div class="run-list" id="run-list"></div></div>`;

  const taskCard = $("#task-card");
  $("#toggle-task").addEventListener("click", async () => {
    taskCard.hidden = !taskCard.hidden;
    if (taskCard.hidden) return;
    const uploads = await api("/api/files/uploads").catch(() => []);
    $("#t-attach").innerHTML = uploads.length ? uploads.map((f) => `<label><input type="checkbox" value="${esc(f.name)}">${KIND_ICON[f.kind]} ${esc(f.name)}</label>`).join("") : `<span class="muted">Yüklenmiş dosya yok</span>`;
  });
  $("#cancel-task").addEventListener("click", () => (taskCard.hidden = true));
  $("#task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#t-submit").disabled = true;
    try {
      const run = await sendJSON("POST", `/api/agents/${$("#t-agent").value}/task`, {
        title: $("#t-title").value.trim(), prompt: $("#t-prompt").value.trim(), attachments: $$("#t-attach input:checked").map((i) => i.value) });
      location.hash = `#/calisma?id=${run.id}`;
    } catch (ex) { toast(ex.message); } finally { $("#t-submit").disabled = false; }
  });

  const load = async () => {
    const [wfs, runs] = await Promise.all([api("/api/workflows").catch(() => []), api("/api/runs").catch(() => [])]);
    const grid = $("#wf-grid"); if (!grid) return;
    grid.innerHTML = wfs.map((w) => {
      const team = state.teams.find((t) => t.id === w.team) || {};
      const last = w.last_run;
      return `<div class="card wf-card" style="--wf-color:${team.color}" id="wf-${w.id}">
        <div class="wf-head"><span class="wf-icon">${team.icon || "⟳"}</span><h3>${esc(w.name)}<br><small class="muted" style="font-weight:400">${esc(team.name)}</small></h3>
          ${last ? runChip(last.status) : chip("Henüz çalışmadı", "gray")}</div>
        <div class="wf-steps">${w.steps.map((s) => `<span class="chip chip-gray">${esc(s.name)}</span>`).join('<span class="arrow">→</span>')}</div>
        <div class="muted" style="font-size:.85rem">${esc(w.brief)}</div>
        <div class="wf-meta"><span>🗓 ${esc(w.schedule)}</span>${last ? `<span>Son: <a href="#/calisma?id=${last.id}">${fmtDate(last.created)}</a> · ${fmtUsd(last.cost)}</span>` : ""}</div>
        <div class="wf-actions">
          <label class="switch"><input type="checkbox" data-sched="${w.id}" ${w.enabled ? "checked" : ""}>Otomatik</label>
          <input type="text" placeholder="Not (isteğe bağlı)" data-note="${w.id}" style="flex:1;min-width:140px;padding:8px 10px">
          <button class="btn btn-gold btn-sm" data-run="${w.id}" ${last && ["running", "queued"].includes(last.status) ? "disabled" : ""}>▶ Şimdi çalıştır</button>
        </div></div>`;
    }).join("");
    $("#run-list").innerHTML = runs.length ? runs.slice(0, 30).map(runRow).join("") : `<div class="empty">Henüz çalıştırma yok</div>`;
    if (focusTeam) $(`.wf-card[style*="${state.teams.find((t) => t.id === focusTeam)?.color}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  $("#wf-grid").addEventListener("click", async (e) => {
    const id = e.target.closest("[data-run]")?.dataset.run; if (!id) return;
    e.target.disabled = true;
    try {
      const run = await sendJSON("POST", `/api/workflows/${id}/run`, { note: $(`[data-note="${id}"]`).value.trim() });
      toast("Akış başladı"); location.hash = `#/calisma?id=${run.id}`;
    } catch (ex) { toast(ex.message); e.target.disabled = false; }
  });
  $("#wf-grid").addEventListener("change", async (e) => {
    const id = e.target.dataset.sched; if (!id) return;
    try { await sendJSON("PUT", `/api/workflows/${id}/schedule`, { enabled: e.target.checked }); toast(e.target.checked ? "Otomatik çalışma açıldı" : "Otomatik çalışma kapatıldı"); }
    catch (ex) { toast(ex.message); e.target.checked = !e.target.checked; }
  });
  load();
  const off = onLive((ev) => { if (["run_start", "run_end"].includes(ev.type)) load(); });
  return off;
}

/* ------------------------------------------------------------ çalıştırma ayrıntısı */

function pageRun() {
  const id = params().get("id");
  view.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  let data = null;

  const draw = () => {
    if (!data) return;
    const groups = [];
    for (const ev of data.events) {
      if (!ev.agent) continue;
      if (!groups.length || groups[groups.length - 1].agent !== ev.agent || ev.type === "agent_start") groups.push({ agent: ev.agent, rows: [] });
      if (ev.type !== "agent_start") groups[groups.length - 1].rows.push(ev);
    }
    const outputs = data.outputs.map((o) => o.type === "report" ? `<a class="btn btn-sm" href="#/raporlar?r=${encodeURIComponent(o.name)}">▤ ${esc(o.name)}</a>`
      : o.type === "media" ? `<a class="btn btn-sm" href="${fileUrl("media", o.name)}" target="_blank" rel="noopener">▣ ${esc(o.name)}</a>`
      : `<a class="btn btn-sm" href="#/onay">✓ ${esc(o.title)}</a>`).join("");
    view.innerHTML = `
      <div class="page-head"><div><h1 class="page-title">${esc(data.title)}</h1>
        <p class="page-desc">${fmtDate(data.created)} · ${data.trigger === "schedule" ? "Zamanlanmış" : "Elle başlatıldı"} · ${fmtUsd(data.cost)}
        ${data.tokens ? ` · ${(data.tokens.input / 1000).toFixed(0)}K girdi / ${(data.tokens.output / 1000).toFixed(0)}K çıktı token` : ""}</p></div>
        <div style="display:flex;gap:8px;align-items:center">${runChip(data.status)}<a class="btn btn-sm" href="#/akislar">← Akışlar</a></div></div>
      <div class="card" style="margin-bottom:16px"><div class="wf-steps">${data.steps.map((s) => {
        const done = data.step_results?.some((r) => r.agent === s); const cur = data.current_agent === s;
        return `<span class="chip ${cur ? "chip-cyan" : done ? "chip-green" : "chip-gray"}">${cur ? '<span class="spinner" style="width:10px;height:10px"></span>' : done ? "✓" : ""} ${esc(agentName(s))}</span>`;
      }).join('<span class="arrow">→</span>')}</div>
        ${data.error ? `<p style="color:var(--red);margin-top:10px">${esc(data.error)}</p>` : ""}
        ${outputs ? `<div class="card-title" style="margin-top:14px">Çıktılar</div><div class="outputs">${outputs}</div>` : ""}</div>
      <div class="card"><div class="card-title">Zaman çizelgesi</div><div class="timeline">${groups.map((g) => `
        <div class="tl-agent" style="color:${state.agents[g.agent]?.color || "var(--gold)"}">● ${esc(agentName(g.agent))}</div>
        ${g.rows.map((ev) => `<div class="tl-row ${evClass(ev.type)}"><span class="log-time">${fmtTime(ev.time)}</span><div class="what">${ev.type === "text" || ev.type === "agent_done" ? renderMarkdown(ev.text || describe(ev)) : esc(describe(ev))}</div></div>`).join("")}`).join("")
        || `<div class="empty">${data.status === "queued" ? "Sırada bekliyor…" : "Olay yok"}</div>`}</div></div>`;
  };

  const load = async () => {
    try { data = await api(`/api/runs/${id}`); draw(); } catch (ex) { view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
  };
  load();
  return onLive((ev) => {
    if (ev.run_id !== id || !data) return;
    if (["run_end", "agent_done"].includes(ev.type)) return load();
    if (ev.type === "agent_start") data.current_agent = ev.agent;
    data.events.push(ev);
    draw();
  });
}

/* ------------------------------------------------------------ onay kuyruğu */

function pageApprovals() {
  let filter = "pending";
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Onay</h1>
      <p class="page-desc">Ekibin ürettiği, yayına hazır içerikler. Onaylayın, revize isteyin ya da reddedin.</p></div>
      <div class="tabs" id="ap-tabs"><button class="tab active" data-f="pending">Bekleyen</button><button class="tab" data-f="approved">Onaylanan</button>
      <button class="tab" data-f="revision">Revizede</button><button class="tab" data-f="rejected">Reddedilen</button></div></div>
    <div class="approval-grid" id="ap-grid"></div>`;

  let items = [];
  const draw = () => {
    const shown = items.filter((i) => i.status === filter);
    $("#ap-grid").innerHTML = shown.length ? shown.map((i) => `
      <div class="card ap-card" data-id="${i.id}">
        <div class="ap-head">${chip(i.kind, { reel: "orange", story: "purple", carousel: "cyan", "görsel": "green" }[i.kind] || "gold")}
          <span class="muted" style="font-size:.8rem">${esc(i.agent_name)} · ${fmtDate(i.created)}</span><h3>${esc(i.title)}</h3></div>
        ${i.media.length ? `<div class="ap-media">${i.media.map((m) => /\.(mp4|mov|webm|m4v)$/i.test(m)
          ? `<video src="${fileUrl("media", m)}#t=0.5" preload="metadata" muted playsinline data-lb="${esc(m)}" data-k="video"></video>`
          : `<img src="${fileUrl("media", m)}" loading="lazy" alt="${esc(m)}" data-lb="${esc(m)}" data-k="image">`).join("")}</div>` : ""}
        <div class="ap-body markdown">${renderMarkdown(i.body)}</div>
        ${i.note ? `<div class="ap-note">Not: ${esc(i.note)}</div>` : ""}
        <div class="ap-actions">
          <button class="btn btn-sm" data-copy="${i.id}">⧉ Metni kopyala</button>
          ${i.status === "pending" ? `<button class="btn btn-sm btn-green" data-act="approve">✓ Onayla</button>
          <button class="btn btn-sm" data-act="revise">↻ Revize iste</button><button class="btn btn-sm btn-danger" data-act="reject">✕ Reddet</button>` : ""}
        </div></div>`).join("") : `<div class="empty" style="grid-column:1/-1">Bu bölümde içerik yok</div>`;
  };
  const load = async () => { items = await api("/api/approvals").catch(() => []); draw(); };

  $("#ap-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab"); if (!b) return;
    $$(".tab", $("#ap-tabs")).forEach((t) => t.classList.toggle("active", t === b));
    filter = b.dataset.f; draw();
  });
  $("#ap-grid").addEventListener("click", async (e) => {
    const card = e.target.closest(".ap-card"); if (!card) return;
    const item = items.find((i) => i.id === card.dataset.id);
    const lb = e.target.closest("[data-lb]");
    if (lb) return openLightbox(lb.dataset.lb, lb.dataset.k);
    if (e.target.closest("[data-copy]")) {
      try { await navigator.clipboard.writeText(item.body); toast("Metin kopyalandı"); } catch { toast("Kopyalanamadı"); }
      return;
    }
    const act = e.target.closest("[data-act]")?.dataset.act; if (!act) return;
    let note = "";
    if (act === "revise") { note = prompt("Ne değişsin? (ajan bu nota göre yeniden üretecek)") || ""; if (!note) return; }
    if (act === "reject" && !confirm("Bu içerik reddedilsin mi?")) return;
    try {
      const r = await sendJSON("POST", `/api/approvals/${item.id}`, { action: act, note });
      toast(act === "approve" ? "Onaylandı" : act === "reject" ? "Reddedildi" : "Revize görevi başladı");
      if (r.run) location.hash = `#/calisma?id=${r.run.id}`; else { load(); refreshBadge(); }
    } catch (ex) { toast(ex.message); }
  });
  load();
  return onLive((ev) => { if (ev.type === "approval") load(); });
}

/* ------------------------------------------------------------ raporlar */

function pageReports() {
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Raporlar</h1>
      <p class="page-desc">Rakip raporları, içerik takvimleri, strateji planları ve video özetleri.</p></div></div>
    <div class="reports-layout">
      <div class="card"><input type="search" id="r-filter" placeholder="Rapor ara…" style="margin-bottom:8px"><div class="report-list" id="report-list"></div></div>
      <div class="card report-view" id="report-view"><div class="empty">Okumak için bir rapor seçin</div></div>
    </div>`;
  let reports = [];
  const current = () => params().get("r");
  const drawList = () => {
    const q = $("#r-filter").value.toLowerCase();
    const shown = reports.filter((r) => r.name.toLowerCase().includes(q));
    $("#report-list").innerHTML = shown.length ? shown.map((r) => `
      <button class="report-item ${r.name === current() ? "active" : ""}" data-r="${esc(r.name)}">${KIND_ICON[r.kind]} ${esc(r.name)}
      <small>${fmtSize(r.size)} · ${fmtDate(r.modified)}</small></button>`).join("") : `<div class="empty">Rapor yok</div>`;
  };
  const open = async (name) => {
    const box = $("#report-view");
    const r = reports.find((x) => x.name === name) || { kind: /\.(md|txt|csv|json)$/i.test(name) ? "text" : "other" };
    const bar = `<div class="report-toolbar"><a class="btn btn-sm" href="${fileUrl("reports", name, true)}">↓ İndir</a></div>`;
    if (r.kind === "pdf") { box.innerHTML = bar + `<iframe src="${fileUrl("reports", name)}" style="width:100%;height:75vh;border:0;border-radius:10px"></iframe>`; return; }
    if (r.kind === "image") { box.innerHTML = bar + `<img src="${fileUrl("reports", name)}" style="max-width:100%;border-radius:10px">`; return; }
    if (r.kind !== "text" || /\.html?$/i.test(name)) { box.innerHTML = bar + `<div class="empty">Bu dosya türü panelde önizlenemiyor. İndirip açabilirsiniz.</div>`; return; }
    box.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    try {
      const text = await api(fileUrl("reports", name));
      box.innerHTML = bar + (/\.md$/i.test(name) ? `<article class="markdown">${renderMarkdown(text)}</article>` : `<pre class="markdown">${esc(text)}</pre>`);
      if (innerWidth < 1180) box.scrollIntoView({ behavior: "smooth" });
    } catch (ex) { box.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
  };
  $("#r-filter").addEventListener("input", drawList);
  $("#report-list").addEventListener("click", (e) => {
    const name = e.target.closest("[data-r]")?.dataset.r; if (!name) return;
    history.replaceState(null, "", `#/raporlar?r=${encodeURIComponent(name)}`);
    drawList(); open(name);
  });
  api("/api/files/reports").catch(() => []).then((r) => { reports = r; drawList(); if (current()) open(current()); });
}

/* ------------------------------------------------------------ galeri */

function pageGallery() {
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Görsel ve video galerisi</h1>
      <p class="page-desc">Ekibin ürettiği ve sizin eklediğiniz görsel/videolar.</p></div>
      <label class="btn btn-gold">＋ Medya yükle<input type="file" id="media-input" accept="image/*,video/*" multiple hidden></label></div>
    <div class="upload-progress" id="m-progress"></div>
    <div class="page-head" style="margin:6px 0 14px"><div class="tabs" id="m-tabs">
      <button class="tab active" data-k="all">Tümü</button><button class="tab" data-k="image">Görseller</button><button class="tab" data-k="video">Videolar</button></div></div>
    <div class="gallery" id="gallery"></div>`;
  let items = [], kind = "all";
  const draw = () => {
    const shown = items.filter((f) => (kind === "all" ? ["image", "video"].includes(f.kind) : f.kind === kind));
    $("#gallery").innerHTML = shown.length ? shown.map((f) => `
      <button class="g-item" data-n="${esc(f.name)}" data-k="${f.kind}">
        ${f.kind === "image" ? `<img loading="lazy" src="${fileUrl("media", f.name)}" alt="${esc(f.name)}">` : `<video preload="metadata" muted playsinline src="${fileUrl("media", f.name)}#t=0.5"></video>`}
        <span class="g-badge chip ${f.kind === "video" ? "chip-orange" : "chip-cyan"}">${f.kind === "video" ? "▶ Video" : "Görsel"}</span>
        <span class="g-name">${esc(f.name)}</span></button>`).join("") : `<div class="empty" style="grid-column:1/-1">Henüz medya yok</div>`;
  };
  const load = async () => { items = await api("/api/files/media").catch(() => []); draw(); };
  $("#m-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab"); if (!b) return;
    $$(".tab", $("#m-tabs")).forEach((t) => t.classList.toggle("active", t === b));
    kind = b.dataset.k; draw();
  });
  $("#media-input").addEventListener("change", (e) => { uploadFiles("media", e.target.files, $("#m-progress"), load); e.target.value = ""; });
  $("#gallery").addEventListener("click", (e) => { const b = e.target.closest(".g-item"); if (b) openLightbox(b.dataset.n, b.dataset.k, load); });
  load();
  return onLive((ev) => { if (ev.type === "tool" && ev.tool === "save_media_from_url") setTimeout(load, 3000); });
}

function openLightbox(name, kind, onDelete) {
  const lb = $("#lightbox");
  const src = fileUrl("media", name);
  lb.innerHTML = `
    <div class="lb-bar"><span class="lb-title">${esc(name)}</span>
      <a class="btn btn-sm" href="${fileUrl("media", name, true)}">↓ İndir</a>
      ${onDelete ? `<button class="btn btn-sm btn-danger" id="lb-del">Sil</button>` : ""}
      <button class="btn btn-sm" id="lb-close" aria-label="Kapat">✕</button></div>
    <div class="lb-body">${kind === "video" ? `<video src="${src}" controls autoplay playsinline></video>` : `<img src="${src}" alt="${esc(name)}">`}</div>`;
  lb.hidden = false;
  const close = () => { lb.hidden = true; lb.innerHTML = ""; document.removeEventListener("keydown", onKey); };
  const onKey = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", onKey);
  $("#lb-close").onclick = close;
  lb.onclick = (e) => { if (e.target === lb || e.target.classList.contains("lb-body")) close(); };
  if (onDelete) $("#lb-del").onclick = async () => {
    if (!confirm(`"${name}" silinsin mi?`)) return;
    try { await api(src, { method: "DELETE" }); toast("Silindi"); close(); onDelete(); } catch (ex) { toast(ex.message); }
  };
}

/* ------------------------------------------------------------ dosyalar */

function pageFiles() {
  let area = "uploads";
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Dosyalar</h1>
      <p class="page-desc">Ekibin kullanacağı veriler: Instagram Insights dışa aktarımları, ürün bilgileri, brief'ler, marka görselleri.</p></div></div>
    <div class="card">
      <label class="dropzone" id="drop"><input type="file" id="file-input" multiple hidden>
        <div style="font-size:1.8rem">⇪</div><div><b>Dosya seçin</b> veya buraya sürükleyin</div>
        <div class="muted" style="font-size:.82rem;margin-top:4px">Excel, CSV, PDF, Word, görsel, video…</div></label>
      <div class="upload-progress" id="up-progress"></div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="page-head" style="margin:0 0 10px">
        <div class="tabs" id="area-tabs"><button class="tab active" data-area="uploads">Yüklenenler</button><button class="tab" data-area="reports">Raporlar</button><button class="tab" data-area="media">Görsel / video</button></div>
        <input type="search" id="file-filter" placeholder="Ara…" style="max-width:220px"></div>
      <div class="file-list" id="file-list"></div>
    </div>`;
  const list = $("#file-list");
  let items = [];
  const draw = () => {
    const q = $("#file-filter").value.toLowerCase();
    const shown = items.filter((f) => f.name.toLowerCase().includes(q));
    list.innerHTML = shown.length ? shown.map((f) => `
      <div class="file-row"><div class="file-ico">${KIND_ICON[f.kind]}</div>
        <div><div class="file-name">${esc(f.name)}</div><div class="file-meta">${fmtSize(f.size)} · ${fmtDate(f.modified)}</div></div>
        <div class="row-actions"><a class="btn btn-sm" href="${fileUrl(f.area, f.name, true)}" title="İndir">↓</a>
          <button class="btn btn-sm btn-danger" data-del="${esc(f.name)}" title="Sil">✕</button></div></div>`).join("") : `<div class="empty">Bu alanda dosya yok</div>`;
  };
  const load = async () => { items = await api(`/api/files/${area}`).catch((e) => (toast(e.message), [])); draw(); };
  $("#area-tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab"); if (!b) return;
    $$(".tab", $("#area-tabs")).forEach((t) => t.classList.toggle("active", t === b));
    area = b.dataset.area; load();
  });
  $("#file-filter").addEventListener("input", draw);
  list.addEventListener("click", async (e) => {
    const name = e.target.closest("[data-del]")?.dataset.del; if (!name) return;
    if (!confirm(`"${name}" silinsin mi? Bu işlem geri alınamaz.`)) return;
    try { await api(fileUrl(area, name), { method: "DELETE" }); toast("Dosya silindi"); load(); } catch (ex) { toast(ex.message); }
  });
  const drop = $("#drop"), input = $("#file-input");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => uploadFiles("uploads", e.dataTransfer.files, $("#up-progress"), () => area === "uploads" && load()));
  input.addEventListener("change", () => { uploadFiles("uploads", input.files, $("#up-progress"), () => area === "uploads" && load()); input.value = ""; });
  load();
}

function uploadFiles(area, fileList, progressBox, done) {
  [...fileList].forEach((file) => {
    const row = document.createElement("div");
    row.className = "up-row";
    row.innerHTML = `<span class="file-name">${esc(file.name)}</span><span class="pct muted">0%</span><div class="progress"><span style="width:0;background:var(--gold)"></span></div>`;
    progressBox.prepend(row);
    const fd = new FormData();
    fd.append("files", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/files/${area}`);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      row.querySelector(".pct").textContent = `${p}%`;
      row.querySelector(".progress span").style.width = `${p}%`;
    };
    xhr.onload = () => {
      if (xhr.status === 401) return showLogin();
      if (xhr.status >= 200 && xhr.status < 300) { row.querySelector(".pct").textContent = "✓"; setTimeout(() => row.remove(), 2500); done && done(); }
      else {
        let msg = "Yükleme başarısız";
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch { /* yok */ }
        row.querySelector(".pct").textContent = "✕"; toast(`${file.name}: ${msg}`);
      }
    };
    xhr.onerror = () => toast(`${file.name}: bağlantı hatası`);
    xhr.send(fd);
  });
}

/* ------------------------------------------------------------ ayarlar */

function pageSettings() {
  view.innerHTML = `<div class="page-head"><div><h1 class="page-title">Ayarlar</h1>
    <p class="page-desc">Ajanların her çalışmada okuduğu marka profili ve rakip listesi.</p></div></div>
    <div class="grid-2" style="margin-top:0">
      <form class="card task-form" id="settings-form">
        <div class="card-title">🏷 Marka profili</div>
        <textarea id="s-brand" style="min-height:260px" required minlength="20"></textarea>
        <p class="muted" style="font-size:.8rem">Ürünler, fiyatlar, aktif kampanyalar, ton, görsel kimlik (renkler, yazı tipi), kullanılmaması gereken ifadeler… Ne kadar ayrıntılı olursa çıktılar o kadar markaya uygun olur.</p>
        <div class="card-title" style="margin-top:8px">🔍 Rakipler</div>
        <div id="s-comps"></div>
        <div style="display:flex;gap:8px;justify-content:space-between"><button type="button" class="btn btn-sm" id="add-comp">＋ Rakip ekle</button><button type="submit" class="btn btn-gold">Kaydet</button></div>
      </form>
      <div class="card"><div class="card-title">🔌 Bağlantılar ve bütçe</div><dl class="kv" id="s-info"></dl></div>
    </div>`;
  const compRow = (c = {}) => `<div class="comp-row"><input type="text" placeholder="Marka adı" value="${esc(c.name || "")}" data-f="name" required>
    <input type="text" placeholder="instagram kullanıcı adı" value="${esc(c.handle || "")}" data-f="handle" pattern="[A-Za-z0-9._]*">
    <input type="text" placeholder="Takipçi" value="${esc(c.followers || "")}" data-f="followers"><button type="button" class="btn btn-sm btn-danger" data-rm>✕</button></div>`;
  api("/api/settings").then((s) => {
    $("#s-brand").value = s.brand;
    $("#s-comps").innerHTML = s.competitors.map(compRow).join("");
    const pct = s.budget.limit ? Math.min(100, (s.budget.spent / s.budget.limit) * 100) : 0;
    $("#s-info").innerHTML = `
      <dt>Claude API</dt><dd>${s.integrations.claude ? chip("Bağlı", "green") : chip("ANTHROPIC_API_KEY yok", "red")}</dd>
      <dt>Model</dt><dd>${esc(s.integrations.model)}</dd>
      <dt>Virale (ChatPlace)</dt><dd>${s.integrations.virale ? chip("Bağlı", "green") : chip("VIRALE_MCP_URL yok", "orange")}</dd>
      <dt>Bu ay harcanan</dt><dd>${fmtUsd(s.budget.spent)} / ${fmtUsd(s.budget.limit)}<div class="budget-bar"><span style="width:${pct}%"></span></div></dd>
      <dt>Üretim sınırı</dt><dd>Çalıştırma başına ${s.limits.generations_per_run} görsel/video</dd>
      <dt>Tur sınırı</dt><dd>Ajan başına ${s.limits.turns} tur</dd>`;
  }).catch((e) => toast(e.message));
  $("#add-comp").addEventListener("click", () => $("#s-comps").insertAdjacentHTML("beforeend", compRow()));
  $("#s-comps").addEventListener("click", (e) => e.target.closest("[data-rm]")?.parentElement.remove());
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const competitors = $$(".comp-row", $("#s-comps")).map((r) => Object.fromEntries($$("input", r).map((i) => [i.dataset.f, i.value.trim().replace(/^@/, "")])));
    try { await sendJSON("PUT", "/api/settings", { brand: $("#s-brand").value, competitors }); toast("Kaydedildi"); } catch (ex) { toast(ex.message); }
  });
}

/* ------------------------------------------------------------ arka plan parçacıkları */

(function particles() {
  const canvas = $("#particles"), ctx = canvas.getContext("2d");
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let w, h, dots, rgb, alpha;
  const readColors = () => {
    const cs = getComputedStyle(document.documentElement);
    rgb = cs.getPropertyValue("--particle").trim() || "166,124,31";
    alpha = parseFloat(cs.getPropertyValue("--particle-alpha")) || 0.3;
  };
  const resize = () => {
    w = canvas.width = innerWidth * devicePixelRatio; h = canvas.height = innerHeight * devicePixelRatio;
    readColors();
    const light = document.documentElement.dataset.theme !== "dark";
    const n = Math.min(light ? 45 : 70, Math.round((innerWidth * innerHeight) / (light ? 32000 : 22000)));
    dots = Array.from({ length: n }, () => ({ x: Math.random() * w, y: Math.random() * h, vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25, r: Math.random() * 1.6 + 0.4 }));
  };
  const tick = () => {
    if (document.hidden) return requestAnimationFrame(tick);
    ctx.clearRect(0, 0, w, h);
    const link = 120 * devicePixelRatio;
    for (const d of dots) {
      d.x = (d.x + d.vx + w) % w; d.y = (d.y + d.vy + h) % h;
      ctx.beginPath(); ctx.arc(d.x, d.y, d.r * devicePixelRatio, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${rgb},${alpha})`; ctx.fill();
    }
    for (let i = 0; i < dots.length; i++) for (let j = i + 1; j < dots.length; j++) {
      const a = dots[i], b = dots[j], dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (dist < link) { ctx.strokeStyle = `rgba(${rgb},${alpha * 0.2 * (1 - dist / link)})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
    }
    requestAnimationFrame(tick);
  };
  addEventListener("resize", resize); addEventListener("themechange", resize); resize(); tick();
})();

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
startApp().catch(showLogin);
