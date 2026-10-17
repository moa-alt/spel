"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const VIEW_KEY = "kh-studio-vy";
const state = {
  article: null,      // innehållet som visas i editorn
  current: null,      // artikelns post i kön (status, historik m.m.)
  queue: [],
  filter: "alla",
  status: {},
  sekundara: [],      // sekundära sökord i formuläret "Ny artikel"
  qSekundara: [],     // sekundära sökord i köns formulär
  editing: false,
};

const STATUS_NAMN = {
  utkast: "Utkast", genereras: "Genereras", granskas: "Väntar på granskning", godkand: "Godkänd",
  schemalagd: "Schemalagd", publicerad: "Publicerad", fel: "Fel", avvisad: "Avvisad",
};
const REDIGERBAR = ["utkast", "granskas", "godkand"];

/* ---------- Hjälpfunktioner ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function slugify(text) {
  return text.toLowerCase().replace(/[åä]/g, "a").replace(/ö/g, "o")
    .normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 75);
}
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove("show"), 2600);
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* privat läge */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function formatTid(iso) {
  return iso ? new Date(iso).toLocaleString("sv-SE", { dateStyle: "medium", timeStyle: "short" }) : "";
}
async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body !== undefined ? "POST" : "GET"),
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return {};
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : res.status === 422 ? "Kontrollera fälten" : `Fel ${res.status}`);
  return data;
}

// Sparar redigeringar i databasen kort efter att du slutat skriva.
const save = debounce(async () => {
  if (!state.current || !state.article) return;
  const id = state.current.id;
  $("#saveState").textContent = "Sparar…";
  try {
    const res = await api(`/api/artiklar/${id}/innehall`, state.article, "PUT");
    if (state.current?.id !== id) return;
    $("#saveState").textContent = "Sparat";
    if (res.status !== state.current.status) {
      if (state.current.status === "godkand") toast("Godkännandet upphävdes eftersom artikeln ändrades");
      await openArticle(id, { keepView: true });
      loadQueue();
    }
  } catch (err) {
    $("#saveState").textContent = "Kunde inte spara";
    toast(err.message);
  }
}, 700);

/* ---------- Status ---------- */
async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch { state.status = {}; }
  $$("#status .pill").forEach((p) => {
    const on = !!state.status[p.dataset.key];
    p.classList.toggle("on", on);
    p.title = on ? "Konfigurerad" : "Inte konfigurerad ännu";
  });
  const wp = $("#wpBtn");
  wp.disabled = !state.status.wordpress;
  wp.title = state.status.wordpress ? "Skapar ett utkast i WordPress" : "WordPress kopplas in i ett senare steg";
}

/* ---------- Sekundära sökord (chips) ---------- */
function chipInput(box, list, input, key) {
  const render = () => {
    list.innerHTML = state[key]
      .map((v, i) => `<span class="chip">${esc(v)}<button type="button" data-i="${i}" aria-label="Ta bort ${esc(v)}">×</button></span>`)
      .join("");
  };
  const add = (value) => {
    value.split(",").map((v) => v.trim()).filter(Boolean).forEach((v) => {
      if (!state[key].some((x) => x.toLowerCase() === v.toLowerCase())) state[key].push(v);
    });
    render();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(input.value);
      input.value = "";
    } else if (e.key === "Backspace" && !input.value && state[key].length) {
      state[key].pop();
      render();
    }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) { add(input.value); input.value = ""; } });
  list.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    state[key].splice(Number(b.dataset.i), 1);
    render();
  });
  box.addEventListener("click", (e) => { if (e.target === box || e.target === list) input.focus(); });
  // Tar med text som skrivits men inte bekräftats med Enter.
  return { render, flush: () => { if (input.value.trim()) { add(input.value); input.value = ""; } } };
}
const briefChips = chipInput($("#chipsBox"), $("#chips"), $("#chipInput"), "sekundara");
const queueChips = chipInput($("#qChipsBox"), $("#qChips"), $("#qChipInput"), "qSekundara");

/* ---------- Generering ---------- */
const STEG_TEXT = ["Analyserar sökintention", "Skriver artikeln", "Skapar 4 bilder (WebP)", "Optimerar metadata"];

function resetProgress() {
  $$("#progress li").forEach((li, i) => { li.className = ""; li.textContent = STEG_TEXT[i]; });
  if (!state.status.bilder) {
    const bild = $('#progress li[data-step="2"]');
    bild.classList.add("skipped");
    bild.textContent = "Bilder – kopplas in senare";
  }
}

