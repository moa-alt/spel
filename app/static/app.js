"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const STORAGE_KEY = "kh-studio-utkast";
const state = { article: null, status: {}, sekundara: [], editing: false };

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
function save() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.article)); } catch { /* privat läge */ }
}
async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Fel ${res.status}`);
  return data;
}

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
function renderChips() {
  $("#chips").innerHTML = state.sekundara
    .map((s, i) => `<span class="chip">${esc(s)}<button type="button" data-i="${i}" aria-label="Ta bort ${esc(s)}">×</button></span>`)
    .join("");
}
function addChip(value) {
  value.split(",").map((v) => v.trim()).filter(Boolean).forEach((v) => {
    if (!state.sekundara.some((s) => s.toLowerCase() === v.toLowerCase())) state.sekundara.push(v);
  });
  renderChips();
}
$("#chipInput").addEventListener("keydown", (e) => {
  const input = e.target;
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    addChip(input.value);
    input.value = "";
  } else if (e.key === "Backspace" && !input.value && state.sekundara.length) {
    state.sekundara.pop();
    renderChips();
  }
});
$("#chipInput").addEventListener("blur", (e) => { if (e.target.value.trim()) { addChip(e.target.value); e.target.value = ""; } });
$("#chips").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (!b) return;
  state.sekundara.splice(Number(b.dataset.i), 1);
  renderChips();
});
$("#chipsBox").addEventListener("click", (e) => { if (e.target.id === "chipsBox" || e.target.id === "chips") $("#chipInput").focus(); });

/* ---------- Generering ---------- */
const STEG_TEXT = ["Analyserar sökintention", "Skriver artikeln", "Skapar 4 bilder (WebP)", "Optimerar metadata"];

function setStep(index) {
  $$("#progress li").forEach((li, i) => {
    if (li.classList.contains("skipped")) return;
    li.classList.toggle("done", i < index);
    li.classList.toggle("active", i === index);
  });
}

// Läser NDJSON-strömmen från servern: en händelse per rad.
async function* readEvents(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) yield JSON.parse(line);
    }
  }
  if (buf.trim()) yield JSON.parse(buf);
}

$("#brief").addEventListener("submit", async (e) => {
  e.preventDefault();
  const huvudsokord = $("#huvudsokord").value.trim();
  if ($("#chipInput").value.trim()) { addChip($("#chipInput").value); $("#chipInput").value = ""; }
  if (huvudsokord.length < 2) return;

  if (state.article && !confirm("Den nuvarande artikeln ersätts. Fortsätta?")) return;

  const btn = $("#genBtn");
  btn.disabled = true;
  btn.classList.add("loading");
  $("#progress").hidden = false;
  $$("#progress li").forEach((li, i) => {
    li.className = "";
    li.textContent = STEG_TEXT[i];
  });
  // Bilderna kopplas in i steg 3 av utvecklingen.
  const bildSteg = $('#progress li[data-step="2"]');
  if (!state.status.bilder) { bildSteg.classList.add("skipped"); bildSteg.textContent = "Bilder – kopplas in senare"; }
  setStep(0);
  const started = Date.now();
  const timer = setInterval(() => {
    btn.lastChild.textContent = ` Genererar… ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);

  try {
    const res = await fetch("/api/generera", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ huvudsokord, sekundara_sokord: state.sekundara }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(Array.isArray(data.detail) ? "Kontrollera sökorden" : data.detail || `Fel ${res.status}`);
    }
    let article = null;
    for await (const ev of readEvents(res)) {
      if (ev.typ === "steg") setStep(ev.steg);
      else if (ev.typ === "framsteg") $('#progress li[data-step="1"]').textContent = `Skriver artikeln… ${ev.tecken.toLocaleString("sv-SE")} tecken`;
      else if (ev.typ === "fel") throw new Error(ev.meddelande);
      else if (ev.typ === "klar") article = ev.artikel;
    }
    if (!article) throw new Error("Anslutningen bröts innan artikeln var klar. Försök igen.");
    $$("#progress li:not(.skipped)").forEach((li) => { li.classList.remove("active"); li.classList.add("done"); });
    $('#progress li[data-step="1"]').textContent = STEG_TEXT[1];
    setArticle(article);
    toast(article.demo ? "Demotext – lägg in ANTHROPIC_API_KEY för AI-text" : "Artikeln är klar – granska och redigera");
  } catch (err) {
    toast(err.message || "Något gick fel");
    $$("#progress li.active").forEach((li) => li.classList.replace("active", "failed"));
  } finally {
    clearInterval(timer);
    btn.lastChild.textContent = " Generera artikel";
    btn.disabled = false;
    btn.classList.remove("loading");
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
  $("#huvudsokord").value = article.huvudsokord || "";
  state.sekundara = [...(article.sekundara_sokord || [])];
  renderChips();
  renderKeywords();
  renderIntent();
  renderImages();
  renderSeo();
  renderPreview();
  save();
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

/* ---------- Start ---------- */
loadStatus();
try {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  if (saved && saved.html) setArticle(saved);
} catch { /* inget sparat utkast */ }
