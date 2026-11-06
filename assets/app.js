const ARCHIVE = window.ARCHIVE || { posts: [], collections: [] };
const VERDICT_KEY = "ig_archive_verdicts_v1";
const OLD_KEEP_KEYS = [
  "ig_archive_marks_v3",
  "ig_archive_marks_all_v2",
  "ig_archive_marks_collections_v2",
];
const UNFILED = "Unfiled";

function loadVerdicts() {
  const out = {};
  try {
    const raw = JSON.parse(localStorage.getItem(VERDICT_KEY) || "{}");
    if (raw && typeof raw === "object") Object.assign(out, raw);
  } catch { /* ignore */ }
  if (Object.keys(out).length) return out;
  for (const key of OLD_KEEP_KEYS) {
    try {
      for (const url of JSON.parse(localStorage.getItem(key) || "[]")) out[url] = "keep";
    } catch { /* ignore */ }
  }
  if (Object.keys(out).length) localStorage.setItem(VERDICT_KEY, JSON.stringify(out));
  return out;
}

const verdicts = loadVerdicts();
function saveVerdicts() {
  localStorage.setItem(VERDICT_KEY, JSON.stringify(verdicts));
}
function getVerdict(url) {
  return verdicts[url] || "";
}
function setVerdict(url, value) {
  if (!value) delete verdicts[url];
  else verdicts[url] = value;
  saveVerdicts();
}

function escapeHTML(s) {
  return (s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
  }[c]));
}

function collectionCatalog() {
  const byName = new Map();
  for (const c of ARCHIVE.collections || []) {
    if (!c.name) continue;
    if (!byName.has(c.name)) byName.set(c.name, { ...c });
  }
  const list = [...byName.values()].map(c => ({
    ...c,
    count: ARCHIVE.posts.filter(p => (p.collections || []).includes(c.name)).length,
  })).filter(c => c.count > 0);
  const unfiled = ARCHIVE.posts.filter(p => !(p.collections || []).length).length;
  if (unfiled) {
    list.push({
      name: UNFILED,
      count: unfiled,
      covers: (ARCHIVE.covers && ARCHIVE.covers.other) || [],
      unfiled: true,
    });
  }
  return list;
}

function parseHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  const qIndex = raw.indexOf("?");
  const path = qIndex === -1 ? raw : raw.slice(0, qIndex);
  const qs = new URLSearchParams(qIndex === -1 ? "" : raw.slice(qIndex + 1));
  const parts = path.split("/").filter(Boolean);
  const route = { view: "home", collection: null, kept: false };
  if (parts[0] === "all") route.view = "all";
  else if (parts[0] === "recipes") { route.view = "collection"; route.collection = "Recipes"; }
  else if (parts[0] === "restaurants") { route.view = "collection"; route.collection = "Restaurants"; }
  else if (parts[0] === "kept") { route.view = "all"; route.status = "keep"; }
  else if (parts[0] === "c" && parts[1]) {
    route.view = "collection";
    route.collection = decodeURIComponent(parts[1]);
  }
  route.q = qs.get("q") || "";
  route.type = qs.get("type") || "all";
  if (!route.status) route.status = qs.get("status") || "queue";
  return route;
}

function setHash(route) {
  let path = "/";
  if (route.view === "all") path = "/all";
  else if (route.view === "collection" && route.collection) {
    path = "/c/" + encodeURIComponent(route.collection);
  }
  const qs = new URLSearchParams();
  if (route.q) qs.set("q", route.q);
  if (route.type && route.type !== "all") qs.set("type", route.type);
  if (route.status && route.status !== "queue") qs.set("status", route.status);
  const query = qs.toString();
  const next = "#" + path + (query ? "?" + query : "");
  if (location.hash !== next) location.hash = next;
  else render();
}