// Visar förloppet för artikeln som är öppen i editorn.
function renderProgress(rec) {
  const box = $("#progress");
  if (!rec || rec.status !== "genereras") {
    if (!box.hidden && rec) {
      $$("#progress li:not(.skipped)").forEach((li) => { li.classList.remove("active"); li.classList.add(rec.status === "fel" ? "failed" : "done"); });
      $('#progress li[data-step="1"]').textContent = STEG_TEXT[1];
    }
    return;
  }
  if (box.hidden) { box.hidden = false; resetProgress(); }
  const steg = rec.forlopp ? Math.max(0, rec.forlopp.steg) : 0;
  $$("#progress li").forEach((li, i) => {
    if (li.classList.contains("skipped")) return;
    li.classList.toggle("done", i < steg);
    li.classList.toggle("active", i === steg);
  });
  if (rec.forlopp?.tecken) $('#progress li[data-step="1"]').textContent = `Skriver artikeln… ${rec.forlopp.tecken.toLocaleString("sv-SE")} tecken`;
}

$("#brief").addEventListener("submit", async (e) => {
  e.preventDefault();
  briefChips.flush();
  const huvudsokord = $("#huvudsokord").value.trim();
  if (huvudsokord.length < 2) return;
  const btn = $("#genBtn");
  btn.disabled = true;
  try {
    const rec = await api("/api/artiklar", { huvudsokord, sekundara_sokord: state.sekundara });
    await api(`/api/artiklar/${rec.id}/generera`, {});
    $("#huvudsokord").value = "";
    state.sekundara = [];
    briefChips.render();
    $("#progress").hidden = true;
    await openArticle(rec.id);
    await loadQueue();
    toast("Artikeln ligger i kön och genereras nu");
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
});

/* ---------- Artikel i editorn ---------- */
function setArticle(article) {
  state.article = article;
  $("#empty").hidden = true;
  $("#editor").hidden = false;
  $("#checksCard").hidden = false;
  $("#demoBadge").hidden = !article.demo;
  $("#titel").value = article.titel;
  $("#meta_titel").value = article.meta_titel;
  $("#meta_beskrivning").value = article.meta_beskrivning;
  $("#slug").value = article.slug;
  $("#htmlEditor").value = article.html;
  renderKeywords();
  renderIntent();
  renderImages();
  renderSeo();
  renderPreview();
}

const INTENT_NAMN = { informativ: "Informativ", kommersiell: "Kommersiell", transaktionell: "Transaktionell", navigerande: "Navigerande" };

function renderIntent() {
  const si = state.article.sokintention;
  $("#intentCard").hidden = !si;
  if (!si) return;
  $("#intentTyp").textContent = INTENT_NAMN[si.typ] || si.typ;
  $("#intentBeskrivning").textContent = si.beskrivning;
  $("#intentMalgrupp").textContent = si.malgrupp;
}

function renderKeywords() {
  const a = state.article;
  $("#kwSummary").innerHTML =
    `<span class="chip main">${esc(a.huvudsokord)}</span>` +
    (a.sekundara_sokord || []).map((s) => `<span class="chip">${esc(s)}</span>`).join("");
}

/* ---------- Förhandsgranskning ---------- */
function placeholderFigure(b) {
  return `<figure class="kh-bild kh-platshallare" data-bild="${b.id}" contenteditable="false">
    <div class="kh-ph"><span>Bild ${b.id}</span><small>${esc(b.alt)}</small></div>
    ${b.bildtext ? `<figcaption>${esc(b.bildtext)}</figcaption>` : ""}
  </figure>`;
}
function imageFigure(b) {
  return `<figure class="kh-bild" data-bild="${b.id}" contenteditable="false">
    <img src="${esc(b.url)}" alt="${esc(b.alt)}">
    ${b.bildtext ? `<figcaption>${esc(b.bildtext)}</figcaption>` : ""}
  </figure>`;
}
function articleBodyForPreview() {
  const bilder = Object.fromEntries((state.article.bilder || []).map((b) => [b.id, b]));
  return state.article.html.replace(/<!--BILD:(\d+)-->/g, (_, id) => {
    const b = bilder[id];
    if (!b) return "";
    return b.url ? imageFigure(b) : placeholderFigure(b);
  });
}

const PREVIEW_BASE_CSS = `
  body { margin: 0; padding: 40px 24px 80px; font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; background: #fff; }
  .kh-h1 { max-width: 760px; margin: 0 auto 24px; font-size: 2.3em; line-height: 1.15; color: #1f4a36; letter-spacing: -.01em; }
  .kh-platshallare .kh-ph { aspect-ratio: 3 / 2; border-radius: 12px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
    background: repeating-linear-gradient(45deg, #eef5f0, #eef5f0 12px, #e5efe8 12px, #e5efe8 24px); color: #2f6b4f; border: 1px dashed #b7d0c0; text-align: center; padding: 16px; }
  .kh-platshallare .kh-ph span { font-weight: 700; }
  .kh-platshallare .kh-ph small { color: #5b6a62; max-width: 80%; }
  [contenteditable="true"]:focus { outline: none; }
  @media (max-width: 640px) { body { padding: 24px 16px 60px; } .kh-h1 { font-size: 1.8em; } }
`;

function renderPreview() {
  const frame = $("#previewFrame");
  const prevScroll = frame.contentWindow?.scrollY || 0;
  const editable = state.editing ? ' contenteditable="true"' : "";
  frame.srcdoc = `<!doctype html><html lang="sv"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap">
    <link rel="stylesheet" href="${location.origin}/static/artikel.css">
    <style>${PREVIEW_BASE_CSS}</style></head>
    <body><h1 class="kh-h1">${esc($("#titel").value)}</h1>
    <div class="kh-artikel" id="artikel"${editable}>${articleBodyForPreview()}</div></body></html>`;
  frame.onload = () => {
    frame.contentWindow.scrollTo(0, prevScroll);
    const doc = frame.contentDocument;
    // Interna länkar (innehållsförteckning) scrollar inom förhandsvisningen.
    doc.addEventListener("click", (e) => {
      const a = e.target.closest("a[href^='#']");
      if (!a || state.editing) return;
      e.preventDefault();
      doc.getElementById(a.getAttribute("href").slice(1))?.scrollIntoView({ behavior: "smooth" });
    });
    if (state.editing) doc.getElementById("artikel").addEventListener("input", syncFromPreview);
  };
  updateWordCount();
}
const renderPreviewSoon = debounce(renderPreview, 350);

// Läser tillbaka texten från den redigerbara förhandsvisningen och ersätter bilderna med markörer igen.
const syncFromPreview = debounce(() => {
  const root = $("#previewFrame").contentDocument?.getElementById("artikel");
  if (!root) return;
  const clone = root.cloneNode(true);
  clone.querySelectorAll("figure[data-bild]").forEach((f) => f.replaceWith(document.createComment(`BILD:${f.dataset.bild}`)));
  state.article.html = clone.innerHTML.trim();
  $("#htmlEditor").value = state.article.html;
  renderSeo();
  updateWordCount();
  save();
}, 300);

function textOf(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div.textContent.replace(/\s+/g, " ").trim();
}
function wordCount() {
  const t = textOf(state.article.html);
  return t ? t.split(" ").length : 0;
}
function updateWordCount() {
  $("#wordCount").textContent = `${wordCount().toLocaleString("sv-SE")} ord · ${Math.max(1, Math.round(wordCount() / 200))} min läsning`;
}

$$(".seg-btn").forEach((b) => b.addEventListener("click", () => {
  $$(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  $("#frameWrap").classList.toggle("mobile", b.dataset.device === "mobile");
}));
$("#editToggle").addEventListener("change", (e) => {
  state.editing = e.target.checked;
  $("#frameWrap").classList.toggle("editing", state.editing);
  renderPreview();
  if (state.editing) toast("Klicka i texten för att redigera");
});

/* ---------- Titel, HTML och SEO-fält ---------- */
$("#titel").addEventListener("input", (e) => {
  state.article.titel = e.target.value;
  renderSeo(); renderPreviewSoon(); save();
});
$("#htmlEditor").addEventListener("input", (e) => {
  state.article.html = e.target.value;
  renderSeo(); renderPreviewSoon(); save();
});
["meta_titel", "meta_beskrivning"].forEach((id) => $("#" + id).addEventListener("input", (e) => {
  state.article[id] = e.target.value;
  renderSeo(); save();
}));
$("#slug").addEventListener("input", (e) => { state.article.slug = e.target.value; renderSeo(); save(); });
$("#slug").addEventListener("blur", (e) => {
  e.target.value = slugify(e.target.value);
  state.article.slug = e.target.value;
  renderSeo(); save();
});

function setCounter(id, len) {
  const el = $(`.counter[data-for="${id}"]`);
  const max = Number(el.dataset.max);
  el.textContent = `${len} / ${max}`;
  el.className = "counter" + (len > max ? " bad" : len < max * 0.6 ? " warn" : "");
}

function renderSeo() {
  const a = state.article;
  setCounter("meta_titel", a.meta_titel.length);
  setCounter("meta_beskrivning", a.meta_beskrivning.length);
  $("#serpTitle").textContent = a.meta_titel.length > 60 ? a.meta_titel.slice(0, 59) + " …" : a.meta_titel;
  $("#serpDesc").textContent = a.meta_beskrivning.length > 158 ? a.meta_beskrivning.slice(0, 155) + " …" : a.meta_beskrivning;
  $("#serpUrl").textContent = `https://klimathemmet.se › ${a.slug}`;
  renderChecks();
}

function renderChecks() {
  const a = state.article;
  const kw = (a.huvudsokord || "").toLowerCase();
  const has = (s) => s.toLowerCase().includes(kw);
  const text = textOf(a.html).toLowerCase();
  const firstP = textOf((a.html.match(/<p[\s\S]*?<\/p>/i) || [""])[0]).toLowerCase();
  const h2 = (a.html.match(/<h2[\s>]/gi) || []).length;
  const sek = a.sekundara_sokord || [];
  const sekHits = sek.filter((s) => text.includes(s.toLowerCase())).length;
  const altOk = (a.bilder || []).every((b) => b.alt.trim().length > 5);
  const ord = wordCount();
  const mt = a.meta_titel.length, md = a.meta_beskrivning.length;

  const checks = [
    [has(a.titel) ? "ok" : "bad", "Huvudsökordet finns i rubriken (H1)"],
    [has(a.meta_titel) ? "ok" : "bad", "Huvudsökordet finns i metatiteln"],
    [mt >= 30 && mt <= 60 ? "ok" : "warn", `Metatitel ${mt} tecken (30–60)`],
    [has(a.meta_beskrivning) ? "ok" : "warn", "Huvudsökordet finns i metabeskrivningen"],
    [md >= 120 && md <= 155 ? "ok" : "warn", `Metabeskrivning ${md} tecken (120–155)`],
    [a.slug && slugify(kw).split("-").every((w) => a.slug.includes(w)) ? "ok" : "warn", "Slugen innehåller sökordet"],
    [firstP.includes(kw) ? "ok" : "warn", "Sökordet nämns i första stycket"],
    [h2 >= 3 ? "ok" : "warn", `${h2} underrubriker (H2)`],
    [!sek.length || sekHits === sek.length ? "ok" : "warn", `Sekundära sökord i texten: ${sekHits}/${sek.length}`],
    [altOk ? "ok" : "bad", "Alla bilder har alt-text"],
    [ord >= 800 ? "ok" : "warn", `Textlängd ${ord} ord (rek. 800+)`],
  ];
  const okCount = checks.filter(([s]) => s === "ok").length;
  $("#score").textContent = `${Math.round((okCount / checks.length) * 100)}%`;
  $("#checks").innerHTML = checks.map(([s, t]) => `<li class="${s === "ok" ? "" : s}">${esc(t)}</li>`).join("");
}

/* ---------- Bilder ---------- */
function renderImages() {
  $("#images").innerHTML = state.article.bilder.map((b, i) => `
    <div class="img-card">
      <div class="img-thumb"><span class="num">Bild ${b.id}</span>
        ${b.url ? `<img src="${esc(b.url)}" alt="${esc(b.alt)}">` : "<span>Genereras i steg 3</span>"}
      </div>
      <div class="img-body">
        <label class="field"><span class="label">Alt-text</span>
          <input type="text" data-i="${i}" data-f="alt" value="${esc(b.alt)}"></label>
        <label class="field"><span class="label">Bildtext <small>valfri</small></span>
          <input type="text" data-i="${i}" data-f="bildtext" value="${esc(b.bildtext)}"></label>
        <label class="field"><span class="label">Bildbeskrivning till AI</span>
          <textarea rows="2" data-i="${i}" data-f="prompt">${esc(b.prompt)}</textarea></label>
      </div>
    </div>`).join("");
}
$("#images").addEventListener("input", (e) => {
  const el = e.target.closest("[data-f]");
  if (!el) return;
  state.article.bilder[Number(el.dataset.i)][el.dataset.f] = el.value;
  renderChecks(); renderPreviewSoon(); save();
});

/* ---------- Flikar ---------- */
$$(".tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === tab.dataset.tab));
  if (tab.dataset.tab === "code") loadExport();
}));

