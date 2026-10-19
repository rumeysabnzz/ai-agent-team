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
  add_lesson: "Hafızaya ders yazıyor", list_lessons: "Hafızayı inceliyor", remove_lesson: "Eski dersi siliyor",
  add_knowledge: "Bilgi bankasına not ekliyor", team_activity: "Takım verisini inceliyor",
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

$("#forgot-toggle").addEventListener("click", () => ($("#forgot-box").hidden = !$("#forgot-box").hidden));
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

/* ------------------------------------------------------------ açılır takvim */

let popMonth = "";
async function drawPopCal() {
  const box = $("#cal-pop");
  const data = await api(`/api/calendar?month=${popMonth}`).catch(() => null);
  if (!data) return;
  popMonth = data.month;
  const [y, m] = popMonth.split("-").map(Number);
  const first = (new Date(y, m - 1, 1).getDay() + 6) % 7, total = new Date(y, m, 0).getDate();
  let cells = "<span></span>".repeat(first);
  for (let d = 1; d <= total; d++) {
    const key = `${popMonth}-${String(d).padStart(2, "0")}`, c = data.days[key] || {};
    const n = Object.values(c).reduce((a, b) => a + b, 0);
    cells += `<button class="pc-day ${key === data.today ? "today" : ""} ${n ? "has" : ""}" data-day="${key}" title="${n ? `${n} kayıt` : "kayıt yok"}">${d}${n ? "<i></i>" : ""}</button>`;
  }
  box.innerHTML = `<div class="pc-head"><button class="btn btn-sm" data-pc="-1" aria-label="Önceki ay">‹</button><b>${MONTHS[m - 1]} ${y}</b><button class="btn btn-sm" data-pc="1" aria-label="Sonraki ay">›</button></div>
    <div class="pc-grid pc-wd">${["Pt", "Sa", "Ça", "Pe", "Cu", "Ct", "Pa"].map((x) => `<span>${x}</span>`).join("")}</div>
    <div class="pc-grid">${cells}</div><div class="pc-foot muted">Noktalı günlerde kayıt var. Güne dokunun.</div>`;
}
$("#cal-toggle").addEventListener("click", (e) => {
  e.stopPropagation();
  const box = $("#cal-pop");
  box.hidden = !box.hidden;
  if (!box.hidden) { popMonth = ""; drawPopCal(); }
});
$("#cal-pop").addEventListener("click", (e) => {
  e.stopPropagation();
  const nav = e.target.closest("[data-pc]");
  if (nav) {
    const [y, m] = popMonth.split("-").map(Number); const d = new Date(y, m - 1 + Number(nav.dataset.pc), 1);
    popMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; drawPopCal(); return;
  }
  const day = e.target.closest("[data-day]")?.dataset.day; if (!day) return;
  $("#cal-pop").hidden = true;
  const target = `#/takvim?ay=${day.slice(0, 7)}&gun=${day}`;
  if (location.hash === target) route(); else location.hash = target;
});
document.addEventListener("click", () => { const b = $("#cal-pop"); if (b) b.hidden = true; });
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
    case "lesson": return `Ders → ${agentName(ev.target)}: ${ev.text}`;
    case "knowledge": return `Bilgi notu: ${ev.text}`;
    default: return "";
  }
}
const evClass = (t) => ({ thinking: "k-thinking", tool: "k-tool", tool_error: "k-error", agent_done: "k-done", lesson: "k-done", knowledge: "k-tool" }[t] || "");

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
  if (ev.agent && ["thinking", "text", "tool", "tool_error", "agent_start", "agent_done", "agent_note", "lesson", "knowledge"].includes(ev.type)) {
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
  galeri: pageGallery, dosyalar: pageFiles, ayarlar: pageSettings, takvim: pageCalendar, gelisim: pageDevelopment, icerik: pageContentHistory };
let cleanup = null;
const params = () => new URLSearchParams(location.hash.split("?")[1] || "");