function renderNav(route) {
  const items = [
    { href: "#/all", label: "All", on: route.view === "all" && !route.collection },
    ...collectionCatalog().map(c => ({
      href: `#/c/${encodeURIComponent(c.name)}`,
      label: c.name,
      on: route.collection === c.name,
    })),
  ];
  document.getElementById("nav").innerHTML = items.map(item =>
    `<a href="${item.href}" class="${item.on ? "on" : ""}">${escapeHTML(item.label)}</a>`
  ).join("");
}

function haystack(post) {
  return [
    post.username, post.owner_name, post.caption, post.caption_clean, post.title,
    (post.hashtags || []).join(" "),
    (post.collections || []).join(" "),
    post.category,
  ].join(" ").toLowerCase();
}

function matchesCollection(post, name) {
  if (name === UNFILED) return !(post.collections || []).length;
  return (post.collections || []).includes(name);
}

function matchesStatus(post, status) {
  const v = getVerdict(post.url);
  if (status === "all") return true;
  if (status === "keep") return v === "keep";
  if (status === "destroy") return v === "destroy";
  return v !== "keep" && v !== "destroy";
}

function filterPosts(route) {
  const q = (route.q || "").trim().toLowerCase();
  const status = route.status || "queue";
  return ARCHIVE.posts.filter(post => {
    if (route.collection && !matchesCollection(post, route.collection)) return false;
    if (route.type === "reel" && post.type !== "reel") return false;
    if (route.type === "post" && post.type === "reel") return false;
    if (!matchesStatus(post, status)) return false;
    if (q && !haystack(post).includes(q)) return false;
    return true;
  });
}

function formatDate(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleDateString("en-GB", {
    day: "2-digit", month: "short", year: "numeric"
  }).toUpperCase();
}

function todayStamp() {
  return new Date().toLocaleDateString("en-GB", {
    day: "2-digit", month: "short", year: "numeric"
  }).toUpperCase();
}

function queueCount(posts) {
  return posts.filter(p => !getVerdict(p.url)).length;
}

function renderMasthead(route) {
  const el = document.getElementById("masthead");
  if (route.view !== "home" || route.q) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const left = queueCount(ARCHIVE.posts);
  const kept = ARCHIVE.posts.filter(p => getVerdict(p.url) === "keep").length;
  const generated = ARCHIVE.generated_at
    ? new Date(ARCHIVE.generated_at).toLocaleDateString("en-GB", {
        day: "2-digit", month: "short", year: "numeric"
      }).toUpperCase()
    : todayStamp();
  el.hidden = false;
  el.innerHTML = `
    <div class="top-rule">
      <span>Personal archive</span>
      <span>${escapeHTML(generated)}</span>
    </div>
    <h1 class="title">Clear the <span class="amp">pile.</span></h1>
    <p class="subtitle">Work a collection, keep what you want, destroy the rest. Kept items collect in the box below so you can paste them somewhere else.</p>
    <div class="stats-row">
      <div class="stat"><div class="num">${left}</div><div class="label">Still to review</div></div>
      <div class="stat"><div class="num">${kept}</div><div class="label">Kept</div></div>
      <div class="stat"><div class="num">${ARCHIVE.posts.length}</div><div class="label">Saved items</div></div>
      <div class="stat"><div class="num">${collectionCatalog().length}</div><div class="label">Categories</div></div>
    </div>
  `;
}

function renderFilters(route, count) {
  const wrap = document.getElementById("filters");
  if (route.view === "home" && !route.q) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  const statuses = [
    { id: "queue", label: "Queue" },
    { id: "keep", label: "Kept" },
    { id: "destroy", label: "Destroyed" },
    { id: "all", label: "All" },
  ];
  const types = [
    { id: "all", label: "All types" },
    { id: "reel", label: "Reels" },
    { id: "post", label: "Posts" },
  ];
  document.getElementById("status-filters").innerHTML = statuses.map(s =>
    `<button type="button" class="chip ${route.status === s.id ? "on" : ""}" data-status="${s.id}">${s.label}</button>`
  ).join("");
  document.getElementById("type-filters").innerHTML = types.map(t =>
    `<button type="button" class="chip ${route.type === t.id ? "on" : ""}" data-type="${t.id}">${t.label}</button>`
  ).join("");
  document.getElementById("result-meta").textContent = `${count} shown`;
  wrap.querySelectorAll("[data-status]").forEach(btn => {
    btn.addEventListener("click", () => {
      const next = parseHash();
      next.status = btn.dataset.status;
      setHash(next);
    });
  });
  wrap.querySelectorAll("[data-type]").forEach(btn => {
    btn.addEventListener("click", () => {
      const next = parseHash();
      next.type = btn.dataset.type;
      setHash(next);
    });
  });
}