/* ---------- Export och WordPress ---------- */
async function exportCode() {
  return (await api("/api/exportera", state.article)).kod;
}
async function loadExport() {
  try { $("#codeOut").value = await exportCode(); } catch (err) { toast(err.message); }
}
$("#copyBtn").addEventListener("click", async () => {
  try {
    const kod = await exportCode();
    await navigator.clipboard.writeText(kod);
    toast("Koden är kopierad – klistra in i ett Anpassad HTML-block");
  } catch (err) { toast(err.message || "Kunde inte kopiera"); }
});
$("#wpBtn").addEventListener("click", async () => {
  try {
    await api("/api/wordpress", state.article);
    toast("Utkastet är skapat i WordPress");
  } catch (err) { toast(err.message); }
});

/* ---------- Vyer ---------- */
function showView(view) {
  $("#queueView").hidden = view !== "queue";
  $("#editorView").hidden = view !== "editor";
  $$(".view-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  remember(VIEW_KEY, view);
  if (view === "queue") loadQueue();
}
$$(".view-btn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
$("#backBtn").addEventListener("click", () => showView("queue"));

/* ---------- Artikelkö ---------- */
let pollTimer = null;

async function loadQueue() {
  try {
    state.queue = (await api("/api/artiklar")).artiklar;
  } catch (err) {
    toast(err.message);
    return;
  }
  renderQueue();
  // Håll editorn i synk med artikeln som är öppen.
  const open = state.current && state.queue.find((a) => a.id === state.current.id);
  if (open) {
    renderProgress(open);
    if (open.status !== state.current.status) openArticle(open.id, { keepView: true });
  }
  clearTimeout(pollTimer);
  if (state.queue.some((a) => a.status === "genereras")) pollTimer = setTimeout(loadQueue, 2000);
}

function renderFilters() {
  const antal = { alla: state.queue.length };
  state.queue.forEach((a) => { antal[a.status] = (antal[a.status] || 0) + 1; });
  const filter = ["alla", ...Object.keys(STATUS_NAMN)].filter((k) => k === "alla" || antal[k]);
  if (!filter.includes(state.filter)) state.filter = "alla";
  $("#filters").innerHTML = filter.map((k) =>
    `<button type="button" class="filter${state.filter === k ? " active" : ""}" data-filter="${k}">${k === "alla" ? "Alla" : STATUS_NAMN[k]}<b>${antal[k]}</b></button>`
  ).join("");
  $("#queueCount").textContent = antal.granskas ? String(antal.granskas) : "";
  $("#queueCount").title = antal.granskas ? `${antal.granskas} väntar på granskning` : "";
}

function statusBadge(status) {
  return `<span class="status-badge s-${status}">${STATUS_NAMN[status] || status}</span>`;
}

function queueActions(a) {
  const b = (act, text, cls = "btn-ghost") => `<button type="button" class="btn ${cls} btn-sm" data-act="${act}" data-id="${a.id}">${text}</button>`;
  const ut = [];
  if (a.status === "granskas") ut.push(b("open", "Granska", "btn-primary"));
  else if (a.har_innehall && a.status !== "genereras") ut.push(b("open", "Öppna"));
  if (a.status === "utkast" && !a.har_innehall) ut.push(b("generate", "Generera", "btn-primary"));
  if (a.status === "utkast" && a.andringar) ut.push(b("generate", "Generera om med ändringar"));
  if (a.status === "fel") ut.push(b("generate", "Försök igen", "btn-primary"));
  if (a.status === "avvisad") ut.push(b("restore", "Återställ"));
  if (!["genereras", "schemalagd", "publicerad"].includes(a.status)) {
    ut.push(b("edit", "Sökord"));
    ut.push(b("delete", "Ta bort", "btn-ghost btn-danger"));
  }
  return ut.join("");
}

function renderQueue() {
  renderFilters();
  const lista = state.queue.filter((a) => state.filter === "alla" || a.status === state.filter);
  const kanFlytta = state.filter === "alla";
  $("#queueEmpty").hidden = state.queue.length > 0;
  $("#queueList").innerHTML = lista.map((a) => {
    const i = state.queue.indexOf(a);
    const notes = [];
    if (a.status === "genereras") {
      const f = a.forlopp;
      notes.push(`<div class="q-note info">${f && f.steg >= 1 ? `Skriver… ${(f.tecken || 0).toLocaleString("sv-SE")} tecken` : "Analyserar sökintention…"}</div>`);
    }
    if (a.fel) notes.push(`<div class="q-note bad">⚠ ${esc(a.fel)}</div>`);
    if (a.andringar) notes.push(`<div class="q-note warn">Begärda ändringar: ${esc(a.andringar)}</div>`);
    if (a.liknande.length) {
      notes.push(`<div class="q-note warn">Liknar ${a.liknande.map((l) => `<a href="#" data-act="goto" data-id="${l.id}">”${esc(l.huvudsokord)}”</a>`).join(", ")} – risk att artiklarna konkurrerar om samma sökningar</div>`);
    }
    return `<li class="q-item" data-id="${a.id}" draggable="${kanFlytta}">
      <div class="q-order">
        <button type="button" class="q-handle" aria-hidden="true" tabindex="-1" ${kanFlytta ? "" : "disabled"}>⋮⋮</button>
        <span class="q-pos">${i + 1}</span>
        <span class="q-move">
          <button type="button" data-act="up" data-id="${a.id}" aria-label="Flytta upp" ${!kanFlytta || i === 0 ? "disabled" : ""}>▲</button>
          <button type="button" data-act="down" data-id="${a.id}" aria-label="Flytta ned" ${!kanFlytta || i === state.queue.length - 1 ? "disabled" : ""}>▼</button>
        </span>
      </div>
      <div class="q-main">
        <div class="q-kw">${esc(a.huvudsokord)}</div>
        ${a.titel ? `<div class="q-title">${esc(a.titel)}</div>` : ""}
        ${a.sekundara_sokord.length ? `<div class="chips">${a.sekundara_sokord.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : ""}
        ${notes.join("")}
      </div>
      <div class="q-side">${statusBadge(a.status)}${queueActions(a)}</div>
    </li>`;
  }).join("");
}

async function moveTo(ids) {
  const byId = Object.fromEntries(state.queue.map((a) => [a.id, a]));
  state.queue = ids.map((id) => byId[id]);
  renderQueue();
  try { await api("/api/artiklar/ordning", { ids }, "PUT"); } catch (err) { toast(err.message); loadQueue(); }
}

function editKeywords(id) {
  const a = state.queue.find((x) => x.id === id);
  const li = $(`.q-item[data-id="${id}"] .q-main`);
  li.innerHTML = `<form class="q-edit">
      <input type="text" name="huvud" value="${esc(a.huvudsokord)}" aria-label="Huvudsökord" required minlength="2" maxlength="120">
      <input type="text" name="sek" value="${esc(a.sekundara_sokord.join(", "))}" aria-label="Sekundära sökord" placeholder="Sekundära sökord, kommaseparerade">
      <div class="q-edit-actions"><button class="btn btn-primary btn-sm" type="submit">Spara</button><button class="btn btn-ghost btn-sm" type="button" data-act="cancel">Avbryt</button></div>
    </form>`;
  const form = $("form", li);
  form.huvud.focus();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/artiklar/${id}/sokord`, {
        huvudsokord: form.huvud.value,
        sekundara_sokord: form.sek.value.split(",").map((s) => s.trim()).filter(Boolean),
      }, "PATCH");
      toast(a.har_innehall ? "Sökorden är sparade – generera om texten för att använda dem" : "Sökorden är sparade");
      loadQueue();
    } catch (err) { toast(err.message); }
  });
}

