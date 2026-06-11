/* ============================================================
   LEADFIELD dashboard · data wiring, scroll choreography, panel
   ============================================================ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = { leads: [], filtered: [], filter: "all", query: "" };

/* ---------- scroll progress + sticky nav ---------- */
const progress = $("#scrollProgress");
const nav = $("#nav");
function onScroll() {
  const h = document.documentElement;
  const scrolled = h.scrollTop / (h.scrollHeight - h.clientHeight || 1);
  progress.style.width = `${Math.min(scrolled * 100, 100)}%`;
  nav.classList.toggle("scrolled", h.scrollTop > 40);
}
document.addEventListener("scroll", onScroll, { passive: true });
onScroll();

/* ---------- reveal-on-scroll (with per-element stagger) ---------- */
function primeReveals(root = document) {
  $$(".reveal", root).forEach((el) => {
    if (el.dataset.d) el.style.setProperty("--d", el.dataset.d);
  });
}
const revealObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        revealObserver.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
);
function observeReveals(root = document) {
  primeReveals(root);
  $$(".reveal", root).forEach((el) => revealObserver.observe(el));
}

/* ---------- count-up numbers ---------- */
function animateCount(el, target) {
  const dur = 1400;
  const start = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  function frame(now) {
    const p = Math.min((now - start) / dur, 1);
    el.textContent = Math.round(ease(p) * target).toLocaleString();
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* ---------- API ---------- */
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

/* ---------- stats / hero / gap / signal ---------- */
function renderStats(stats) {
  $("#heroLocation").textContent = `· ${stats.location} ·`;

  const statBand = $("#scan");
  const numbers = $$(".stat-num", statBand);
  const statObserver = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        numbers.forEach((el) => animateCount(el, stats[el.dataset.key] || 0));
        obs.disconnect();
      });
    },
    { threshold: 0.4 }
  );
  statObserver.observe(statBand);

  // gap bar
  const total = stats.total_businesses || 1;
  const hasPct = Math.max((stats.with_website / total) * 100, 6);
  const nonePct = Math.max((stats.without_website / total) * 100, 6);
  $("#gapHasVal").textContent = stats.with_website;
  $("#gapNoneVal").textContent = stats.without_website;
  const gapObserver = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        $("#gapHas").style.width = `${hasPct}%`;
        $("#gapNone").style.width = `${nonePct}%`;
        obs.disconnect();
      });
    },
    { threshold: 0.4 }
  );
  $("#gapHas").style.width = "50%";
  $("#gapNone").style.width = "50%";
  gapObserver.observe($(".gap-bar"));

  // category signal bars
  renderSignal(stats.categories || []);
}

function renderSignal(categories) {
  const wrap = $("#signalBars");
  if (!categories.length) {
    wrap.innerHTML = `<p style="color:var(--paper-dim);font-family:var(--font-mono)">No categories yet.</p>`;
    return;
  }
  const max = Math.max(...categories.map((c) => c.count));
  wrap.innerHTML = categories
    .map(
      (c) => `
      <div class="sig-row reveal">
        <span class="sig-name">${escapeHTML(c.name)}</span>
        <div class="sig-track"><div class="sig-fill" data-w="${(c.count / max) * 100}"></div></div>
        <span class="sig-count">${c.count}</span>
      </div>`
    )
    .join("");
  observeReveals(wrap);
  const fillObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const fill = $(".sig-fill", entry.target);
        if (fill) fill.style.width = `${fill.dataset.w}%`;
        fillObserver.unobserve(entry.target);
      });
    },
    { threshold: 0.5 }
  );
  $$(".sig-row", wrap).forEach((row) => fillObserver.observe(row));
}