function route() {
  const key = location.hash.replace(/^#\/?/, "").split("?")[0];
  const page = routes[key] || pageHome;
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (key || "home") || (key === "calisma" && a.dataset.route === "akislar") || (key === "icerik" && a.dataset.route === "onay")));
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
    state.management = ov.management;
    state.agents = Object.fromEntries(ov.teams.flatMap((t) => [...t.agents, t.manager].map((a) => [a.id, { ...a, team: t.id, color: t.color }])));
    state.agents[ov.management.controller.id] = { ...ov.management.controller, team: "yonetim", color: ov.management.color };
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
    <a class="hub-center" href="#/gelisim" data-agent="${state.management.controller.id}" title="Ana Kontrolcü · Gelişim">
      <div class="hub-ring"></div><div class="hub-ring"></div><div class="hub-ring"></div>
      <div class="hub-icon">🧭</div><div class="hub-name">Ana Kontrolcü</div><div class="hub-role">4 yönetici · 12 ajan</div>
    </a>
    <div class="teams-grid">${state.teams.map((t, i) => `
      <div class="team-node pos-${i}" data-team="${t.id}">
        <div class="node-card" style="--card-color:${t.color}"><div class="node-inner">
          <div class="node-header"><span class="node-icon">${t.icon}</span><span class="node-title">${esc(t.name)}</span><span class="team-chip"></span></div>
          <div class="node-summary">${esc(t.summary)}</div>
          <div class="agent-row manager-row" data-agent="${t.manager.id}" style="color:${t.color}">
            <span class="agent-dot" style="background:${t.color}"></span><span class="agent-name">👔 ${esc(t.manager.name)}</span>
            <span class="agent-act"></span></div>
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
  const hc = $(".hub-center[data-agent]");
  if (hc) hc.classList.toggle("active", !!state.live[hc.dataset.agent]);
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
      const team = state.teams.find((t) => t.id === w.team) || (w.team === "yonetim" ? state.management : {});
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
      if (!groups.length || groups[groups.length - 1].agent !== ev.agent || ev.type === "agent_start") groups.push({ agent: ev.agent, model: ev.model, rows: [] });
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
        <div class="tl-agent" style="color:${state.agents[g.agent]?.color || "var(--gold)"}">● ${esc(agentName(g.agent))}${g.model ? ` <span class="chip chip-gray">${esc(g.model)}</span>` : ""}</div>
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

function revisionInfo(i) {
  const run = i.revision_run ? `<a href="#/calisma?id=${i.revision_run}">çalıştırmayı aç</a>` : "";
  if (i.revised_by) return `<div class="ap-rev ok">↻ Yeni sürüm hazırlandı ve onaya gönderildi.</div>`;
  if (i.status === "revision") {
    if (i.revision_status === "failed") return `<div class="ap-rev bad">✕ Revize çalıştırması hata verdi: ${esc(i.revision_error || "")} · ${run}</div>`;
    if (i.revision_status === "done") return `<div class="ap-rev bad">Revize çalıştırması bitti ama yeni sürüm onaya gelmedi · ${run}</div>`;
    if (i.revision_run) return `<div class="ap-rev"><span class="spinner"></span> Revize hazırlanıyor · ${run}</div>`;
    return `<div class="ap-rev bad">Bu içerik için revize çalıştırması kaydı yok; tekrar başlatabilirsiniz.</div>`;
  }
  if (i.revision_of) return `<div class="ap-rev ok">↻ Revize edilmiş sürüm</div>`;
  return "";
}

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
          <span class="muted" style="font-size:.8rem">${esc(i.agent_name)} · ${fmtDate(i.created)}</span><h3><a class="ap-title" href="#/icerik?id=${i.id}">${esc(i.title)}</a></h3></div>
        ${i.media.length ? `<div class="ap-media">${i.media.map((m) => /\.(mp4|mov|webm|m4v)$/i.test(m)
          ? `<video src="${fileUrl("media", m)}#t=0.5" preload="metadata" muted playsinline data-lb="${esc(m)}" data-k="video"></video>`
          : `<img src="${fileUrl("media", m)}" loading="lazy" alt="${esc(m)}" data-lb="${esc(m)}" data-k="image">`).join("")}</div>` : ""}
        <div class="ap-body markdown">${renderMarkdown(i.body)}</div>
        ${i.note ? `<div class="ap-note">Not: ${esc(i.note)}</div>` : ""}
        ${revisionInfo(i)}
        <div class="ap-actions">
          <a class="btn btn-sm" href="#/icerik?id=${i.id}">🕘 Geçmiş${i.revision_of || i.revised_by || i.revision_run ? " ↻" : ""}</a>
          <button class="btn btn-sm" data-copy="${i.id}">⧉ Metni kopyala</button>
          ${i.status === "pending" ? `<button class="btn btn-sm btn-green" data-act="approve">✓ Onayla</button>
          <button class="btn btn-sm" data-act="revise">↻ Revize iste</button><button class="btn btn-sm btn-danger" data-act="reject">✕ Reddet</button>` : ""}
          ${i.status === "revision" && (i.revision_status === "failed" || !i.revision_run) ? `<button class="btn btn-sm" data-act="revise" data-retry="1">↻ Revizeyi tekrar başlat</button>` : ""}
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
    if (act === "revise") { note = prompt("Ne değişsin? (ajan bu nota göre yeniden üretecek)", item.note || "") || ""; if (!note) return; }
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
      <div class="card report-list-card"><input type="search" id="r-filter" placeholder="Rapor ara…" style="margin-bottom:8px"><div class="report-list" id="report-list"></div></div>
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
    $(".reports-layout").classList.add("has-report");
    const bar = `<div class="report-toolbar"><button type="button" class="btn btn-sm only-mobile" data-back>← Raporlar</button><a class="btn btn-sm" href="${fileUrl("reports", name, true)}">↓ İndir</a></div>`;
    if (r.kind === "pdf") { box.innerHTML = bar + `<iframe src="${fileUrl("reports", name)}" style="width:100%;height:75vh;border:0;border-radius:10px"></iframe>`; return; }
    if (r.kind === "image") { box.innerHTML = bar + `<img src="${fileUrl("reports", name)}" style="max-width:100%;border-radius:10px">`; return; }
    if (r.kind !== "text" || /\.html?$/i.test(name)) { box.innerHTML = bar + `<div class="empty">Bu dosya türü panelde önizlenemiyor. İndirip açabilirsiniz.</div>`; return; }
    box.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    try {
      const text = await api(fileUrl("reports", name));
      box.innerHTML = bar + (/\.md$/i.test(name) ? `<article class="markdown">${renderMarkdown(text)}</article>` : `<pre class="markdown">${esc(text)}</pre>`);
      if (innerWidth < 1180) window.scrollTo({ top: 0 });
    } catch (ex) { box.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
  };
  $("#r-filter").addEventListener("input", drawList);
  $("#report-view").addEventListener("click", (e) => {
    if (!e.target.closest("[data-back]")) return;
    history.replaceState(null, "", "#/raporlar");
    $(".reports-layout").classList.remove("has-report");
    $("#report-view").innerHTML = `<div class="empty">Okumak için bir rapor seçin</div>`;
    drawList();
  });
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

const MD_EXT = /\.(pdf|docx|xlsx|xlsm|pptx|md|txt|csv|json|tsv|xml|ya?ml)$/i;

async function openMarkdown(area, name) {
  const lb = $("#lightbox");
  lb.innerHTML = `<div class="lb-bar"><span class="lb-title">${esc(name)} · ajanların okuduğu metin</span>
      <button class="btn btn-sm" id="md-dl" disabled>↓ .md indir</button><button class="btn btn-sm" id="lb-close" aria-label="Kapat">✕</button></div>
    <div class="lb-body md-body"><div class="md-sheet"><span class="spinner"></span> Dönüştürülüyor…</div></div>`;
  lb.hidden = false;
  const close = () => { lb.hidden = true; lb.innerHTML = ""; document.removeEventListener("keydown", onKey); };
  const onKey = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", onKey);
  $("#lb-close").onclick = close;
  try {
    const r = await api(`/api/files/${area}/${encodeURIComponent(name)}/markdown`);
    $(".md-sheet", lb).innerHTML = `<article class="markdown">${renderMarkdown(r.markdown)}</article>`;
    const btn = $("#md-dl"); btn.disabled = false;
    btn.onclick = () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([r.markdown], { type: "text/markdown" }));
      a.download = name.replace(/\.[^.]+$/, "") + ".md"; a.click(); URL.revokeObjectURL(a.href);
    };
  } catch (ex) { $(".md-sheet", lb).innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
}

function pageFiles() {
  let area = "uploads";
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Dosyalar</h1>
      <p class="page-desc">Ekibin kullanacağı veriler: Instagram Insights dışa aktarımları, ürün bilgileri, brief'ler, marka görselleri.</p></div></div>
    <div class="card">
      <label class="dropzone" id="drop"><input type="file" id="file-input" multiple hidden>
        <div style="font-size:1.8rem">⇪</div><div><b>Dosya seçin</b> veya buraya sürükleyin</div>
        <div class="muted" style="font-size:.82rem;margin-top:4px">Excel, CSV, PDF, Word, görsel, video…</div></label>
      <div class="up-meta">
        <select id="up-team"><option value="genel">Tüm ekip kullansın</option>${state.teams.map((t) => `<option value="${t.id}">${t.icon} ${esc(t.name)}</option>`).join("")}</select>
        <input type="text" id="up-note" placeholder="Ajanlara not: bu dosya nedir, nasıl kullanılsın? (ör. Ekim Insights verisi, ürün görselleri)">
      </div>
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
        <div><div class="file-name">${esc(f.name)}</div><div class="file-meta">${fmtSize(f.size)} · ${fmtDate(f.modified)}${area === "uploads" ? ` · ${f.team && f.team !== "genel" ? esc(state.teams.find((t) => t.id === f.team)?.name || f.team) : "tüm ekip"}` : ""}</div>
          ${f.note ? `<div class="file-note">📝 ${esc(f.note)}</div>` : ""}</div>
        <div class="row-actions">${MD_EXT.test(f.name) ? `<button class="btn btn-sm" data-md="${esc(f.name)}" title="Ajanların okuduğu metin (Markdown)">MD</button>` : ""}${area === "uploads" ? `<button class="btn btn-sm" data-note="${esc(f.name)}" title="Ajanlara not">✎</button>` : ""}<a class="btn btn-sm" href="${fileUrl(f.area, f.name, true)}" title="İndir">↓</a>
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
    const mdFor = e.target.closest("[data-md]")?.dataset.md;
    if (mdFor) return openMarkdown(area, mdFor);
    const noteFor = e.target.closest("[data-note]")?.dataset.note;
    if (noteFor) {
      const f = items.find((x) => x.name === noteFor) || {};
      const note = prompt("Ajanlar bu dosyayı nasıl kullansın?", f.note || "");
      if (note === null) return;
      try { await sendJSON("PUT", `/api/files/uploads/${encodeURIComponent(noteFor)}/note`, { note, team: f.team || "genel" }); toast("Not kaydedildi"); load(); } catch (ex) { toast(ex.message); }
      return;
    }
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
    if ($("#up-note")) { fd.append("note", $("#up-note").value); fd.append("team", $("#up-team").value); }
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
      <div style="display:flex;flex-direction:column;gap:16px;min-width:0">
        <div class="card"><div class="card-title">🧠 Ajan modelleri</div>
          <p class="muted" style="font-size:.88rem;margin-bottom:10px">Her ajan kendi işine uygun modelle çalışır. Değişiklik bir sonraki çalıştırmadan itibaren geçerlidir.</p>
          <div class="model-presets"><button type="button" class="btn btn-sm" data-preset="default">Önerilen</button><button type="button" class="btn btn-sm" data-preset="claude-haiku-5-5">Tümü Haiku</button><button type="button" class="btn btn-sm" data-preset="claude-opus-5-5">Tümü Opus</button></div>
          <div id="s-models"></div><div class="muted" id="s-model-prices" style="font-size:.82rem;margin-top:10px"></div></div>
        <div class="card"><div class="card-title">🔌 Bağlantılar ve bütçe</div><dl class="kv" id="s-info"></dl></div>
        <form class="card task-form" id="pw-form"><div class="card-title">🔑 Parola değiştir</div>
          <input type="password" id="pw-cur" placeholder="Mevcut parola" autocomplete="current-password" required>
          <input type="password" id="pw-new" placeholder="Yeni parola (en az 10 karakter)" autocomplete="new-password" minlength="10" required>
          <input type="password" id="pw-new2" placeholder="Yeni parola (tekrar)" autocomplete="new-password" minlength="10" required>
          <p class="muted" style="font-size:.84rem">Değiştirince diğer cihazlardaki oturumlar kapanır. Unutursanız Railway'deki PANEL_PASSWORD ile sıfırlanır.</p>
          <button type="submit" class="btn btn-gold">Parolayı değiştir</button></form>
      </div>
    </div>`;
  const compRow = (c = {}) => `<div class="comp-row"><input type="text" placeholder="Marka adı" value="${esc(c.name || "")}" data-f="name" required>
    <input type="text" placeholder="instagram kullanıcı adı" value="${esc(c.handle || "")}" data-f="handle" pattern="[A-Za-z0-9._]*">
    <input type="text" placeholder="Takipçi" value="${esc(c.followers || "")}" data-f="followers"><button type="button" class="btn btn-sm btn-danger" data-rm>✕</button></div>`;
  let modelState = null;
  const drawModels = () => {
    const { models, model_choices: choices } = modelState;
    const groups = [...state.teams.map((t) => ({ ...t, list: [t.manager, ...t.agents] })),
      { ...state.management, list: [state.management.controller] }];
    $("#s-models").innerHTML = groups.map((t) => `<div class="model-team" style="--team-c:${t.color}"><div class="model-team-name">${t.icon} ${esc(t.name)}</div>
      ${t.list.map((a) => `<label class="model-row"><span>${esc(a.name)}</span><select data-agent="${a.id}">
        ${choices.map((c) => `<option value="${c.id}" ${models[a.id] === c.id ? "selected" : ""}>${esc(c.label)}${modelState.model_defaults[a.id] === c.id ? " ★" : ""}</option>`).join("")}
      </select></label>`).join("")}</div>`).join("");
    $("#s-model-prices").innerHTML = choices.map((c) => `<b>${esc(c.label)}</b>: ${esc(c.note)} · $${c.price.input} / $${c.price.output} (1M girdi / çıktı token)`).join("<br>") + "<br>★ ajanın görevi için önerilen model";
  };
  const saveModels = async (changes) => {
    try {
      const r = await sendJSON("PUT", "/api/settings/models", { models: changes });
      Object.assign(modelState.models, changes); drawModels(); toast(`Kaydedildi: ${r.summary}`);
    } catch (ex) { toast(ex.message); drawModels(); }
  };
  $("#s-models").addEventListener("change", (e) => { const sel = e.target.closest("select[data-agent]"); if (sel) saveModels({ [sel.dataset.agent]: sel.value }); });
  $$("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    const p = b.dataset.preset;
    saveModels(Object.fromEntries(Object.keys(modelState.models).map((id) => [id, p === "default" ? modelState.model_defaults[id] : p])));
  }));
  api("/api/settings").then((s) => {
    modelState = { models: s.models, model_defaults: s.model_defaults, model_choices: s.model_choices }; drawModels();
    $("#s-brand").value = s.brand;
    $("#s-comps").innerHTML = s.competitors.map(compRow).join("");
    const pct = s.budget.limit ? Math.min(100, (s.budget.spent / s.budget.limit) * 100) : 0;
    $("#s-info").innerHTML = `
      <dt>Claude API</dt><dd>${s.integrations.claude ? chip("Bağlı", "green") : chip("ANTHROPIC_API_KEY yok", "red")}</dd>
      <dt>Modeller</dt><dd>${esc(s.integrations.model)}</dd>
      <dt>Virale (ChatPlace)</dt><dd>${s.integrations.virale ? chip("Bağlı", "green") : chip("VIRALE_MCP_URL yok", "orange")}</dd>
      <dt>Bu ay harcanan</dt><dd>${fmtUsd(s.budget.spent)} / ${fmtUsd(s.budget.limit)}<div class="budget-bar"><span style="width:${pct}%"></span></div></dd>
      <dt>Üretim sınırı</dt><dd>Çalıştırma başına ${s.limits.generations_per_run} görsel/video</dd>
      <dt>Tur sınırı</dt><dd>Ajan başına ${s.limits.turns} tur</dd>
      <dt>Panel sürümü</dt><dd>${esc(s.version)}</dd>`;
  }).catch((e) => toast(e.message));
  $("#pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("#pw-new").value !== $("#pw-new2").value) return toast("Yeni parolalar aynı değil");
    try { await sendJSON("POST", "/api/password", { current: $("#pw-cur").value, new: $("#pw-new").value }); e.target.reset(); toast("Parola değiştirildi"); }
    catch (ex) { toast(ex.message); }
  });
  $("#add-comp").addEventListener("click", () => $("#s-comps").insertAdjacentHTML("beforeend", compRow()));
  $("#s-comps").addEventListener("click", (e) => e.target.closest("[data-rm]")?.parentElement.remove());
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const competitors = $$(".comp-row", $("#s-comps")).map((r) => Object.fromEntries($$("input", r).map((i) => [i.dataset.f, i.value.trim().replace(/^@/, "")])));
    try { await sendJSON("PUT", "/api/settings", { brand: $("#s-brand").value, competitors }); toast("Kaydedildi"); } catch (ex) { toast(ex.message); }
  });
}