$("#filters").addEventListener("click", (e) => {
  const f = e.target.closest("[data-filter]");
  if (!f) return;
  state.filter = f.dataset.filter;
  renderQueue();
});

$("#queueList").addEventListener("click", async (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  e.preventDefault();
  const id = Number(el.dataset.id);
  const ids = state.queue.map((a) => a.id);
  const i = ids.indexOf(id);
  try {
    switch (el.dataset.act) {
      case "open": await openArticle(id); break;
      case "goto": $(`.q-item[data-id="${id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" }); break;
      case "generate": await api(`/api/artiklar/${id}/generera`, {}); toast("Genereringen har startat"); await loadQueue(); break;
      case "restore": await api(`/api/artiklar/${id}/aterstall`, {}); await loadQueue(); break;
      case "edit": editKeywords(id); break;
      case "cancel": renderQueue(); break;
      case "up": if (i > 0) { [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; await moveTo(ids); } break;
      case "down": if (i < ids.length - 1) { [ids[i + 1], ids[i]] = [ids[i], ids[i + 1]]; await moveTo(ids); } break;
      case "delete": {
        const a = state.queue[i];
        if (!confirm(`Ta bort ”${a.huvudsokord}” från kön?${a.har_innehall ? " Den genererade texten försvinner." : ""}`)) return;
        await api(`/api/artiklar/${id}`, undefined, "DELETE");
        if (state.current?.id === id) { state.current = null; state.article = null; }
        await loadQueue();
        break;
      }
    }
  } catch (err) { toast(err.message); }
});

// Dra och släpp för att ändra ordning.
let dragId = null;
$("#queueList").addEventListener("dragstart", (e) => {
  const li = e.target.closest(".q-item");
  if (!li || state.filter !== "alla") return;
  dragId = Number(li.dataset.id);
  li.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
});
$("#queueList").addEventListener("dragover", (e) => {
  if (dragId === null) return;
  e.preventDefault();
  $$(".q-item.drop-before").forEach((x) => x.classList.remove("drop-before"));
  e.target.closest(".q-item")?.classList.add("drop-before");
});
$("#queueList").addEventListener("drop", (e) => {
  e.preventDefault();
  const target = e.target.closest(".q-item");
  if (dragId === null || !target) return;
  const ids = state.queue.map((a) => a.id).filter((id) => id !== dragId);
  ids.splice(ids.indexOf(Number(target.dataset.id)), 0, dragId);
  moveTo(ids);
});
$("#queueList").addEventListener("dragend", () => {
  dragId = null;
  $$(".q-item.dragging, .q-item.drop-before").forEach((x) => x.classList.remove("dragging", "drop-before"));
});

$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  queueChips.flush();
  try {
    await api("/api/artiklar", { huvudsokord: $("#qHuvud").value, sekundara_sokord: state.qSekundara });
    $("#qHuvud").value = "";
    state.qSekundara = [];
    queueChips.render();
    $("#qHuvud").focus();
    await loadQueue();
  } catch (err) { toast(err.message); }
});

$("#csvInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const res = await fetch("/api/artiklar/import", { method: "POST", headers: { "Content-Type": "text/csv" }, body: await file.arrayBuffer() });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fel ${res.status}`);
    const dubb = data.dubbletter.length ? `, ${data.dubbletter.length} fanns redan och hoppades över` : "";
    toast(`${data.importerade} artiklar importerade${dubb}`);
    await loadQueue();
  } catch (err) { toast(err.message); }
});

$("#genAllBtn").addEventListener("click", async () => {
  const antal = state.queue.filter((a) => a.status === "utkast" && !a.har_innehall).length;
  if (!antal) { toast("Det finns inga utkast som behöver genereras"); return; }
  if (!confirm(`Generera ${antal} artiklar med AI? De sparas som Väntar på granskning.`)) return;
  try {
    const res = await api("/api/artiklar/generera-alla", {});
    toast(`${res.startade} artiklar genereras – två åt gången`);
    await loadQueue();
  } catch (err) { toast(err.message); }
});

/* ---------- Öppna artikel och granskning ---------- */
async function openArticle(id, { keepView = false } = {}) {
  let rec;
  try { rec = await api(`/api/artiklar/${id}`); } catch (err) { toast(err.message); return; }
  state.current = rec;
  remember("kh-studio-artikel", String(id));
  if (!keepView) showView("editor");
  $("#progress").hidden = rec.status !== "genereras";
  if (rec.status === "genereras") { resetProgress(); renderProgress(rec); }
  $("#saveState").textContent = "";
  if (rec.innehall && rec.status !== "genereras") {
    setArticle(rec.innehall);
  } else {
    state.article = null;
    $("#editor").hidden = true;
    $("#checksCard").hidden = true;
    $("#empty").hidden = false;
    $("#empty h1").textContent = rec.status === "genereras" ? `Genererar ”${rec.huvudsokord}”` : `”${rec.huvudsokord}” har ingen text ännu`;
    $("#empty p").textContent = rec.status === "genereras"
      ? "AI:n arbetar på servern och fortsätter även om du stänger fönstret. Förloppet syns till vänster."
      : "Gå till artikelkön och välj Generera, eller skapa en ny artikel i formuläret.";
  }
  renderReview();
  renderHistory();
}

function renderReview() {
  const rec = state.current;
  if (!rec) return;
  const s = rec.status;
  $("#statusBadge").className = `status-badge s-${s}`;
  $("#statusBadge").textContent = STATUS_NAMN[s];
  const visa = {
    regenBtn: ["utkast", "granskas", "fel"].includes(s),
    toReviewBtn: s === "utkast" && rec.har_innehall,
    rejectBtn: ["utkast", "granskas", "godkand", "fel"].includes(s),
    changesBtn: ["granskas", "godkand"].includes(s),
    revokeBtn: s === "godkand",
    approveBtn: s === "granskas",
  };
  Object.entries(visa).forEach(([id, v]) => { $("#" + id).hidden = !v; });
  $("#regenBtn").textContent = rec.andringar ? "Generera om med ändringar" : "Generera om text";

  const notice = $("#notice");
  let text = "", cls = "";
  if (s === "fel") { text = `Genereringen misslyckades: ${rec.fel}`; cls = "bad"; }
  else if (rec.andringar) text = `Begärda ändringar: ”${rec.andringar}”. Redigera texten och skicka till granskning, eller generera om med ändringarna.`;
  else if (s === "godkand") { text = `Godkänd ${formatTid(rec.godkand_at)}. Om du ändrar något måste artikeln godkännas igen.`; cls = "ok"; }
  else if (s === "avvisad") text = "Artikeln är avvisad. Återställ den från artikelkön om du vill använda den.";
  else if (s === "granskas") text = "Granska text, SEO, bilder och metadata. Godkänn artikeln när den är klar för publicering.";
  notice.hidden = !text || !rec.innehall;
  notice.className = "notice" + (cls ? " " + cls : "");
  notice.textContent = text;

  const kanRedigera = REDIGERBAR.includes(s) && rec.har_innehall;
  $("#editor").classList.toggle("readonly", !kanRedigera);
  $$("#editor .panel input, #editor .panel textarea:not(#codeOut), #titel").forEach((el) => { el.readOnly = !kanRedigera; });
  $("#editToggle").disabled = !kanRedigera;
  if (!kanRedigera && state.editing) { $("#editToggle").checked = false; state.editing = false; $("#frameWrap").classList.remove("editing"); renderPreview(); }
}

const HANDLING_NAMN = {
  genererad: "Text genererad", skickad_till_granskning: "Skickad till granskning", godkand: "Godkänd",
  andringar_begarda: "Ändringar begärda", avvisad: "Avvisad", aterstalld: "Återställd",
  godkannande_aterkallat: "Godkännande återkallat", godkannande_upphavt: "Godkännande upphävt",
};
function renderHistory() {
  const h = state.current?.historik || [];
  $("#historyCard").hidden = !h.length;
  $("#history").innerHTML = h.map((x) => `<li><strong>${esc(HANDLING_NAMN[x.handling] || x.handling)}</strong>
    ${x.kommentar ? `<q>${esc(x.kommentar)}</q>` : ""}<time>${formatTid(x.tid)}</time></li>`).join("");
}

function askComment({ title, text, ok, required }) {
  const dlg = $("#commentDialog");
  $("#dialogTitle").textContent = title;
  $("#dialogText").textContent = text;
  $("#dialogOk").textContent = ok;
  $("#dialogComment").value = "";
  $("#dialogComment").required = required;
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok" ? $("#dialogComment").value.trim() : null), { once: true });
  });
}