/* ---------- filters + cards ---------- */
function buildFilters(leads) {
  const cats = [...new Set(leads.map((l) => l.category))].sort();
  const defs = [
    { key: "all", label: "all leads" },
    { key: "none", label: "no website" },
    { key: "has", label: "has website" },
    ...cats.map((c) => ({ key: `cat:${c}`, label: c })),
  ];
  $("#filters").innerHTML = defs
    .map(
      (d) =>
        `<button class="chip ${d.key === "all" ? "active" : ""}" data-filter="${d.key}">${escapeHTML(
          d.label
        )}</button>`
    )
    .join("");
  $$("#filters .chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      $$("#filters .chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.filter = chip.dataset.filter;
      applyFilters();
    })
  );
}

function applyFilters() {
  const q = state.query.toLowerCase();
  state.filtered = state.leads.filter((l) => {
    const matchFilter =
      state.filter === "all" ||
      (state.filter === "none" && !l.has_website) ||
      (state.filter === "has" && l.has_website) ||
      (state.filter.startsWith("cat:") && l.category === state.filter.slice(4));
    const matchQuery =
      !q ||
      l.business_name.toLowerCase().includes(q) ||
      l.category.toLowerCase().includes(q) ||
      (l.subject || "").toLowerCase().includes(q);
    return matchFilter && matchQuery;
  });
  renderCards();
}

function renderCards() {
  const wrap = $("#cards");
  const empty = $("#empty");
  if (!state.leads.length) {
    wrap.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  if (!state.filtered.length) {
    wrap.innerHTML = `<p style="color:var(--paper-dim);font-family:var(--font-mono);grid-column:1/-1">No leads match that filter.</p>`;
    return;
  }
  wrap.innerHTML = state.filtered
    .map((l, i) => {
      const kind = l.has_website ? "has" : "none";
      const tag = l.has_website ? "improve site" : "build site";
      return `
      <article class="card ${kind} reveal" data-d="${Math.min(i, 8)}" data-idx="${state.leads.indexOf(l)}">
        <div class="card-top">
          <span class="card-cat">${escapeHTML(l.category)}</span>
          <span class="card-tag ${kind}">${tag}</span>
        </div>
        <h3 class="card-name">${escapeHTML(l.business_name)}</h3>
        <p class="card-subject">${escapeHTML(l.subject || "")}</p>
        <div class="card-foot">
          <span>${l.has_website ? "● live site" : "○ no site found"}</span>
          <span class="open">read draft →</span>
        </div>
      </article>`;
    })
    .join("");
  observeReveals(wrap);
  $$(".card", wrap).forEach((card) =>
    card.addEventListener("click", () => openPanel(state.leads[+card.dataset.idx]))
  );
}

/* ---------- detail panel ---------- */
const overlay = $("#overlay");
const panel = $("#panel");

function openPanel(lead) {
  const kind = lead.has_website ? "has" : "none";
  const site = lead.website_url
    ? `<span class="p-pill"><a href="${escapeAttr(lead.website_url)}" target="_blank" rel="noopener">${escapeHTML(
        shortUrl(lead.website_url)
      )} ↗</a></span>`
    : `<span class="p-pill">no website</span>`;
  const obs =
    lead.observations && lead.observations.length
      ? `<div class="p-section-label">site observations</div>
         <ul class="p-obs">${lead.observations.map((o) => `<li>${escapeHTML(o)}</li>`).join("")}</ul>`
      : "";

  $("#panelBody").innerHTML = `
    <div class="p-cat">${escapeHTML(lead.category)} · ${lead.has_website ? "redesign pitch" : "new build pitch"}</div>
    <h2 class="p-name">${escapeHTML(lead.business_name)}</h2>
    <div class="p-meta">
      ${site}
      <span class="p-pill">model: ${escapeHTML(lead.model || "n/a")}</span>
      <span class="p-pill ${kind}">${lead.has_website ? "improve_site" : "build_site"}</span>
    </div>
    ${obs}
    <div class="p-section-label">drafted email</div>
    <p class="p-subject">${escapeHTML(lead.subject || "")}</p>
    <pre class="p-body" id="emailBody">${escapeHTML(lead.body || "")}</pre>
    <button class="p-copy" id="copyBtn">copy email to clipboard</button>
  `;
  overlay.hidden = false;
  panel.hidden = false;
  document.body.style.overflow = "hidden";

  $("#copyBtn").addEventListener("click", async (e) => {
    const text = `Subject: ${lead.subject}\n\n${lead.body}`;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* clipboard may be blocked; ignore */
    }
    e.target.textContent = "copied ✓";
    e.target.classList.add("copied");
    setTimeout(() => {
      e.target.textContent = "copy email to clipboard";
      e.target.classList.remove("copied");
    }, 1800);
  });
}