function thumbPath(shortcode) {
  return `media/${shortcode}.jpg`;
}

let igScriptLoaded = false;
function loadIgScript() {
  if (igScriptLoaded) return;
  igScriptLoaded = true;
  const s = document.createElement("script");
  s.src = "https://www.instagram.com/embed.js";
  s.async = true;
  document.head.appendChild(s);
}
function processEmbeds() {
  if (window.instgrm && window.instgrm.Embeds) window.instgrm.Embeds.process();
}

function makeEmbed(url, shortcode) {
  return `
    <div class="embed-frame" data-url="${escapeHTML(url)}" data-shortcode="${escapeHTML(shortcode)}">
      <img class="local-img" src="${escapeHTML(thumbPath(shortcode))}" alt=""
           onload="this.classList.add('show'); this.parentElement.classList.add('has-local-img');"
           onerror="this.remove();">
      <div class="fallback">
        <div class="placeholder-icon">▣</div>
        <button type="button" class="load-btn">Load preview</button>
      </div>
    </div>
  `;
}

function loadOneEmbed(frame) {
  const url = frame.dataset.url;
  loadIgScript();
  frame.classList.add("loaded");
  frame.innerHTML = `
    <blockquote class="instagram-media" data-instgrm-permalink="${escapeHTML(url)}" data-instgrm-captioned style="background:transparent; border:1px solid var(--rule); margin:0; max-width:240px; width:240px;">
      <a href="${escapeHTML(url)}" target="_blank" rel="noopener">View on Instagram</a>
    </blockquote>
  `;
  setTimeout(processEmbeds, 100);
  setTimeout(processEmbeds, 800);
  setTimeout(processEmbeds, 2000);
}