/* ------------------------------------------------------------ içerik geçmişi: ilk çıktı → revizeler */

const AP_STATE = { pending: ["Onay bekliyor", "orange"], approved: ["Onaylandı", "green"], revision: ["Revize istendi", "purple"], rejected: ["Reddedildi", "red"] };

function pageContentHistory() {
  const id = params().get("id");
  view.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  const media = (list) => list.length ? `<div class="ap-media">${list.map((m) => /\.(mp4|mov|webm|m4v)$/i.test(m)
    ? `<video src="${fileUrl("media", m)}#t=0.5" preload="metadata" muted playsinline data-lb="${esc(m)}" data-k="video"></video>`
    : `<img src="${fileUrl("media", m)}" loading="lazy" alt="${esc(m)}" data-lb="${esc(m)}" data-k="image">`).join("")}</div>` : `<div class="muted hv-nomedia">Bu sürümde görsel/video yok</div>`;
  const runLink = (r, label) => r ? `<a class="hv-run" href="#/calisma?id=${r.id}">${label}: <b>${esc(r.title)}</b> · ${fmtDate(r.created)} ${runChip(r.status)}</a>` : "";
  const load = async () => {
    const d = await api(`/api/approvals/${id}/history`).catch((e) => ({ error: e.message }));
    if (d.error) { view.innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
    const last = d.versions[d.versions.length - 1];
    view.innerHTML = `
      <div class="page-head"><div><h1 class="page-title">${esc(d.title)}</h1>
        <p class="page-desc">${d.versions.length} sürüm · ilk çıktı ${fmtDate(d.versions[0].created)} · son durum: ${AP_STATE[last.status]?.[0] || last.status}</p></div>
        <a class="btn btn-sm" href="#/onay">← Onay</a></div>
      <div class="hv-timeline">${d.versions.map((v, n) => {
        const [sl, sc] = AP_STATE[v.status] || [v.status, "gray"];
        const rr = v.revision_run_info;
        return `<section class="card hv-ver ${v.id === id ? "current" : ""}">
          <div class="hv-head">${chip(n === 0 ? "İlk çıktı" : `Revize ${n}`, n === 0 ? "gold" : "purple")} ${chip(v.kind, "gray")}
            <span class="muted">${esc(v.agent_name)} · ${fmtDate(v.created)}</span><span class="hv-state">${chip(sl, sc)}</span></div>
          ${runLink(v.made_by_run, n === 0 ? "Başlangıç" : "Revize çalıştırması")}
          ${media(v.media)}
          <details class="hv-body" ${v.id === id ? "open" : ""}><summary>İçerik metni</summary><div class="markdown">${renderMarkdown(v.body)}</div></details>
          ${v.decided ? `<div class="hv-decision"><b>Kurucu kararı (${fmtDate(v.decided)}):</b> ${esc(sl)}${v.note ? ` — “${esc(v.note)}”` : ""}</div>` : ""}
          ${rr ? `<div class="hv-next ${rr.status === "failed" ? "bad" : ""}">↓ ${runLink(rr, "Revize")} ${rr.error ? `<div class="hv-err">${esc(rr.error)}</div>` : ""}
            ${!v.revised_by && rr.status === "done" ? `<div class="hv-err">Çalıştırma bitti ama yeni sürüm onaya gelmedi.</div>` : ""}</div>` : ""}
          ${v.status === "revision" && !rr ? `<div class="hv-err">Bu revize isteği için çalıştırma kaydı yok (eski sürümde başlatılmış olabilir). Onay → Revizede'den "Revizeyi tekrar başlat" ile yeniden başlatın.</div>` : ""}
        </section>`;
      }).join('<div class="hv-arrow">↓</div>')}</div>`;
  };
  view.addEventListener("click", (e) => { const lb = e.target.closest("[data-lb]"); if (lb) openLightbox(lb.dataset.lb, lb.dataset.k); });
  load();
  return onLive((ev) => { if (["run_end", "approval"].includes(ev.type)) load(); });
}

/* ------------------------------------------------------------ takvim: günlük geçmiş */

const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const CAL_KEYS = [["runs", "⟳", "Çalıştırma", "var(--purple)"], ["reports", "▤", "Rapor", "var(--brand)"], ["media", "▣", "Görsel/video", "var(--cyan)"],
  ["approvals", "✓", "Onay", "var(--green)"], ["alerts", "⚠", "Uyarı", "var(--red)"]];

function pageCalendar() {
  const p = params();
  let month = p.get("ay") || "", day = p.get("gun") || "";
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Takvim</h1>
      <p class="page-desc">Bir güne dokunun; o gün yapılan çalıştırmalar, raporlar, üretilen görsel ve videolar, onaylar ve uyarılar listelenir.</p></div></div>
    <div class="cal-layout">
      <div class="card cal-card">
        <div class="cal-head"><button class="btn btn-sm" id="cal-prev" aria-label="Önceki ay">‹</button><b id="cal-title"></b><button class="btn btn-sm" id="cal-next" aria-label="Sonraki ay">›</button></div>
        <div class="cal-grid cal-wd">${["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"].map((d) => `<span>${d}</span>`).join("")}</div>
        <div class="cal-grid" id="cal-days"></div>
        <div class="cal-legend">${CAL_KEYS.map(([, ic, l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join("")}</div>
      </div>
      <div class="card" id="day-detail"><div class="empty">Bir gün seçin</div></div>
    </div>`;

  const setUrl = () => history.replaceState(null, "", `#/takvim?ay=${month}${day ? `&gun=${day}` : ""}`);
  const shift = (n) => { const [y, m] = month.split("-").map(Number); const d = new Date(y, m - 1 + n, 1);
    month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; loadMonth(); };

  const loadMonth = async () => {
    const data = await api(`/api/calendar?month=${month}`).catch(() => null);
    if (!data) return;
    month = data.month;
    if (!day) day = data.today.startsWith(month) ? data.today : "";
    setUrl();
    const [y, m] = month.split("-").map(Number);
    $("#cal-title").textContent = `${MONTHS[m - 1]} ${y}`;
    const first = (new Date(y, m - 1, 1).getDay() + 6) % 7, total = new Date(y, m, 0).getDate();
    let html = "<span></span>".repeat(first);
    for (let d = 1; d <= total; d++) {
      const key = `${month}-${String(d).padStart(2, "0")}`, c = data.days[key] || {};
      const dots = CAL_KEYS.filter(([k]) => c[k]).map(([k, , l, col]) => `<i style="background:${col}" title="${c[k]} ${l}"></i>`).join("");
      const sum = CAL_KEYS.reduce((n, [k]) => n + (c[k] || 0), 0);
      html += `<button class="cal-day ${key === data.today ? "today" : ""} ${key === day ? "sel" : ""} ${sum ? "has" : ""}" data-day="${key}">
        <span class="cal-num">${d}</span><span class="cal-dots">${dots}</span>${sum ? `<span class="cal-sum">${sum}</span>` : ""}</button>`;
    }
    $("#cal-days").innerHTML = html;
    if (day) loadDay();
  };

  const section = (title, list, render) => list.length ? `<div class="day-sec"><div class="card-title">${title} <span class="muted">(${list.length})</span></div>${list.map(render).join("")}</div>` : "";
  const loadDay = async () => {
    const box = $("#day-detail");
    box.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    const d = await api(`/api/day/${day}`).catch((e) => ({ error: e.message }));
    if (d.error) { box.innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
    const [y, m, dd] = day.split("-").map(Number);
    const total = d.runs.length + d.reports.length + d.media.length + d.approvals.length + d.alerts.length;
    box.innerHTML = `<div class="day-title">${dd} ${MONTHS[m - 1]} ${y}</div>` + (total || d.activity.length ? [
      section("⟳ Çalıştırmalar", d.runs, runRow),
      section("▤ Raporlar", d.reports, (f) => `<a class="day-item" href="#/raporlar?r=${encodeURIComponent(f.name)}">${KIND_ICON[f.kind] || "📄"} ${esc(f.name)}<small>${fmtTime(f.modified)}</small></a>`),
      d.media.length ? `<div class="day-sec"><div class="card-title">▣ Görsel ve videolar <span class="muted">(${d.media.length})</span></div><div class="day-media">${d.media.map((f) => f.kind === "video"
        ? `<video src="${fileUrl("media", f.name)}#t=0.5" preload="metadata" muted playsinline data-lb="${esc(f.name)}" data-k="video"></video>`
        : f.kind === "image" ? `<img src="${fileUrl("media", f.name)}" loading="lazy" alt="${esc(f.name)}" data-lb="${esc(f.name)}" data-k="image">` : "").join("")}</div></div>` : "",
      section("✓ Onaya gönderilenler", d.approvals, (a) => `<a class="day-item" href="#/onay">${chip(a.kind, "gray")} ${esc(a.title)}<small>${esc(a.agent_name)} · ${{ pending: "bekliyor", approved: "onaylandı", revision: "revizede", rejected: "reddedildi" }[a.status] || a.status}</small></a>`),
      section("⚠ Uyarılar", d.alerts, alertRow),
      section("🗂 Aktivite", d.activity, (a) => `<div class="log-entry"><span class="log-time">${fmtTime(a.time)}</span><span class="log-dot" style="background:${ACT_COLOR[a.kind] || ACT_COLOR.info}"></span><span class="log-msg">${esc(a.message)}</span></div>`),
    ].join("") : `<div class="empty">Bu gün kayıt yok</div>`);
    if (innerWidth < 1180) box.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  $("#cal-prev").addEventListener("click", () => { day = ""; shift(-1); });
  $("#cal-next").addEventListener("click", () => { day = ""; shift(1); });
  $("#cal-days").addEventListener("click", (e) => {
    const b = e.target.closest("[data-day]"); if (!b) return;
    day = b.dataset.day; setUrl();
    $$(".cal-day", $("#cal-days")).forEach((x) => x.classList.toggle("sel", x === b));
    loadDay();
  });
  $("#day-detail").addEventListener("click", (e) => { const lb = e.target.closest("[data-lb]"); if (lb) openLightbox(lb.dataset.lb, lb.dataset.k); });
  loadMonth();
}

/* ------------------------------------------------------------ gelişim: yöneticiler, hafıza, bilgi bankası */

function pageDevelopment() {
  view.innerHTML = `
    <div class="page-head"><div><h1 class="page-title">Gelişim</h1>
      <p class="page-desc">Her takımın yöneticisi ajanlarını değerlendirir, onların hafızasına ders yazar ve dış kaynaklardan bilgi ekler; Ana Kontrolcü yöneticilerin raporlarını birleştirir.</p></div>
      <button class="btn btn-gold" id="dev-run">▶ Yönetim değerlendirmesini başlat</button></div>
    <div class="card dev-org" id="dev-org"></div>
    <div class="grid-2">
      <div class="card"><div class="card-title">🧠 Ajan hafızaları</div><p class="muted dev-hint">Dersler; Kurucu geri bildirimlerinden (onay / revize / ret notları), yöneticilerin koçluğundan ve ajanların kendi değerlendirmesinden gelir. Ajan her çalışmada bunları okur.</p><div id="dev-mem"></div></div>
      <div class="card"><div class="card-title">📚 Bilgi bankası (dış kaynaklar)</div><p class="muted dev-hint">Yöneticilerin web araştırmasından veya sizin eklediğiniz notlar. Takımdaki tüm ajanlar okur.</p>
        <form id="kb-form" class="kb-form">
          <select id="kb-team"><option value="genel">Tüm ekip</option>${state.teams.map((t) => `<option value="${t.id}">${t.icon} ${esc(t.name)}</option>`).join("")}</select>
          <input type="text" id="kb-title" placeholder="Başlık" required minlength="2">
          <textarea id="kb-text" placeholder="Bilgi notu (ör. Instagram'da reel'lerin ilk 2 saniyesi…)" required minlength="3" style="min-height:80px"></textarea>
          <input type="text" id="kb-url" placeholder="Kaynak bağlantısı (isteğe bağlı)">
          <button type="submit" class="btn btn-sm btn-gold">＋ Not ekle</button></form>
        <div id="dev-kb"></div></div>
    </div>`;
  let data = null;
  const teamOf = (id) => [...state.teams, state.management].find((t) => t.id === id) || { name: "Tüm ekip", icon: "🌐", color: "var(--brand)" };
  const draw = () => {
    const last = data.last_management_run;
    $("#dev-org").innerHTML = `
      <div class="org-top"><div class="org-node ctrl ${state.live[state.management.controller.id] ? "active" : ""}">🧭 Ana Kontrolcü</div></div>
      <div class="org-row">${state.teams.map((t) => `<div class="org-node ${state.live[t.manager.id] ? "active" : ""}" style="--team-c:${t.color}">👔 ${esc(t.manager.name)}<small>${t.agents.length} ajan</small></div>`).join("")}</div>
      <div class="org-meta">${last ? `Son değerlendirme: ${fmtDate(last.created)} ${runChip(last.status)} <a href="#/calisma?id=${last.id}">aç</a>` : "Henüz yönetim değerlendirmesi yapılmadı."} · Otomatik: her Cuma 17:00</div>
      ${data.management_reports.length ? `<div class="outputs">${data.management_reports.map((f) => `<a class="btn btn-sm" href="#/raporlar?r=${encodeURIComponent(f.name)}">▤ ${esc(f.name)}</a>`).join("")}</div>` : ""}`;
    const groups = [...state.teams.map((t) => ({ t, list: [t.manager, ...t.agents] })), { t: state.management, list: [state.management.controller] }];
    $("#dev-mem").innerHTML = groups.map(({ t, list }) => `<div class="model-team" style="--team-c:${t.color}"><div class="model-team-name">${t.icon} ${esc(t.name)}</div>
      ${list.map((a) => { const ls = data.memory[a.id] || []; return `<details class="mem-agent"><summary>${esc(a.name)} <span class="muted">(${ls.length} ders)</span></summary>
        ${ls.length ? ls.slice().reverse().map((l) => `<div class="mem-item"><div>${esc(l.text)}<small>${esc(l.source)} · ${fmtDate(l.time)}</small></div><button class="btn btn-sm btn-danger" data-del-lesson="${a.id}:${l.id}" aria-label="Sil">✕</button></div>`).join("") : `<div class="muted mem-empty">Henüz ders yok</div>`}
        <form class="mem-add" data-agent="${a.id}"><input type="text" placeholder="Bu ajana bir çalışma kuralı yazın" minlength="3" required><button class="btn btn-sm">Ekle</button></form></details>`; }).join("")}</div>`).join("");
    const kb = Object.entries(data.knowledge).flatMap(([team, notes]) => notes.map((n) => ({ ...n, team }))).sort((a, b) => b.time.localeCompare(a.time));
    $("#dev-kb").innerHTML = kb.length ? kb.map((n) => { const t = teamOf(n.team); return `<div class="kb-item" style="--team-c:${t.color}"><div><b>${esc(n.title)}</b><p>${esc(n.text)}</p>
      <small>${t.icon} ${esc(t.name)} · ${esc(n.added_by)} · ${fmtDate(n.time)}${n.source_url ? ` · <a href="${esc(n.source_url)}" target="_blank" rel="noopener">kaynak</a>` : ""}</small></div>
      <button class="btn btn-sm btn-danger" data-del-kb="${n.team}:${n.id}" aria-label="Sil">✕</button></div>`; }).join("") : `<div class="empty">Henüz bilgi notu yok</div>`;
  };
  const load = async () => { data = await api("/api/development").catch(() => null); if (data) draw(); };
  view.addEventListener("submit", async (e) => {
    const f = e.target.closest(".mem-add"); if (!f) return;
    e.preventDefault();
    try { await sendJSON("POST", `/api/development/lessons/${f.dataset.agent}`, { text: $("input", f).value }); toast("Ders eklendi"); load(); } catch (ex) { toast(ex.message); }
  });
  view.addEventListener("click", async (e) => {
    const l = e.target.closest("[data-del-lesson]"), k = e.target.closest("[data-del-kb]");
    if (!l && !k) return;
    if (!confirm("Silinsin mi?")) return;
    const [a, id] = (l ? l.dataset.delLesson : k.dataset.delKb).split(":");
    try { await api(l ? `/api/development/lessons/${a}/${id}` : `/api/development/knowledge/${a}/${id}`, { method: "DELETE" }); load(); } catch (ex) { toast(ex.message); }
  });
  $("#kb-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await sendJSON("POST", "/api/development/knowledge", { team: $("#kb-team").value, title: $("#kb-title").value, text: $("#kb-text").value, source_url: $("#kb-url").value });
      e.target.reset(); toast("Not eklendi"); load();
    } catch (ex) { toast(ex.message); }
  });
  $("#dev-run").addEventListener("click", async () => {
    try { const r = await sendJSON("POST", "/api/workflows/haftalik-yonetim/run", { note: "" }); location.hash = `#/calisma?id=${r.id}`; } catch (ex) { toast(ex.message); }
  });
  const off = onLive(() => data && draw());
  load();
  return off;
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