function closePanel() {
  overlay.hidden = true;
  panel.hidden = true;
  document.body.style.overflow = "";
}
overlay.addEventListener("click", closePanel);
$("#panelClose").addEventListener("click", closePanel);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePanel();
});

/* ---------- search ---------- */
$("#search").addEventListener("input", (e) => {
  state.query = e.target.value;
  applyFilters();
});

/* ---------- helpers ---------- */
function escapeHTML(str = "") {
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}
function escapeAttr(str = "") {
  return escapeHTML(str);
}
function shortUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
function setStatus(text, ok) {
  $("#navStatusText").textContent = text;
  $(".pulse").style.background = ok ? "var(--mint)" : "var(--ember)";
}

/* ---------- data refresh (boot + after a live run) ---------- */
async function refreshData({ syncLocationInput = false } = {}) {
  const [stats, leads] = await Promise.all([
    getJSON("/api/stats"),
    getJSON("/api/leads"),
  ]);
  renderStats(stats);
  state.leads = leads;
  buildFilters(leads);
  applyFilters();
  setStatus(leads.length ? `${leads.length} leads live` : "no data yet", true);
  if (syncLocationInput && stats.location) {
    $("#runLocation").value = stats.location;
  }
}

/* ---------- live run console ---------- */
const runForm = $("#runForm");
const runBtn = $("#runBtn");
const runStatus = $("#runStatus");
const runStatusText = $("#runStatusText");
const runBarFill = $("#runBarFill");
let pollTimer = null;

function setRunStatus(text, kind) {
  runStatus.hidden = false;
  runStatusText.textContent = text;
  runStatusText.className = `console-status-text${kind ? " " + kind : ""}`;
}

function setBar(processed, total) {
  if (total > 0) {
    runBarFill.classList.remove("indeterminate");
    runBarFill.style.width = `${Math.round((processed / total) * 100)}%`;
  } else {
    runBarFill.classList.add("indeterminate");
  }
}

async function pollRun() {
  let job;
  try {
    job = await getJSON("/api/run/status");
  } catch {
    return;
  }
  setBar(job.processed, job.total);
  if (job.status === "running") {
    const suffix = job.total ? ` (${job.processed}/${job.total})` : "";
    setRunStatus(`${job.message}${suffix}`, null);
    return;
  }
  clearInterval(pollTimer);
  pollTimer = null;
  runBtn.disabled = false;
  runBtn.textContent = "Run live scan";
  if (job.status === "error") {
    setRunStatus(`Scan failed: ${job.error}`, "error");
    return;
  }
  const n = job.result ? job.result.emails : 0;
  setBar(1, 1);
  setRunStatus(`Done. ${n} fresh leads loaded.`, "ok");
  await refreshData();
}

runForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (pollTimer) return;
  const location = $("#runLocation").value.trim();
  if (!location) return;
  const limit = parseInt($("#runLimit").value, 10);

  runBtn.disabled = true;
  runBtn.textContent = "Scanning…";
  setBar(0, 0);
  setRunStatus("Dispatching scan…", null);
  try {
    await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locations: [location], limit, reset: true }),
    });
    pollTimer = setInterval(pollRun, 1200);
    pollRun();
  } catch (err) {
    runBtn.disabled = false;
    runBtn.textContent = "Run live scan";
    setRunStatus(`Could not start scan: ${err}`, "error");
  }
});