function renderPanel(post) {
  const enr = post.enriched || {};
  if (enr.restaurant) {
    const r = enr.restaurant;
    if (r.type === "roundup") {
      const items = (r.entries || []).map(e => `
        <li><strong>${escapeHTML(e.name)}</strong>
          ${e.address ? `<div class="addr">${escapeHTML(e.address)}</div>` : ""}
          ${e.note ? `<div>${escapeHTML(e.note)}</div>` : ""}
        </li>
      `).join("");
      return `
        <div class="panel restaurant">
          <div class="panel-label">Roundup</div>
          <div class="panel-title">${escapeHTML(r.name)}</div>
          <div class="panel-body">
            <p>${escapeHTML(r.blurb || "")}</p>
            <ul>${items}</ul>
          </div>
        </div>
      `;
    }
    if (r.type === "recipe_misfile") {
      return `
        <div class="panel not-recipe">
          <div class="panel-label">${escapeHTML(r.note || "Note")}</div>
          <div class="panel-title">${escapeHTML(r.name)}</div>
          <div class="panel-body"><p>${escapeHTML(r.blurb || "")}</p></div>
        </div>
      `;
    }
    const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(r.name + " " + (r.address || ""))}`;
    return `
      <div class="panel restaurant">
        <div class="panel-label">Restaurant</div>
        <div class="panel-title">${escapeHTML(r.name)}</div>
        <div class="panel-body">
          ${r.address ? `<div class="addr">📍 <a class="addr-link" href="${escapeHTML(mapsUrl)}" target="_blank" rel="noopener">${escapeHTML(r.address)}</a></div>` : ""}
          ${r.neighborhood ? `<div class="addr">${escapeHTML(r.neighborhood)}</div>` : ""}
          ${r.hours ? `<div class="addr">⏱ ${escapeHTML(r.hours)}</div>` : ""}
          ${r.website ? `<div class="addr">🌐 <a class="addr-link" href="https://${escapeHTML(r.website)}" target="_blank" rel="noopener">${escapeHTML(r.website)}</a></div>` : ""}
          <p style="margin-top:10px;">${escapeHTML(r.blurb || "")}</p>
        </div>
      </div>
    `;
  }
  if (enr.reconstructed) {
    const rec = enr.reconstructed;
    const cls = rec.type === "recipe" ? "recipe reconstructed" : "not-recipe";
    return `
      <div class="panel ${cls}">
        <div class="panel-label">${escapeHTML(rec.note || (rec.type === "recipe" ? "Recipe" : "Note"))}</div>
        <div class="panel-title">${escapeHTML(rec.title || "")}</div>
        <div class="panel-body">${rec.body_html || ""}</div>
      </div>
    `;
  }
  if (post.recipe_status === "inline") {
    return `
      <div class="panel recipe">
        <div class="panel-label">Recipe</div>
        <div class="panel-body" style="font-style:italic; color:var(--ink-soft);">Recipe is in the caption above ↑</div>
      </div>
    `;
  }
  return "";
}

function stripHtml(s) {
  return (s || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function postBlurb(post) {
  const enr = post.enriched || {};
  if (enr.restaurant && enr.restaurant.name && enr.restaurant.type !== "recipe_misfile") {
    const r = enr.restaurant;
    if (r.type === "roundup") {
      const entries = (r.entries || []).map(e => [e.name, e.address, e.note].filter(Boolean).join(" — ")).join("\n");
      return [r.name, r.blurb, entries].filter(Boolean).join("\n");
    }
    return [r.name, r.address || r.neighborhood, r.blurb].filter(Boolean).join("\n");
  }
  if (enr.reconstructed) {
    const rec = enr.reconstructed;
    return [rec.title, stripHtml(rec.body_html)].filter(Boolean).join("\n");
  }
  const cap = (post.caption_clean || post.caption || "").trim();
  if (cap.length > 420) return cap.slice(0, 417) + "…";
  return cap;
}

function formatKeptItem(post) {
  const who = post.username ? "@" + post.username : (post.owner_name || "unknown");
  const cols = (post.collections || []).join(", ") || UNFILED;
  const blurb = postBlurb(post);
  return [who + " · " + cols, blurb, post.url].filter(Boolean).join("\n");
}

function buildKeptText() {
  const kept = ARCHIVE.posts.filter(p => getVerdict(p.url) === "keep");
  if (!kept.length) return "";
  const groups = new Map();
  for (const p of kept) {
    const key = (p.collections && p.collections[0]) || UNFILED;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const chunks = [];
  for (const [name, posts] of groups) {
    chunks.push(`## ${name} (${posts.length})`);
    for (const p of posts) chunks.push(formatKeptItem(p), "");
  }
  return chunks.join("\n").trim() + "\n";
}

function updateExportBox() {
  const kept = ARCHIVE.posts.filter(p => getVerdict(p.url) === "keep").length;
  const destroyed = ARCHIVE.posts.filter(p => getVerdict(p.url) === "destroy").length;
  document.getElementById("export-meta").textContent =
    `${kept} kept · ${destroyed} destroyed · ${queueCount(ARCHIVE.posts)} left`;
  const box = document.getElementById("export-box");
  const text = buildKeptText();
  if (document.activeElement !== box) box.value = text;
}