async function reviewAction(path, body, message) {
  const id = state.current.id;
  try {
    await api(`/api/artiklar/${id}/${path}`, body);
    toast(message);
    await openArticle(id, { keepView: true });
    loadQueue();
  } catch (err) { toast(err.message); }
}

$("#approveBtn").addEventListener("click", () => reviewAction("godkann", {}, "Artikeln är godkänd"));
$("#revokeBtn").addEventListener("click", () => reviewAction("aterkalla", {}, "Godkännandet är återkallat"));
$("#toReviewBtn").addEventListener("click", () => reviewAction("skicka-till-granskning", {}, "Skickad till granskning"));
$("#changesBtn").addEventListener("click", async () => {
  const kommentar = await askComment({
    title: "Begär ändringar", ok: "Begär ändringar", required: true,
    text: "Beskriv vad som ska ändras. Artikeln går tillbaka till utkast. Du kan redigera den själv eller låta AI:n skriva om den utifrån kommentaren.",
  });
  if (kommentar) reviewAction("begar-andringar", { kommentar }, "Ändringarna är begärda – artikeln är nu ett utkast");
});
$("#rejectBtn").addEventListener("click", async () => {
  const kommentar = await askComment({
    title: "Avvisa artikeln", ok: "Avvisa", required: false,
    text: "Artikeln flyttas till Avvisad och kan inte godkännas eller publiceras. Du kan återställa den senare. Ange gärna ett skäl.",
  });
  if (kommentar !== null) reviewAction("avvisa", { kommentar }, "Artikeln är avvisad");
});
$("#regenBtn").addEventListener("click", async () => {
  const rec = state.current;
  if (rec.har_innehall && !confirm(rec.andringar ? "AI:n skriver om artikeln utifrån dina begärda ändringar. Fortsätta?" : "Den nuvarande texten ersätts av en ny version. Fortsätta?")) return;
  try {
    await api(`/api/artiklar/${rec.id}/generera`, {});
    await openArticle(rec.id, { keepView: true });
    loadQueue();
  } catch (err) { toast(err.message); }
});

/* ---------- Start ---------- */
loadStatus();
(async () => {
  await loadQueue();
  const senaste = Number(recall("kh-studio-artikel"));
  if (senaste && state.queue.some((a) => a.id === senaste)) await openArticle(senaste, { keepView: true });
  showView(recall(VIEW_KEY) === "editor" ? "editor" : "queue");
})();