/* ---------- scheduler view ---------- */
const schedulerView = $("#schedulerView");
const schedCities = [];
let schedMode = "now";

let schedLiveTimer = null;
let schedLiveStatus = null;

function openScheduler() {
  schedulerView.hidden = false;
  document.body.style.overflow = "hidden";
  if (!schedCities.length) {
    const loc = $("#runLocation").value.trim();
    if (loc) schedCities.push(loc);
  }
  renderCityList();
  loadSchedules();
  updateSchedulerLive();
  if (schedLiveTimer) clearInterval(schedLiveTimer);
  schedLiveTimer = setInterval(updateSchedulerLive, 2000);
}
function closeScheduler() {
  schedulerView.hidden = true;
  document.body.style.overflow = "";
  if (schedLiveTimer) {
    clearInterval(schedLiveTimer);
    schedLiveTimer = null;
  }
}

function schedSubmitLabel() {
  return schedMode === "now" ? "Run now" : "Add schedule";
}

// Drives the live "scan in progress" banner and keeps the schedule list fresh
// while the Scheduler tab is open. Catches scheduled runs too, not just Run now.
async function updateSchedulerLive() {
  let job;
  try {
    job = await getJSON("/api/run/status");
  } catch {
    return;
  }
  const banner = $("#activeScan");
  const bar = $("#activeScanBar");
  if (job.status === "running") {
    banner.hidden = false;
    $("#activeScanText").textContent = job.message || "scan in progress…";
    if (job.total > 0) {
      bar.classList.remove("indeterminate");
      bar.style.width = `${Math.round((job.processed / job.total) * 100)}%`;
    } else {
      bar.classList.add("indeterminate");
    }
    $("#schedSubmit").disabled = true;
    $("#schedSubmit").textContent = "scan in progress…";
    if (schedLiveStatus !== "running") loadSchedules();
  } else {
    banner.hidden = true;
    $("#schedSubmit").disabled = false;
    $("#schedSubmit").textContent = schedSubmitLabel();
    if (schedLiveStatus === "running") {
      // a run just finished: refresh the list and the dashboard underneath
      loadSchedules();
      refreshData({ syncLocationInput: true });
    }
  }
  schedLiveStatus = job.status;
}
$("#navScheduler").addEventListener("click", openScheduler);
$("#schedulerClose").addEventListener("click", closeScheduler);

function renderCityList() {
  $("#cityList").innerHTML = schedCities
    .map(
      (c, i) =>
        `<li class="city-chip">${escapeHTML(c)}<button data-i="${i}" aria-label="remove">✕</button></li>`
    )
    .join("");
  $$("#cityList .city-chip button").forEach((b) =>
    b.addEventListener("click", () => {
      schedCities.splice(+b.dataset.i, 1);
      renderCityList();
    })
  );
}

function addCity() {
  const value = $("#cityInput").value.trim();
  if (value && !schedCities.includes(value)) {
    schedCities.push(value);
    $("#cityInput").value = "";
    renderCityList();
  }
}
$("#addCity").addEventListener("click", addCity);
$("#cityInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addCity();
  }
});

$$("#modeTabs .mode-tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$("#modeTabs .mode-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    schedMode = tab.dataset.mode;
    $("#onceAt").hidden = schedMode !== "once";
    $("#dailyAt").hidden = schedMode !== "daily";
    $("#schedSubmit").textContent = schedMode === "now" ? "Run now" : "Add schedule";
  })
);

$("#schedSubmit").addEventListener("click", async () => {
  if (!schedCities.length) {
    setSchedStatus("Add at least one city first.", "error");
    return;
  }
  const limit = parseInt($("#maxPerCity").value, 10) || 20;
  const reset = $("#resetToggle").checked;
  const locations = schedCities.slice();

  if (schedMode === "now") {
    await startSchedRun(locations, limit, reset);
  } else if (schedMode === "once") {
    const runAt = $("#onceAt").value;
    if (!runAt) return setSchedStatus("Pick a date and time.", "error");
    await createSchedule({
      locations,
      max_per_city: limit,
      mode: "once",
      run_at: runAt,
      reset,
    });
  } else {
    const timeOfDay = $("#dailyAt").value;
    if (!timeOfDay) return setSchedStatus("Pick a time of day.", "error");
    await createSchedule({
      locations,
      max_per_city: limit,
      mode: "daily",
      time_of_day: timeOfDay,
      reset,
    });
  }
});