function renderPost(post, idx, contextLabel) {
  const cap = post.caption_clean || post.caption || "";
  const isShort = cap.length < 280;
  const v = getVerdict(post.url);
  const tagsHtml = (post.hashtags || []).slice(0, 12).map(h => `<span>#${escapeHTML(h)}</span>`).join("");
  const pills = (post.collections || []).map(name =>
    `<a class="col-pill" href="#/c/${encodeURIComponent(name)}">${escapeHTML(name)}</a>`
  ).join("");
  return `
    <article class="post ${v}" data-url="${escapeHTML(post.url)}">
      <div class="post-media">
        ${makeEmbed(post.url, post.shortcode)}
      </div>
      <div class="post-body">
        <div class="post-head">
          <span class="idx">${String(idx).padStart(3, "0")}${contextLabel ? " · " + escapeHTML(contextLabel) : ""}</span>
          <span class="type ${post.type}">${escapeHTML(post.type)}</span>
        </div>
        <div class="author">${post.username ? '<span class="at">@</span>' + escapeHTML(post.username) : "<em>unknown</em>"}</div>
        ${post.owner_name ? `<div class="owner-name">${escapeHTML(post.owner_name)}</div>` : ""}
        <div class="caption ${!cap ? "empty" : ""} ${isShort ? "short" : ""}">${escapeHTML(cap)}</div>
        ${renderPanel(post)}
        ${pills ? `<div class="col-pills">${pills}</div>` : ""}
        ${tagsHtml ? `<div class="hashtags">${tagsHtml}</div>` : ""}
        <div class="post-foot">
          <a class="open" href="${escapeHTML(post.url)}" target="_blank" rel="noopener">Open on IG</a>
          <div class="verdicts">
            <label class="verdict keep">
              <input type="checkbox" data-verdict="keep" data-url="${escapeHTML(post.url)}" ${v === "keep" ? "checked" : ""}>
              Keep
            </label>
            <label class="verdict destroy">
              <input type="checkbox" data-verdict="destroy" data-url="${escapeHTML(post.url)}" ${v === "destroy" ? "checked" : ""}>
              Destroy
            </label>
          </div>
        </div>
      </div>
    </article>
  `;
}

function applyVerdictToCard(card, url, value) {
  card.classList.remove("keep", "destroy");
  if (value) card.classList.add(value);
  card.querySelectorAll("input[data-verdict]").forEach(input => {
    input.checked = input.dataset.verdict === value;
  });
  const route = parseHash();
  const fake = { url };
  if (!matchesStatus(fake, route.status || "queue")) {
    card.classList.add("cleared");
  } else {
    card.classList.remove("cleared");
  }
  const shown = document.querySelectorAll(".post:not(.cleared)").length;
  const meta = document.getElementById("result-meta");
  if (meta) meta.textContent = `${shown} shown`;
  const sectionMeta = document.querySelector(".section-meta");
  if (sectionMeta) {
    sectionMeta.textContent = sectionMeta.textContent.replace(/^\d+ shown/, `${shown} shown`);
  }
  updateExportBox();
}

function bindPostEvents(root) {
  root.querySelectorAll(".caption").forEach(c => {
    c.addEventListener("click", () => c.classList.toggle("expanded"));
  });
  root.querySelectorAll("input[data-verdict]").forEach(input => {
    input.addEventListener("change", () => {
      const url = input.dataset.url;
      const card = input.closest(".post");
      let value = input.dataset.verdict;
      if (!input.checked) value = "";
      setVerdict(url, value);
      applyVerdictToCard(card, url, value);
    });
  });
  root.querySelectorAll(".load-btn").forEach(btn => {
    btn.addEventListener("click", () => loadOneEmbed(btn.closest(".embed-frame")));
  });
}

function sectionTitle(route) {
  if (route.collection) return route.collection;
  if (route.q) return "Search";
  return "All saved";
}

