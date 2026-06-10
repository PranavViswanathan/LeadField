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

/* ---------- boot ---------- */
async function boot() {
  observeReveals();
  try {
    const [stats, leads] = await Promise.all([
      getJSON("/api/stats"),
      getJSON("/api/leads"),
    ]);
    renderStats(stats);
    state.leads = leads;
    buildFilters(leads);
    applyFilters();
    setStatus(leads.length ? `${leads.length} leads live` : "no data yet", true);
  } catch (err) {
    console.error(err);
    setStatus("offline", false);
    renderCards();
  }
}
boot();