async function createSchedule(body) {
  await fetch("/api/schedules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  setSchedStatus(`Scheduled scan for ${body.locations.length} location(s).`, "ok");
  loadSchedules();
}

function setSchedStatus(text, kind) {
  $("#schedRunStatus").hidden = false;
  $("#schedRunText").textContent = text;
  $("#schedRunText").className = `console-status-text${kind ? " " + kind : ""}`;
}

async function startSchedRun(locations, limit, reset) {
  setSchedStatus("Dispatching scan…", null);
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locations, limit, reset }),
  });
  const data = await res.json().catch(() => ({}));
  if (data.status === "busy") {
    setSchedStatus("A scan is already in progress.", "error");
    return;
  }
  setSchedStatus("Scan started, watch progress above.", "ok");
  updateSchedulerLive();
}

async function loadSchedules() {
  let schedules;
  try {
    schedules = await getJSON("/api/schedules");
  } catch {
    return;
  }
  const wrap = $("#schedList");
  if (!schedules.length) {
    wrap.innerHTML = `<p class="empty-note">No scheduled scans yet.</p>`;
    return;
  }
  wrap.innerHTML = schedules
    .map((s) => {
      const when =
        s.mode === "once" ? `Once · ${fmtWhen(s.run_at)}` : `Daily · ${s.time_of_day}`;
      const last = s.last_run ? ` · last run ${fmtWhen(s.last_run)}` : "";
      return `<div class="sched-card">
        <div>
          <div class="sched-when">${when}</div>
          <div class="sched-cities">${s.locations.map(escapeHTML).join(", ")}</div>
          <div class="sched-meta">max ${s.max_per_city || "-"}/city${last}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
          <span class="sched-status ${s.status}">${s.status}</span>
          <button class="sched-del" data-id="${s.id}">delete</button>
        </div>
      </div>`;
    })
    .join("");
  $$("#schedList .sched-del").forEach((b) =>
    b.addEventListener("click", async () => {
      await fetch(`/api/schedules/${b.dataset.id}`, { method: "DELETE" });
      loadSchedules();
    })
  );
}

function fmtWhen(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!schedulerView.hidden) closeScheduler();
  if (!analyticsView.hidden) closeAnalytics();
});

/* ---------- analytics dashboard view ---------- */
const analyticsView = $("#analyticsView");
const CHART = { mint: "#7fe3c4", ember: "#ff6a3d", amber: "#f0a93b", surface: "#12181f" };

async function openAnalytics() {
  analyticsView.hidden = false;
  document.body.style.overflow = "hidden";
  let data;
  try {
    data = await getJSON("/api/analytics");
  } catch {
    return;
  }
  $("#analyticsEmpty").hidden = data.total_businesses > 0;

  renderKpis(data);
  renderDonut($("#donutGap"), [
    { label: "has a site", value: data.with_website, color: CHART.mint },
    { label: "no site", value: data.without_website, color: CHART.ember },
  ]);
  renderStackedBars(
    $("#catBars"),
    data.by_category.map((c) => ({
      label: c.name,
      withSite: c.with_website,
      withoutSite: c.without_website,
      total: c.count,
    }))
  );
  renderBars(
    $("#cityBars"),
    data.by_city.map((c) => ({ label: c.city, value: c.count })),
    CHART.amber
  );
  renderBars(
    $("#issueBars"),
    data.top_issues.map((i) => ({ label: i.label, value: i.count })),
    CHART.ember
  );
}
function closeAnalytics() {
  analyticsView.hidden = true;
  document.body.style.overflow = "";
}
$("#navDashboard").addEventListener("click", openAnalytics);
$("#analyticsClose").addEventListener("click", closeAnalytics);