function renderList(route, posts) {
  const main = document.getElementById("main");
  if (!posts.length) {
    main.innerHTML = `<p class="empty">${route.status === "queue" ? "Queue is clear." : "Nothing matches that filter."}</p>`;
    return;
  }
  const col = (ARCHIVE.collections || []).find(c => c.name === route.collection);
  const updated = col ? formatDate(col.update_ts) : "";
  const context = route.collection || "all";
  main.innerHTML = `
    <section class="collection">
      <div class="section-head">
        <div class="section-num">${route.q ? "SEARCH" : "QUEUE"}</div>
        <h2 class="section-title">${escapeHTML(sectionTitle(route))}</h2>
        <div class="section-meta">${posts.length} shown${updated ? " · updated " + updated : ""}</div>
      </div>
      <div class="posts">
        ${posts.map((p, i) => renderPost(p, i + 1, (p.collections || [])[0] || context)).join("")}
      </div>
    </section>
  `;
  bindPostEvents(main);
}

function mosaicFor(col) {
  const cells = [];
  for (let i = 0; i < 4; i++) {
    const p = col.covers[i];
    if (p) cells.push(`<img src="${escapeHTML(thumbPath(p))}" alt="" onerror="this.outerHTML='&lt;div class=ph&gt;▣&lt;/div&gt;'">`);
    else cells.push(`<div class="ph">▣</div>`);
  }
  return `<div class="mosaic">${cells.join("")}</div>`;
}

function renderHome() {
  const main = document.getElementById("main");
  const collections = collectionCatalog().map(c => {
    const posts = ARCHIVE.posts.filter(p => matchesCollection(p, c.name));
    return {
      href: `#/c/${encodeURIComponent(c.name)}`,
      name: c.name,
      total: posts.length,
      left: queueCount(posts),
      covers: c.covers || [],
    };
  });
  main.innerHTML = `
    <div class="home-block">
      <div class="home-label">Your collections</div>
      <div class="collection-grid">
        ${collections.map(c => `
          <a class="collection-card" href="${c.href}">
            ${mosaicFor(c)}
            <div class="card-name">${escapeHTML(c.name)}</div>
            <div class="card-count">${c.left} left · ${c.total} items</div>
          </a>
        `).join("")}
      </div>
    </div>
  `;
}

function showToast(msg) {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1600);
}

let lastViewKey = "";

function render() {
  if (!ARCHIVE.posts) {
    document.getElementById("main").innerHTML = `<p class="empty">No archive data. Run <code>python3 build.py</code>.</p>`;
    return;
  }
  const route = parseHash();
  const qEl = document.getElementById("q");
  if (document.activeElement !== qEl) qEl.value = route.q;
  renderNav(route);
  document.title = route.view === "home" && !route.q ? "Saved" : `Saved · ${sectionTitle(route)}`;

  if (route.view === "home" && !route.q) {
    renderMasthead(route);
    renderFilters(route, ARCHIVE.posts.length);
    renderHome();
  } else {
    const posts = filterPosts(route);
    renderMasthead(route);
    renderFilters(route, posts.length);
    renderList(route, posts);
  }
  updateExportBox();
  const viewKey = [route.view, route.collection || "", route.status || "", route.type || "", route.q || ""].join("|");
  if (viewKey !== lastViewKey && !route.q) {
    window.scrollTo(0, 0);
  }
  lastViewKey = viewKey;
}

document.getElementById("search-form").addEventListener("submit", e => {
  e.preventDefault();
  const next = parseHash();
  next.q = document.getElementById("q").value.trim();
  if (next.view === "home" && next.q) next.view = "all";
  setHash(next);
});
document.getElementById("q").addEventListener("input", () => {
  clearTimeout(window.__qTimer);
  window.__qTimer = setTimeout(() => {
    const next = parseHash();
    next.q = document.getElementById("q").value.trim();
    if (next.view === "home" && next.q) next.view = "all";
    if (!next.q && next.view === "all" && !next.collection) next.view = "home";
    setHash(next);
  }, 180);
});

document.getElementById("copy-kept").addEventListener("click", async () => {
  const text = buildKeptText();
  if (!text) {
    showToast("Nothing kept yet");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied kept list");
  } catch {
    const box = document.getElementById("export-box");
    box.focus();
    box.select();
    showToast("Select and copy");
  }
});

window.addEventListener("hashchange", render);
render();