function renderKpis(data) {
  const tiles = [
    { n: data.total_businesses, l: "businesses" },
    { n: data.with_website, l: "with a site" },
    { n: data.without_website, l: "no site · build" },
    { n: data.by_city.length, l: "cities" },
    { n: data.total_emails, l: "emails drafted" },
  ];
  $("#kpiRow").innerHTML = tiles
    .map((t) => `<div class="kpi"><div class="kpi-num">${t.n}</div><div class="kpi-label">${t.l}</div></div>`)
    .join("");
}

function renderDonut(el, segments) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  const r = 52;
  const circ = 2 * Math.PI * r;
  let offset = 0;
  const rings = segments
    .map((seg) => {
      const len = total ? (seg.value / total) * circ : 0;
      const ring = `<circle cx="60" cy="60" r="${r}" fill="none" stroke="${seg.color}"
        stroke-width="15" stroke-dasharray="${len} ${circ - len}"
        stroke-dashoffset="${-offset}" transform="rotate(-90 60 60)"></circle>`;
      offset += len;
      return ring;
    })
    .join("");
  el.innerHTML = `
    <svg viewBox="0 0 120 120" class="donut">
      <circle cx="60" cy="60" r="52" fill="none" stroke="${CHART.surface}" stroke-width="15"></circle>
      ${rings}
      <text x="60" y="56" class="donut-num">${total}</text>
      <text x="60" y="72" class="donut-sub">leads</text>
    </svg>
    <div class="donut-legend">
      ${segments
        .map((s) => `<span><i style="background:${s.color}"></i>${escapeHTML(s.label)} · ${s.value}</span>`)
        .join("")}
    </div>`;
}

function renderStackedBars(el, items) {
  if (!items.length) {
    el.innerHTML = `<p class="empty-note">No data.</p>`;
    return;
  }
  const max = Math.max(...items.map((i) => i.total), 1);
  el.innerHTML = items
    .map(
      (i) => `
      <div class="hbar-row">
        <span class="hbar-label">${escapeHTML(i.label)}</span>
        <div class="hbar-track">
          <div class="hbar-seg mint" style="width:${(i.withSite / max) * 100}%"></div>
          <div class="hbar-seg ember" style="width:${(i.withoutSite / max) * 100}%"></div>
        </div>
        <span class="hbar-count">${i.total}</span>
      </div>`
    )
    .join("");
}

function renderBars(el, items, color) {
  if (!items.length) {
    el.innerHTML = `<p class="empty-note">No data.</p>`;
    return;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  el.innerHTML = items
    .map(
      (i) => `
      <div class="hbar-row">
        <span class="hbar-label">${escapeHTML(i.label)}</span>
        <div class="hbar-track">
          <div class="hbar-seg" style="width:${(i.value / max) * 100}%;background:${color}"></div>
        </div>
        <span class="hbar-count">${i.value}</span>
      </div>`
    )
    .join("");
}

/* ---------- global run watcher ---------- */
// Detects runs the page did not start (scheduled scans, or another tab) and
// refreshes the dashboard automatically when they finish.
let lastWatchedStatus = null;
async function watchRuns() {
  let job;
  try {
    job = await getJSON("/api/run/status");
  } catch {
    return;
  }
  if (job.status === "running") {
    setStatus(job.message || "scanning…", true);
  }
  if (lastWatchedStatus === "running" && job.status !== "running") {
    await refreshData({ syncLocationInput: true });
    if (!schedulerView.hidden) loadSchedules();
  }
  lastWatchedStatus = job.status;
}

/* ---------- boot ---------- */
async function boot() {
  observeReveals();
  try {
    await refreshData({ syncLocationInput: true });
  } catch (err) {
    console.error(err);
    setStatus("offline", false);
    renderCards();
  }
  watchRuns();
  setInterval(watchRuns, 6000);
}
boot();
