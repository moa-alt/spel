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
// En schemalagd artikel kan redigeras; då tas den ur kalendern och måste godkännas igen.
const REDIGERBAR = ["utkast", "granskas", "godkand", "schemalagd"];

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
function formatDag(iso) {
  return iso ? new Date(iso).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}
function formatTid(iso) {
  return iso ? new Date(iso).toLocaleString("sv-SE", { dateStyle: "medium", timeStyle: "short" }) : "";
}
async function api(path, body, method) {
  const m = method || (body !== undefined ? "POST" : "GET");
  const headers = { "X-Studio": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method: m, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (res.status === 401) { location.href = "/login"; throw new Error("Du är utloggad"); }
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
  wp.title = state.status.wordpress ? "Skapar eller uppdaterar ett utkast i WordPress. Publicerar aldrig." : "WordPress är inte konfigurerat på servern";
  $("#logoutBtn").hidden = !state.status.inloggning;
}
$("#logoutBtn").addEventListener("click", async () => {
  try { await api("/api/logout", {}); } finally { location.href = "/login"; }
});

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
const STEG_TEXT = ["Analyserar sökintention", "Skriver artikeln", "Planerar 4 bildmotiv", "Optimerar metadata"];

function resetProgress() {
  $$("#progress li").forEach((li, i) => { li.className = ""; li.textContent = STEG_TEXT[i]; });
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
  requestAnimationFrame(fitTitle);
  $("#meta_titel").value = article.meta_titel;
  $("#meta_beskrivning").value = article.meta_beskrivning;
  $("#slug").value = article.slug;
  const last = !!state.current?.mal_slug;
  $("#slug").readOnly = last;
  $("#slugLock").hidden = !last;
  $("#htmlEditor").value = article.html;
  renderKeywords();
  renderIntent();
  renderLinks();
  renderImages();
  renderSeo();
  renderPreview();
}

const INTENT_NAMN = { informativ: "Informativ", kommersiell: "Kommersiell", transaktionell: "Transaktionell", navigerande: "Navigerande" };

function renderLinks() {
  const rec = state.current;
  const kontroll = rec?.lankkontroll || [];
  const plan = rec?.lankplan || { lanka_fran: [], lanka_till: [] };
  $("#linksCard").hidden = !kontroll.length && !plan.lanka_fran.length;
  $("#linksList").innerHTML = kontroll.map((l) => `<li class="${l.finns === false ? "bad" : ""}">
      <span>${esc(l.ankartext || "(ingen ankartext)")} → <a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.url.replace(/^https?:\/\/[^/]+/, ""))}</a>
      ${l.finns === false ? " – sidan finns inte i kartan" : ""}</span></li>`).join("") || `<li class="warn">Inga internlänkar ännu</li>`;
  $("#linksPlan").innerHTML = plan.lanka_fran.length
    ? `<strong>Föreslagna inkommande länkar</strong> <span class="tag tag-ai">AI-förslag</span>
       <p class="muted">Befintliga artiklar som kan länka hit. Inga publicerade artiklar ändras utan ditt godkännande.</p>
       <ul>${plan.lanka_fran.map((u) => `<li><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\/[^/]+/, ""))}</a></li>`).join("")}</ul>`
    : "";
  const inn = rec?.inkommande_forslag || [];
  if (inn.length) $("#linksCard").hidden = false;
  $("#inForslag").innerHTML = inn.length
    ? `<strong>Befintliga artiklar som kan länka hit</strong>
       <p class="muted">${inn.some((f) => f.status === "vantar_pa_publicering") ? "Förslagen kan godkännas i SEO-kartan när artikeln är publicerad." : "Godkänn förslagen under SEO-karta → Länkförslag."} Publicerade artiklar ändras bara med ett separat godkännande.</p>
       <ul class="rek">${inn.map((f) => `<li class="beraknad"><b>${esc(f.kalla_titel)}</b>: ”${esc(f.ankartext)}” <span class="muted">(${LANK_STATUS[f.status] || f.status})</span></li>`).join("")}</ul>`
    : "";
}

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
  // Förhandsvisningen är ett srcdoc-dokument; studions bildadresser görs därför absoluta.
  const src = b.url.startsWith("/") ? location.origin + b.url : b.url;
  return `<figure class="kh-bild" data-bild="${b.id}" contenteditable="false">
    <img src="${esc(src)}" alt="${esc(b.alt)}"${b.bredd ? ` width="${b.bredd}" height="${b.hojd}"` : ""}${b.id === 1 ? "" : ' loading="lazy"'}>
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
  html { overflow: hidden; }
  .kh-h1 { max-width: 760px; margin: 0 auto 24px; font-family: Fraunces, Georgia, serif; font-weight: 500; font-size: 2.4em; line-height: 1.12; color: #193D2D; letter-spacing: -.01em; }
  .kh-platshallare .kh-ph { aspect-ratio: 3 / 2; border-radius: 12px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
    background: repeating-linear-gradient(45deg, #EEF4EF, #EEF4EF 12px, #DCE9DF 12px, #DCE9DF 24px); color: #193D2D; border: 1px dashed #B9CDBF; text-align: center; padding: 16px; }
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
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500&family=Inter:wght@400;600;700&display=swap">
    <link rel="stylesheet" href="${location.origin}/static/artikel.css">
    <style>${PREVIEW_BASE_CSS}</style></head>
    <body><h1 class="kh-h1">${esc($("#titel").value)}</h1>
    <div class="kh-artikel" id="artikel"${editable}>${articleBodyForPreview()}</div></body></html>`;
  frame.onload = () => {
    frame.contentWindow.scrollTo(0, prevScroll);
    const doc = frame.contentDocument;
    fitFrame();
    new ResizeObserver(fitFrame).observe(doc.body);
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

// Låter iframen bli lika hög som artikeln så att sidan scrollar som en vanlig webbsida.
function fitFrame() {
  const frame = $("#previewFrame");
  const body = frame.contentDocument?.body;
  if (body) frame.style.height = `${Math.ceil(body.parentElement.scrollHeight)}px`;
}

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
// Rubriken är en textarea så att den kan radbrytas på mobil; Enter ger ingen ny rad.
function fitTitle() {
  const t = $("#titel");
  t.style.height = "auto";
  t.style.height = `${t.scrollHeight}px`;
}
$("#titel").addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
window.addEventListener("resize", debounce(fitTitle, 150));
$("#titel").addEventListener("input", (e) => {
  e.target.value = e.target.value.replace(/\s*\n\s*/g, " ");
  fitTitle();
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
  const klaraBilder = (a.bilder || []).filter((b) => b.fil).length;
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
    [klaraBilder >= 4 ? "ok" : "warn", `${klaraBilder} av 4 bilder klara`],
    [ord >= 800 ? "ok" : "warn", `Textlängd ${ord} ord (rek. 800+)`],
  ];
  const lk = state.current?.lankkontroll;
  if (lk && state.current.innehall?.html === a.html) {
    const trasiga = lk.filter((l) => l.finns === false).length;
    checks.push([lk.length >= 3 && lk.length <= 6 ? "ok" : "warn", `${lk.length} internlänkar (rek. 3–6)`]);
    if (trasiga) checks.push(["bad", `${trasiga} länk${trasiga === 1 ? "" : "ar"} till sidor som inte finns`]);
  }
  const okCount = checks.filter(([s]) => s === "ok").length;
  $("#score").textContent = `${Math.round((okCount / checks.length) * 100)}%`;
  $("#checks").innerHTML = checks.map(([s, t]) => `<li class="${s === "ok" ? "" : s}">${esc(t)}</li>`).join("");
}

/* ---------- Bilder ---------- */
const BILD_STATUSAR = ["utkast", "granskas"];
const KALLA_NAMN = { ai: "AI-bild", manuell: "Egen bild", test: "Testbild" };
const bild = { timer: null, valda: [], byt: null, vantande: false };

function kanAndraBilder() {
  return !!state.current && BILD_STATUSAR.includes(state.current.status) && !state.current.bilder_pagar;
}
function bildStatus(b) {
  if (b.status === "genereras") return ["tag-ai", "Skapas…"];
  if (b.fel && !b.fil) return ["tag-bad", "Misslyckades"];
  if (!b.fil) return ["tag-warn", "Saknas"];
  if (b.godkand) return ["tag-ok", "Godkänd"];
  return ["tag-ok", KALLA_NAMN[b.kalla] || "Klar"];
}

function renderImages() {
  const info = state.current?.bildinfo || {};
  const kan = kanAndraBilder();
  const tjanst = !!state.status.bilder;
  $("#images").innerHTML = state.article.bilder.map((b, i) => {
    const inf = info[b.id] || {};
    const [cls, statusText] = bildStatus(b);
    const skapas = b.status === "genereras";
    return `
    <div class="img-card${skapas ? " busy" : ""}" data-id="${b.id}">
      <div class="img-thumb"><span class="num">Bild ${b.id}${inf.roll === "huvudbild" ? " · Huvudbild" : ""}</span>
        ${b.url ? `<img src="${esc(b.url)}" alt="${esc(b.alt)}" width="${b.bredd || 1536}" height="${b.hojd || 1024}">` : `<span>${skapas ? "" : "Ingen bild ännu"}</span>`}
        ${skapas ? `<div class="img-busy"><span class="spinner" aria-hidden="true"></span>Skapar bilden…</div>` : ""}
      </div>
      <div class="img-body">
        <div class="img-meta"><span class="tag ${cls}">${statusText}</span><span class="muted">${esc(inf.avsnitt || "")}</span></div>
        ${b.fel ? `<p class="img-fel" role="alert">${esc(b.fel)}${b.fil ? " Den tidigare bilden finns kvar." : ""}</p>` : ""}
        ${b.inaktuell ? `<p class="img-hint">Texten har genererats om efter att bilden skapades. Motivet är uppdaterat; generera om bilden om den inte längre passar.</p>` : ""}
        <label class="field"><span class="label">Alt-text <span class="counter${b.alt.length > 125 ? " warn" : ""}">${b.alt.length} tecken</span></span>
          <input type="text" data-i="${i}" data-f="alt" value="${esc(b.alt)}" maxlength="1000"></label>
        ${inf.alt_varning ? `<p class="img-hint">${esc(inf.alt_varning)}</p>` : ""}
        <label class="field"><span class="label">Bildtext <small>valfri</small></span>
          <input type="text" data-i="${i}" data-f="bildtext" value="${esc(b.bildtext)}" maxlength="1000"></label>
        <label class="field"><span class="label">Motiv till bildtjänsten <small>stil och förbud mot text läggs till automatiskt</small></span>
          <textarea rows="3" data-i="${i}" data-f="prompt" maxlength="2000">${esc(b.prompt)}</textarea></label>
        ${b.fil ? `<p class="muted img-file"><code>${esc(inf.filnamn || "")}</code> · ${b.bredd}×${b.hojd} · ${b.storlek_kb} kB</p>` : ""}
        <label class="img-lock"><input type="checkbox" data-godkand="${b.id}" ${b.godkand ? "checked" : ""} ${kan && b.fil && !skapas ? "" : "disabled"}>
          <span>Godkänd – genereras aldrig om automatiskt</span></label>
        <div class="img-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-bild-act="skapa" data-id="${b.id}" ${kan && tjanst && !b.godkand && !skapas ? "" : "disabled"}>${b.fil ? "Generera om" : "Skapa bild"}</button>
          <button class="btn btn-ghost btn-sm" type="button" data-bild-act="byt" data-id="${b.id}" ${kan && !b.godkand && !skapas ? "" : "disabled"}>Byt bild…</button>
        </div>
      </div>
    </div>`;
  }).join("");
  renderBildHuvud();
}

function bildKandidater() {
  const ej = state.article.bilder.filter((b) => !b.godkand && b.status !== "genereras");
  const saknas = ej.filter((b) => !b.fil);
  return saknas.length ? { ids: saknas.map((b) => b.id), text: `Skapa ${saknas.length === 4 ? "alla 4" : saknas.length} bilder` }
    : { ids: ej.map((b) => b.id), text: ej.length ? `Generera om ej godkända (${ej.length})` : "Alla bilder är godkända" };
}

function renderBildHuvud() {
  const s = state.current?.status;
  const k = bildKandidater();
  const btn = $("#imgGenAll");
  btn.textContent = state.current?.bilder_pagar ? "Bilder skapas…" : k.text;
  btn.disabled = !kanAndraBilder() || !state.status.bilder || !k.ids.length;
  let text = "";
  if (state.current && !BILD_STATUSAR.includes(s)) {
    text = s === "godkand" || s === "schemalagd" ? "Bilderna är låsta eftersom artikeln är godkänd. Återkalla godkännandet om du vill ändra dem." : "Bilderna kan inte ändras i artikelns nuvarande status.";
  } else if (!state.status.bilder) {
    text = "Ingen bildtjänst är konfigurerad på servern (OPENAI_API_KEY). Du kan fortfarande byta bilder manuellt.";
  } else if (state.current?.bilder_pagar) {
    text = "Bilderna skapas på servern. Varje bild sparas så fort den är klar, även om du stänger fönstret.";
  }
  $("#imgNotice").hidden = !text;
  $("#imgNotice").textContent = text;
}

$("#images").addEventListener("input", (e) => {
  const el = e.target.closest("[data-f]");
  if (!el) return;
  state.article.bilder[Number(el.dataset.i)][el.dataset.f] = el.value;
  if (el.dataset.f === "alt") {
    const c = el.closest(".field").querySelector(".counter");
    c.textContent = `${el.value.length} tecken`;
    c.classList.toggle("warn", el.value.length > 125);
  }
  renderChecks(); renderPreviewSoon(); save();
});
// Uppdateringar från servern väntar tills du har lämnat fältet du skriver i.
const skriverIBild = () => !!document.activeElement?.matches("#images input[type=text], #images textarea");
$("#images").addEventListener("focusout", () => setTimeout(() => {
  if (bild.vantande && !skriverIBild()) { bild.vantande = false; renderImages(); }
}, 0));

$("#images").addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-godkand]");
  if (!cb) return;
  try {
    await api(`/api/artiklar/${state.current.id}/bilder/${cb.dataset.godkand}/godkann`, { godkand: cb.checked });
    toast(cb.checked ? "Bilden är godkänd och låst" : "Bilden är upplåst");
    await uppdateraBilder();
  } catch (err) { cb.checked = !cb.checked; toast(err.message); }
});

$("#images").addEventListener("click", (e) => {
  const b = e.target.closest("[data-bild-act]");
  if (!b) return;
  if (b.dataset.bildAct === "skapa") bestallBilder([Number(b.dataset.id)]);
  if (b.dataset.bildAct === "byt") { bild.byt = Number(b.dataset.id); $("#imgFile").value = ""; $("#imgFile").click(); }
});
$("#imgGenAll").addEventListener("click", () => bestallBilder(bildKandidater().ids));

async function bestallBilder(ids) {
  if (!ids.length) return;
  let k;
  try { k = await api(`/api/bilder/kostnad?antal=${ids.length}`); } catch (err) { toast(err.message); return; }
  bild.valda = ids;
  const kr = k.total_sek.toLocaleString("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  $("#imgCostTitle").textContent = ids.length === 1 ? `Skapa bild ${ids[0]}` : `Skapa ${ids.length} bilder`;
  $("#imgCostText").textContent = ids.some((id) => state.article.bilder.find((b) => b.id === id)?.fil)
    ? "Nya bilder ersätter de nuvarande när de är klara. Misslyckas en bild finns den tidigare kvar."
    : "Bilderna skapas på servern och sparas som optimerad WebP så fort de är klara.";
  $("#imgCostAntal").textContent = `${ids.length} st (bild ${ids.join(", ")})`;
  $("#imgCostModell").textContent = `${k.modell}, ${k.kvalitet}, ${k.storlek}`;
  $("#imgCostSumma").textContent = k.total_usd ? `ca ${k.total_usd.toFixed(3)} USD (≈ ${kr} kr)` : "0 kr (testbilder)";
  $("#imgCostConfirm").checked = false;
  $("#imgCostOk").disabled = true;
  $("#imgCostDialog").showModal();
}
$("#imgCostConfirm").addEventListener("change", (e) => { $("#imgCostOk").disabled = !e.target.checked; });
$("#imgCostDialog").addEventListener("close", async () => {
  if ($("#imgCostDialog").returnValue !== "ok" || !$("#imgCostConfirm").checked) return;
  try {
    const r = await api(`/api/artiklar/${state.current.id}/bilder/generera`, { bild_ids: bild.valda, bekraftad: true });
    toast(`${r.startade.length === 1 ? "Bilden" : `${r.startade.length} bilder`} skapas nu`);
    await uppdateraBilder();
  } catch (err) { toast(err.message); }
});

$("#imgFile").addEventListener("change", async (e) => {
  const fil = e.target.files[0];
  if (!fil || !bild.byt) return;
  if (fil.size > 15 * 1024 * 1024) { toast("Bilden är för stor (max 15 MB)"); return; }
  try {
    const res = await fetch(`/api/artiklar/${state.current.id}/bilder/${bild.byt}/ladda-upp`, {
      method: "POST", headers: { "X-Studio": "1", "Content-Type": fil.type || "application/octet-stream" }, body: fil,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fel ${res.status}`);
    toast(`Bild ${bild.byt} är bytt och sparad som WebP`);
    await uppdateraBilder();
  } catch (err) { toast(err.message); }
});

// Hämtar bildernas status från servern utan att skriva över texter du håller på att redigera.
async function uppdateraBilder() {
  const id = state.current?.id;
  if (!id || !state.article) return;
  let rec;
  try { rec = await api(`/api/artiklar/${id}`); } catch { return; }
  if (state.current?.id !== id || !rec.innehall || !state.article) return;
  const lokala = Object.fromEntries(state.article.bilder.map((b) => [b.id, b]));
  state.article.bilder = rec.innehall.bilder.map((b) => {
    const l = lokala[b.id];
    return l ? { ...b, alt: l.alt, bildtext: l.bildtext, prompt: l.prompt } : b;
  });
  Object.assign(state.current, { bildinfo: rec.bildinfo, bilder_pagar: rec.bilder_pagar, status: rec.status, historik: rec.historik });
  if (skriverIBild()) bild.vantande = true;
  else renderImages();
  renderChecks(); renderPreview(); renderHistory();
  clearTimeout(bild.timer);
  if (rec.bilder_pagar || rec.innehall.bilder.some((b) => b.status === "genereras")) bild.timer = setTimeout(uppdateraBilder, 2000);
}

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
  if (!state.current) return;
  if (!confirm("Skapa eller uppdatera ett utkast i WordPress? Inget publiceras.")) return;
  try {
    const res = await api(`/api/artiklar/${state.current.id}/wp-utkast`, {});
    toast(`Utkastet är sparat i WordPress (inlägg ${res.wp_post_id})`);
    await openArticle(state.current.id, { keepView: true });
  } catch (err) { toast(err.message); }
});

/* ---------- Vyer ---------- */
function showView(view) {
  if (document.body.dataset.view !== view) window.scrollTo(0, 0);
  document.body.dataset.view = view;
  $("#queueView").hidden = view !== "queue";
  $("#editorView").hidden = view !== "editor";
  $("#pubView").hidden = view !== "pub";
  $("#seoView").hidden = view !== "seo";
  $$(".view-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  remember(VIEW_KEY, view);
  if (view === "queue") loadQueue();
  if (view === "pub") loadPub();
  if (view === "seo") loadSeo();
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
    if (a.mal_slug) notes.push(`<div class="q-note info">Ska publiceras på /${esc(a.mal_slug)}/ – befintliga artiklar länkar redan hit</div>`);
    if (a.status === "schemalagd" && a.publiceras_at) notes.push(`<div class="q-note info">Publiceras ${formatDag(a.publiceras_at)}</div>`);
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
    const res = await fetch("/api/artiklar/import", { method: "POST", headers: { "Content-Type": "text/csv", "X-Studio": "1" }, body: await file.arrayBuffer() });
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
  if (!keepView || state.current?.id !== id) window.scrollTo(0, 0);
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
  clearTimeout(bild.timer);
  if (rec.bilder_pagar || (rec.innehall?.bilder || []).some((b) => b.status === "genereras")) bild.timer = setTimeout(uppdateraBilder, 2000);
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
    rejectBtn: ["utkast", "granskas", "godkand", "schemalagd", "fel"].includes(s),
    changesBtn: ["granskas", "godkand", "schemalagd"].includes(s),
    revokeBtn: ["godkand", "schemalagd"].includes(s),
    approveBtn: s === "granskas",
  };
  Object.entries(visa).forEach(([id, v]) => { $("#" + id).hidden = !v; });
  $("#regenBtn").textContent = rec.andringar ? "Generera om med ändringar" : "Generera om text";

  const notice = $("#notice");
  let text = "", cls = "";
  if (s === "fel") { text = `Genereringen misslyckades: ${rec.fel}`; cls = "bad"; }
  else if (rec.andringar) text = `Begärda ändringar: ”${rec.andringar}”. Redigera texten och skicka till granskning, eller generera om med ändringarna.`;
  else if (s === "godkand") { text = `Godkänd ${formatTid(rec.godkand_at)}. Om du ändrar något måste artikeln godkännas igen.`; cls = "ok"; }
  else if (s === "schemalagd") { text = `Schemalagd: publiceras ${formatDag(rec.publiceras_at)}. Om du ändrar något tas den ur kalendern och måste godkännas igen.`; cls = "ok"; }
  else if (s === "publicerad") { text = "Publicerad på klimathemmet.se. Ändringar görs i WordPress."; cls = "ok"; }
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
  schemalagd: "Schemalagd", omschemalagd: "Nytt datum", avbokad: "Borttagen ur kalendern", publicerad: "Publicerad",
  publicering_simulerad: "Simulerad publicering", wp_utkast_skapat: "Utkast i WordPress", publicering_stoppad: "Publicering stoppad",
  publicering_misslyckad: "Publicering misslyckades", publicering_forsok: "Nytt försök planerat", publicering_missad: "Publiceringsdag missad",
  publicering_pausad: "Publicering pausad", publicering_aterupptagen: "Publicering återupptagen",
  genererad: "Text genererad", fran_seo_karta: "Från SEO-kartan",
  lank_tillagd: "Länk tillagd", lank_simulerad: "Länk simulerad", lank_aterstalld: "Länkar återställda",
  fran_saknad_url: "Skapad för saknad adress", mal_publicerad: "Publicerad på planerad adress", mal_fel_adress: "Publicerad på fel adress", skickad_till_granskning: "Skickad till granskning", godkand: "Godkänd",
  andringar_begarda: "Ändringar begärda", avvisad: "Avvisad", aterstalld: "Återställd",
  godkannande_aterkallat: "Godkännande återkallat", godkannande_upphavt: "Godkännande upphävt",
  bilder_bestallda: "Bilder beställda", bild_genererad: "Bild skapad", bild_misslyckad: "Bild misslyckades",
  bild_bytt: "Bild bytt manuellt", bild_godkand: "Bild godkänd", bild_upplast: "Bild upplåst",
  bild_uppladdad: "Bild uppladdad till WordPress",
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

// Godkännande är alltid ett medvetet steg: sammanfattning och en kryssruta som måste bockas i.
$("#approveBtn").addEventListener("click", () => {
  const a = state.article;
  const ok = $$("#checks li:not(.warn):not(.bad)").length, totalt = $$("#checks li").length;
  const saknarAlt = (a.bilder || []).filter((b) => b.alt.trim().length <= 5).length;
  const rader = [
    ["Rubrik", a.titel],
    ["Metatitel", `${a.meta_titel.length} tecken`],
    ["Metabeskrivning", `${a.meta_beskrivning.length} tecken`],
    ["URL", `/${a.slug}`],
    ["SEO-kontroll", `${ok} av ${totalt} uppfyllda`],
    ["Bilder utan alt-text", String(saknarAlt)],
    ["Färdiga bilder", `${(a.bilder || []).filter((b) => b.fil).length} av 4 (godkänns tillsammans med artikeln)`],
    ["Längd", `${wordCount().toLocaleString("sv-SE")} ord`],
  ];
  $("#approveSummary").innerHTML = rader.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("#approveConfirm").checked = false;
  $("#approveOk").disabled = true;
  $("#approveDialog").showModal();
});
$("#approveConfirm").addEventListener("change", (e) => { $("#approveOk").disabled = !e.target.checked; });
$("#approveDialog").addEventListener("close", () => {
  if ($("#approveDialog").returnValue === "ok" && $("#approveConfirm").checked) {
    reviewAction("godkann", { bekraftad: true }, "Artikeln är godkänd");
  }
});
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

/* ---------- Publicering ---------- */
const SLOT_NAMN = {
  planerad: "Schemalagd", publicerar: "Publiceras nu", publicerad: "Publicerad", utkast: "Utkast i WordPress",
  simulerad: "Simulerad", missad: "Missad", fel: "Fel",
};
const LAGE_TEXT = {
  simulering: ["Simuleringsläge", "Inget skickas till klimathemmet.se. Schemaläggaren kör alla kontroller och loggar vad som skulle ha hänt."],
  utkast: ["Utkastläge", "På publiceringsdagen skapas ett utkast i WordPress. Inget publiceras."],
  skarp: ["Skarp publicering", "Godkända artiklar publiceras automatiskt på klimathemmet.se på sina datum."],
};
const pub = { data: null, manad: null };

async function loadPub() {
  try { pub.data = await api("/api/publicering"); } catch (err) { toast(err.message); return; }
  const d = pub.data;
  if (!pub.manad) { const t = new Date(d.idag); pub.manad = new Date(t.getFullYear(), t.getMonth(), 1); }
  const [rubrik, text] = LAGE_TEXT[d.lage];
  const banner = $("#modeBanner");
  banner.className = `mode-banner ${d.pausad ? "pausad" : d.lage}`;
  banner.innerHTML = d.pausad
    ? `<strong>Publiceringen är pausad</strong>Inget publiceras förrän du återupptar den. Läge: ${esc(rubrik.toLowerCase())}.`
    : `<strong>${esc(rubrik)}</strong>${esc(text)}`;
  $("#pauseToggle").checked = d.pausad;
  $("#pauseText").textContent = d.pausad ? "Publiceringen är pausad" : "Pausa all publicering";
  const n = d.godkanda.length;
  $("#approvedText").textContent = n
    ? `${n} godkänd${n === 1 ? "" : "a"} artikel${n === 1 ? "" : "ar"} väntar på datum. De läggs på första lediga dag, en per dag, i köns ordning.`
    : "Inga godkända artiklar väntar på datum. Godkänn artiklar i artikelkön först.";
  $("#scheduleBtn").disabled = !n;
  $("#scheduleBtn").textContent = n ? `Schemalägg ${n} godkänd${n === 1 ? "" : "a"}` : "Schemalägg godkända";
  if (!$("#startDate").value) {
    const t = new Date(d.idag); t.setDate(t.getDate() + 1);
    $("#startDate").value = t.toISOString().slice(0, 10);
  }
  $("#startDate").min = d.idag;
  $("#wpText").textContent = d.wordpress
    ? "Kontrollera inloggning, behörighet, SEO-plugin och tilläggspluginet. Ingenting ändras på sajten."
    : "WordPress är inte konfigurerat på servern ännu (WP_URL, WP_USER och WP_APP_PASSWORD).";
  $("#wpCheckBtn").disabled = !d.wordpress;
  renderCalendar();
  renderPubList();
  $("#pubLog").innerHTML = d.logg.length ? d.logg.map((h) => `<li><strong>${esc(HANDLING_NAMN[h.handling] || h.handling)}${h.huvudsokord ? ` · ${esc(h.huvudsokord)}` : ""}</strong>
    ${h.kommentar ? `<q>${esc(h.kommentar)}</q>` : ""}<time>${formatTid(h.tid)}</time></li>`).join("") : `<li class="pub-empty">Inga händelser ännu.</li>`;
}

function renderCalendar() {
  const m = pub.manad;
  $("#calTitle").textContent = m.toLocaleDateString("sv-SE", { month: "long", year: "numeric" });
  const perDag = {};
  pub.data.kalender.forEach((p) => { (perDag[p.datum] ||= []).push(p); });
  const forsta = new Date(m);
  forsta.setDate(1 - ((m.getDay() + 6) % 7)); // måndag före eller på den första
  const celler = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"].map((d) => `<div class="cal-dow">${d}</div>`);
  for (let i = 0; i < 42; i++) {
    const dag = new Date(forsta); dag.setDate(forsta.getDate() + i);
    const iso = `${dag.getFullYear()}-${String(dag.getMonth() + 1).padStart(2, "0")}-${String(dag.getDate()).padStart(2, "0")}`;
    const ev = (perDag[iso] || []).map((p) => `<span class="cal-ev ${p.status}" title="${esc(p.huvudsokord)} – ${SLOT_NAMN[p.status]}">${p.lokal_tid.slice(11, 16)} <span class="t">${esc(p.huvudsokord)}</span></span>`).join("");
    celler.push(`<div class="cal-day${dag.getMonth() !== m.getMonth() ? " other" : ""}${iso === pub.data.idag ? " today" : ""}"><span class="n">${dag.getDate()}</span>${ev}</div>`);
  }
  $("#calendar").innerHTML = celler.join("");
}

function renderPubList() {
  const lista = [...pub.data.kalender].sort((a, b) => a.datum.localeCompare(b.datum));
  const kommande = lista.filter((p) => p.datum >= pub.data.idag || ["planerad", "fel", "missad"].includes(p.status));
  $("#pubList").innerHTML = kommande.length ? kommande.map((p) => `
    <li class="pub-item">
      <div>
        <div class="kw">${esc(p.titel || p.huvudsokord)}</div>
        <div class="when">${formatDag(p.tid_utc)} · ${statusBadgeSlot(p.status)}</div>
        ${p.fel && p.status !== "planerad" ? `<div class="err">${esc(p.fel)}</div>` : ""}
        ${p.fel && p.status === "planerad" ? `<div class="err">Senaste försöket misslyckades: ${esc(p.fel)}</div>` : ""}
        ${p.wp_lank ? `<a href="${esc(p.wp_lank)}" target="_blank" rel="noopener">Visa på sajten</a>` : ""}
      </div>
      ${p.status === "planerad" ? `<div class="pub-item-actions">
        <input type="date" value="${p.datum}" min="${pub.data.idag}" data-flytta="${p.id}" aria-label="Nytt datum för ${esc(p.huvudsokord)}">
        <button class="btn btn-ghost btn-sm btn-danger" type="button" data-avboka="${p.artikel_id}">Ta bort</button>
      </div>` : ""}
    </li>`).join("") : `<li class="pub-empty">Inget är schemalagt.</li>`;
}
function statusBadgeSlot(s) {
  const klass = { planerad: "schemalagd", publicerar: "genereras", publicerad: "publicerad", utkast: "godkand", simulerad: "godkand", missad: "fel", fel: "fel" }[s];
  return `<span class="status-badge s-${klass}">${SLOT_NAMN[s] || s}</span>`;
}

$("#calPrev").addEventListener("click", () => { pub.manad.setMonth(pub.manad.getMonth() - 1); renderCalendar(); });
$("#calNext").addEventListener("click", () => { pub.manad.setMonth(pub.manad.getMonth() + 1); renderCalendar(); });

$("#scheduleForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const n = pub.data.godkanda.length;
  if (!confirm(`Schemalägg ${n} godkänd${n === 1 ? "" : "a"} artikel${n === 1 ? "" : "ar"} från ${$("#startDate").value} kl. ${$("#startTime").value}?`)) return;
  try {
    const res = await api("/api/publicering/schemalagg", { startdatum: $("#startDate").value, klockslag: $("#startTime").value });
    const k = res.schemalagda.length;
    toast(`${k} ${k === 1 ? "artikel schemalagd" : "artiklar schemalagda"}`);
    await loadPub();
  } catch (err) { toast(err.message); }
});

$("#pauseToggle").addEventListener("change", async (e) => {
  try {
    await api("/api/publicering/paus", { pausad: e.target.checked });
    toast(e.target.checked ? "Publiceringen är pausad" : "Publiceringen är återupptagen");
  } catch (err) { toast(err.message); }
  loadPub();
});

$("#pubList").addEventListener("change", async (e) => {
  const id = e.target.dataset.flytta;
  if (!id) return;
  try {
    const res = await api(`/api/publicering/${id}/flytta`, { datum: e.target.value });
    toast(`Flyttad till ${formatDag(res.lokal_tid)}`);
  } catch (err) { toast(err.message); }
  loadPub();
});
$("#pubList").addEventListener("click", async (e) => {
  const id = e.target.dataset.avboka;
  if (!id || !confirm("Ta bort artikeln ur kalendern? Den förblir godkänd och kan schemaläggas igen.")) return;
  try { await api(`/api/artiklar/${id}/avboka`, {}); toast("Borttagen ur kalendern"); } catch (err) { toast(err.message); }
  loadPub();
});

$("#wpCheckBtn").addEventListener("click", async () => {
  const btn = $("#wpCheckBtn"), ut = $("#wpResult");
  btn.disabled = true;
  try {
    const r = await api("/api/wordpress/kontrollera", {});
    const rader = [
      ["", `Inloggad som ${r.anvandare}`],
      [r.kan_publicera ? "" : "bad", r.kan_publicera ? "Har behörighet att publicera" : "Saknar behörighet att publicera"],
      [r.seo_plugin.length ? "" : "warn", r.seo_plugin.length ? `SEO-plugin: ${r.seo_plugin.join(", ")}` : "Inget SEO-plugin hittades"],
      [r.studio_plugin ? "" : "bad", r.studio_plugin ? "Tilläggspluginet är installerat" : "Tilläggspluginet saknas"],
      ...r.problem.map((p) => ["warn", p]),
    ];
    ut.innerHTML = rader.map(([k, t]) => `<li class="${k}">${esc(t)}</li>`).join("");
  } catch (err) {
    ut.innerHTML = `<li class="bad">${esc(err.message)}</li>`;
  } finally {
    ut.hidden = false;
    btn.disabled = false;
  }
});

/* ---------- SEO-karta ---------- */
const ROLL_NAMN = { pelare: "Pelarsida", stod: "Stödartikel", kategori: "Kategori" };
const TYP_NAMN = { post: "Artikel", page: "Sida", kategori: "Kategori" };
const seo = { data: null, pos: null, vald: null, vy: null, klusterNamn: {}, farg: {} };
// Kategorisk palett (kontrollerad för färgblindhet). Varje kluster får en fast färg efter namn, så att
// färgerna inte byter plats när kartan filtreras eller kartläggs om. Fler än åtta kluster blir grå.
const KLUSTER_FARGER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const OVRIG_FARG = "#9C998F";

async function loadSeo() {
  try { seo.data = await api("/api/seokarta"); } catch (err) { toast(err.message); return; }
  const d = seo.data;
  seo.klusterNamn = Object.fromEntries(d.kluster.map((k) => [k.id, k.namn]));
  const namnordning = d.kluster.filter((k) => k.id !== "ovrigt").map((k) => k.id)
    .sort((a, b) => seo.klusterNamn[a].localeCompare(seo.klusterNamn[b], "sv"));
  seo.farg = Object.fromEntries(namnordning.map((id, i) => [id, KLUSTER_FARGER[i] || OVRIG_FARG]));
  $("#clusterLegend").innerHTML = d.kluster.map((k) => `<li><span class="dot" style="background:${seo.farg[k.id] || OVRIG_FARG}"></span>${esc(k.namn)}</li>`).join("");
  $("#aiBtn").disabled = !d.noder.length || !d.ai;
  $("#aiBtn").title = d.ai ? "" : "Kräver ANTHROPIC_API_KEY på servern";
  $("#mapBtn").disabled = !d.wp_url;
  $("#seoMeta").textContent = d.kartlagd ? `Senast kartlagd ${formatTid(d.kartlagd)} · ${d.wp_url}` : "";
  const tom = !d.noder.length;
  $("#seoEmpty").hidden = !tom;
  $("#seoContent").hidden = tom;
  $("#seoEmptyText").textContent = d.wp_url
    ? `Tryck på Kartlägg sajten för att läsa in ${d.wp_url}. Det tar en stund för en stor sajt, och ingenting ändras på sajten.`
    : "Ange sajtens adress (WP_URL) på servern för att kunna kartlägga den.";
  if (tom) { renderForslag(); return; }

  const sel = $("#fKluster"), fore = sel.value;
  sel.innerHTML = `<option value="">Alla kluster</option>` + d.kluster.map((k) => `<option value="${esc(k.id)}">${esc(k.namn)} (${k.antal})</option>`).join("");
  sel.value = d.kluster.some((k) => k.id === fore) ? fore : "";
  const v = d.verifierat;
  const lf = d.lankforslag_antal || {};
  const ls = d.lankstatistik;
  const stats = [
    [d.noder.length, "kartlagda sidor", `${d.kluster.filter((k) => k.id !== "ovrigt").length} ämneskluster`],
    [ls.fungerande, "fungerande interna länkar", ""],
    [ls.trasiga, "trasiga länkar", "utan planerad artikel"],
    [ls.planerade_mal, "planerade mål-URL", `${ls.planerade_lankar} länkar väntar på artikel`],
    [v.isolerade.length, "isolerade sidor", ""],
    [(lf.ny || 0) + (lf.godkand || 0), "väntande länkförslag", ""],
  ];
  $("#seoStats").innerHTML = stats.map(([n, t, delt]) => `<div class="stat"><b>${n.toLocaleString("sv-SE")}</b><span>${t}</span>${delt ? `<span class="delt">${esc(delt)}</span>` : ""}</div>`).join("");
  layoutGraph();
  renderGraph();
  renderProblems();
  renderForslag();
  renderNodeTable();
  loadLinks();
  loadSaknade();
}

function nodSynlig(n) {
  const k = $("#fKluster").value, t = $("#fTyp").value, st = $("#fStatus").value, inn = $("#fIn").value;
  if (k && n.kluster !== k) return false;
  if (t && n.typ !== t) return false;
  if (inn === "0" && n.in !== 0) return false;
  if (inn === "1-2" && (n.in < 1 || n.in > 2)) return false;
  if (inn === "3-9" && (n.in < 3 || n.in > 9)) return false;
  if (inn === "10" && n.in < 10) return false;
  if (st === "pelare" && n.roll !== "pelare") return false;
  if (st === "isolerad" && !n.isolerad) return false;
  if (st === "fa" && !n.fa_inkommande) return false;
  if (st === "foraldralos" && !n.foraldralos) return false;
  if (st === "trasiga" && !n.trasiga) return false;
  if (st === "kommersiell" && !n.kommersiell) return false;
  return true;
}
["#fKluster", "#fTyp", "#fStatus", "#fIn"].forEach((id) => $(id).addEventListener("change", () => { renderGraph(); renderNodeTable(); }));

// Enkel kraftbaserad layout: kluster placeras runt en cirkel, länkar drar ihop, alla noder stöter bort varandra.
function layoutGraph() {
  const { noder, kanter, kluster } = seo.data;
  const W = 1000, H = 700, n = noder.length;
  const klIndex = Object.fromEntries(kluster.map((k, i) => [k.id, i]));
  const centrum = kluster.map((_, i) => {
    const vinkel = (i / Math.max(1, kluster.length)) * Math.PI * 2;
    const r = kluster.length > 1 ? Math.min(W, H) * 0.32 : 0;
    return [W / 2 + Math.cos(vinkel) * r, H / 2 + Math.sin(vinkel) * r];
  });
  let slump = 1;
  const rnd = () => { slump = (slump * 16807) % 2147483647; return slump / 2147483647 - 0.5; };
  const p = noder.map((nd) => { const c = centrum[klIndex[nd.kluster]] || [W / 2, H / 2]; return { x: c[0] + rnd() * 80, y: c[1] + rnd() * 80, vx: 0, vy: 0 }; });
  const idx = Object.fromEntries(noder.map((nd, i) => [nd.id, i]));
  const kant = kanter.map((k) => [idx[k.fran], idx[k.till]]).filter(([a, b]) => a !== undefined && b !== undefined);
  const varv = n > 1200 ? 40 : n > 500 ? 90 : 220;
  const k2 = 900;
  for (let t = 0; t < varv; t++) {
    const kyla = 1 - t / varv;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = p[i].x - p[j].x, dy = p[i].y - p[j].y;
        const d2 = Math.max(25, dx * dx + dy * dy);
        if (d2 > 40000) continue;
        const f = k2 / d2;
        dx *= f; dy *= f;
        p[i].vx += dx; p[i].vy += dy; p[j].vx -= dx; p[j].vy -= dy;
      }
    }
    for (const [a, b] of kant) {
      const dx = p[b].x - p[a].x, dy = p[b].y - p[a].y;
      p[a].vx += dx * 0.01; p[a].vy += dy * 0.01; p[b].vx -= dx * 0.01; p[b].vy -= dy * 0.01;
    }
    noder.forEach((nd, i) => {
      const c = centrum[klIndex[nd.kluster]] || [W / 2, H / 2];
      p[i].vx += (c[0] - p[i].x) * 0.02; p[i].vy += (c[1] - p[i].y) * 0.02;
      const steg = Math.min(12, Math.hypot(p[i].vx, p[i].vy)) * kyla;
      const fart = Math.hypot(p[i].vx, p[i].vy) || 1;
      p[i].x += (p[i].vx / fart) * steg; p[i].y += (p[i].vy / fart) * steg;
      p[i].vx *= 0.5; p[i].vy *= 0.5;
    });
  }
  const xs = p.map((q) => q.x), ys = p.map((q) => q.y);
  const kant0 = 30;
  // Extra marginal till höger för etiketterna.
  seo.hel = [Math.min(...xs) - kant0, Math.min(...ys) - kant0, Math.max(...xs) - Math.min(...xs) + 2 * kant0 + 180, Math.max(...ys) - Math.min(...ys) + 2 * kant0];
  seo.vy = [...seo.hel];
  seo.pos = Object.fromEntries(noder.map((nd, i) => [nd.id, p[i]]));
}

function radie(nd) { return Math.min(16, 5 + Math.sqrt(nd.in) * 2.2) + (nd.roll === "pelare" ? 3 : 0); }

// Kartans enheter per skärmpixel, så att noder och etiketter behåller sin storlek vid zoom och på mobil.
function skala() {
  const r = $("#graph").getBoundingClientRect();
  return r.width ? Math.max(seo.vy[2] / r.width, seo.vy[3] / r.height) : 1;
}
function renderGraph() {
  const svg = $("#graph");
  const { noder, kanter } = seo.data;
  const pos = seo.pos;
  const k = skala();
  const per = Object.fromEntries(noder.map((n) => [n.id, n]));
  // Linjer med pilspets vid målsidan; pilen hamnar vid nodens kant oavsett zoom.
  const kantSvg = kanter.map((kant) => {
    const a = pos[kant.fran], b = pos[kant.till], na = per[kant.fran], nb = per[kant.till];
    if (!a || !b) return "";
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    const ra = radie(na) * k, rb = radie(nb) * k + 2 * k;
    if (len < ra + rb + 6 * k) return "";
    const x1 = a.x + ux * ra, y1 = a.y + uy * ra, x2 = b.x - ux * rb, y2 = b.y - uy * rb;
    const sp = 7 * k, w = 3.5 * k, bx = x2 - ux * sp, by = y2 - uy * sp;
    const aktiv = seo.vald && (kant.fran === seo.vald || kant.till === seo.vald) ? " aktiv" : "";
    const dold = nodSynlig(na) || nodSynlig(nb) ? "" : ' opacity="0.15"';
    return `<g${dold}><line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${bx.toFixed(1)}" y2="${by.toFixed(1)}" class="${aktiv.trim()}"/>`
      + `<path d="M${x2.toFixed(1)} ${y2.toFixed(1)}L${(bx - uy * w).toFixed(1)} ${(by + ux * w).toFixed(1)}L${(bx + uy * w).toFixed(1)} ${(by - ux * w).toFixed(1)}Z" class="pilspets${aktiv}"/></g>`;
  }).join("");
  const nodSvg = noder.map((nd) => {
    const q = pos[nd.id], r = radie(nd) * k;
    const farg = seo.farg[nd.kluster] || OVRIG_FARG;
    const klass = ["form", nd.roll === "pelare" ? "pelare" : "", nd.foraldralos ? "utan" : nd.fa_inkommande ? "fa" : ""].join(" ").trim();
    const form = nd.typ === "kategori"
      ? `<rect x="${(q.x - r).toFixed(1)}" y="${(q.y - r).toFixed(1)}" width="${(2 * r).toFixed(1)}" height="${(2 * r).toFixed(1)}" rx="${(3 * k).toFixed(1)}" fill="${farg}" class="${klass}"/>`
      : `<circle cx="${q.x.toFixed(1)}" cy="${q.y.toFixed(1)}" r="${r.toFixed(1)}" fill="${farg}" class="${klass}"/>`;
    const bruten = nd.trasiga ? `<text class="bruten" x="${(q.x + r * 0.55).toFixed(1)}" y="${(q.y - r * 0.55).toFixed(1)}" font-size="${(14 * k).toFixed(2)}" stroke-width="${(3 * k).toFixed(2)}">×</text>` : "";
    const etikett = nd.roll === "pelare" || nd.typ === "kategori" || nd.id === seo.vald
      ? `<text x="${(q.x + r + 4 * k).toFixed(1)}" y="${(q.y + 4 * k).toFixed(1)}" font-size="${(12 * k).toFixed(2)}" stroke-width="${(3 * k).toFixed(2)}">${esc(nd.titel.length > 34 ? nd.titel.slice(0, 33) + "…" : nd.titel)}</text>` : "";
    return `<g class="nod${nodSynlig(nd) ? "" : " dold"}${nd.id === seo.vald ? " vald" : ""}" data-id="${nd.id}" tabindex="0" role="button" aria-label="${esc(nd.titel)}">${form}${bruten}${etikett}</g>`;
  }).join("");
  svg.innerHTML = `<g>${kantSvg}</g><g>${nodSvg}</g>`;
  svg.setAttribute("viewBox", seo.vy.map((v) => v.toFixed(1)).join(" "));
}

function zooma(faktor, cx, cy) {
  const [x, y, w, h] = seo.vy;
  const nw = Math.min(seo.hel[2] * 3, Math.max(60, w * faktor)), nh = nw * (h / w);
  cx = cx ?? x + w / 2; cy = cy ?? y + h / 2;
  seo.vy = [cx - (cx - x) * (nw / w), cy - (cy - y) * (nh / h), nw, nh];
  ritaSnart();
}
let ritning = 0;
function ritaSnart() {
  cancelAnimationFrame(ritning);
  ritning = requestAnimationFrame(renderGraph);
}
window.addEventListener("resize", () => { if (seo.data && !$("#seoView").hidden) ritaSnart(); });
function svgPunkt(e) {
  const r = $("#graph").getBoundingClientRect(), [x, y, w, h] = seo.vy;
  // viewBox bevarar proportionerna (xMidYMid meet): räkna med den skala som faktiskt används.
  const s = Math.max(w / r.width, h / r.height);
  return [x + w / 2 + (e.clientX - r.left - r.width / 2) * s, y + h / 2 + (e.clientY - r.top - r.height / 2) * s, s];
}
$("#zoomIn").addEventListener("click", () => zooma(0.7));
$("#zoomOut").addEventListener("click", () => zooma(1.4));
$("#zoomReset").addEventListener("click", () => { seo.vy = [...seo.hel]; zooma(1); });
$("#graph").addEventListener("wheel", (e) => {
  e.preventDefault();
  const [px, py] = svgPunkt(e);
  zooma(e.deltaY > 0 ? 1.15 : 0.87, px, py);
}, { passive: false });

// Panorering med mus eller ett finger, nyp-zoom med två fingrar.
const pekare = new Map();
let flyttad = false;
$("#graph").addEventListener("pointerdown", (e) => {
  pekare.set(e.pointerId, { x: e.clientX, y: e.clientY });
  flyttad = false;
  $("#graph").setPointerCapture(e.pointerId);
});
$("#graph").addEventListener("pointermove", (e) => {
  const fore = pekare.get(e.pointerId);
  if (!fore) return visaTips(e);
  if (pekare.size === 2) {
    const [a, b] = [...pekare.values()];
    const avstand = Math.hypot(a.x - b.x, a.y - b.y);
    pekare.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const [c, d] = [...pekare.values()];
    const nytt = Math.hypot(c.x - d.x, c.y - d.y);
    if (avstand && nytt) zooma(avstand / nytt);
    flyttad = true;
    return;
  }
  const [, , s] = svgPunkt(e);
  const dx = (e.clientX - fore.x) * s, dy = (e.clientY - fore.y) * s;
  if (Math.abs(e.clientX - fore.x) + Math.abs(e.clientY - fore.y) > 3) flyttad = true;
  pekare.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (flyttad) {
    $("#graph").classList.add("drar");
    seo.vy[0] -= dx; seo.vy[1] -= dy;
    $("#graph").setAttribute("viewBox", seo.vy.map((v) => v.toFixed(1)).join(" "));
  }
});
["pointerup", "pointercancel"].forEach((t) => $("#graph").addEventListener(t, (e) => {
  pekare.delete(e.pointerId);
  $("#graph").classList.remove("drar");
  if (t === "pointerup" && !flyttad) {
    const g = document.elementFromPoint(e.clientX, e.clientY)?.closest(".nod");
    if (g) valjNod(Number(g.dataset.id));
  }
}));
$("#graph").addEventListener("pointerleave", () => { $("#graphTip").hidden = true; });
$("#graph").addEventListener("keydown", (e) => {
  const g = e.target.closest?.(".nod");
  if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); valjNod(Number(g.dataset.id)); }
});

function visaTips(e) {
  const g = e.target.closest?.(".nod"), tip = $("#graphTip");
  if (!g) { tip.hidden = true; return; }
  const nd = seo.data.noder.find((n) => n.id === Number(g.dataset.id));
  const r = $(".graph-wrap").getBoundingClientRect();
  const status = nd.isolerad ? " · isolerad" : nd.foraldralos ? " · saknar inkommande" : nd.fa_inkommande ? " · få inkommande" : "";
  tip.innerHTML = `<strong>${esc(nd.titel)}</strong><br>${ROLL_NAMN[nd.roll]} · ${esc(seo.klusterNamn[nd.kluster] || "")}<br>${nd.in} in · ${nd.ut} ut${nd.trasiga ? ` · ${nd.trasiga} brutna` : ""}${status}`;
  tip.hidden = false;
  tip.style.left = `${Math.min(r.width - 250, e.clientX - r.left + 12)}px`;
  tip.style.top = `${e.clientY - r.top + 12}px`;
}

async function valjNod(id) {
  seo.vald = id;
  renderGraph();
  const panel = $("#seoDetail");
  panel.innerHTML = `<p class="muted">Hämtar…</p>`;
  let d;
  try { d = await api(`/api/seokarta/sida/${id}`); } catch (err) { panel.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  const nd = seo.data.noder.find((n) => n.id === id);
  const lank = (url, text) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>`;
  const ut = d.utgaende.map((l) => `<li class="${l.status === "trasig" ? "trasig" : ""}">${l.till_id ? `<button class="lank" type="button" data-nod="${l.till_id}">${esc(l.titel)}</button>` : esc(l.till_url)}
      <span class="ank">”${esc(l.ankartext)}”${l.status === "trasig" ? " · trasig länk (404)" : l.status === "okand" ? " · okänd sida" : ""}</span></li>`).join("");
  const inn = d.inkommande.map((l) => `<li><button class="lank" type="button" data-nod="${l.fran_id}">${esc(l.titel)}</button><span class="ank">”${esc(l.ankartext)}”</span></li>`).join("");
  panel.innerHTML = `
    <h3>${esc(d.titel)}</h3>
    <div class="url">${lank(d.url, d.url)}</div>
    <dl>
      <dt>Typ</dt><dd>${TYP_NAMN[d.typ]} · ${ROLL_NAMN[nd.roll]}</dd>
      <dt>Kluster</dt><dd><span class="dot-inline" style="background:${seo.farg[nd.kluster] || OVRIG_FARG}"></span>${esc(seo.klusterNamn[nd.kluster] || "")}</dd>
      ${d.typ !== "kategori" ? `<dt>Huvudnyckelord</dt><dd>${esc(d.huvudnyckelord)} <span class="muted">(${esc(d.nyckelord_kalla)})</span></dd>` : ""}
      ${d.typ !== "kategori" ? `<dt>Längd</dt><dd>${d.ord.toLocaleString("sv-SE")} ord</dd>` : ""}
      ${d.seo_titel != null ? `<dt>SEO-titel</dt><dd>${esc(d.seo_titel)} <span class="muted">(${d.seo_titel.length} tecken)</span></dd>` : ""}
      ${d.seo_beskrivning != null ? `<dt>Metabeskrivning</dt><dd>${d.seo_beskrivning ? `${esc(d.seo_beskrivning)} <span class="muted">(${d.seo_beskrivning.length} tecken)</span>` : `<span style="color:var(--bad)">Saknas</span>`}</dd>` : ""}
    </dl>
    <label class="toggle"><input type="checkbox" id="kommersiellToggle" ${nd.kommersiell ? "checked" : ""}><span class="toggle-track"><span class="toggle-thumb"></span></span>Viktig kommersiell sida</label>
    <h4>Inkommande länkar (${d.inkommande.length})</h4>
    <ul class="link-list">${inn || `<li>Inga – sidan saknar inkommande internlänkar.</li>`}</ul>
    <h4>Utgående länkar (${d.utgaende.length})</h4>
    <ul class="link-list">${ut || "<li>Inga.</li>"}</ul>
    <h4>Rekommenderade nya länkar</h4>
    <ul class="rek">${[
      ...d.forslag.map((f) => `<li>${f.kalla_id === id ? `Länka härifrån till <b>${esc(f.mal_titel || f.mal_artikel || f.mal_url)}</b>` : `Länka hit från <b>${esc(f.kalla_titel)}</b>`} med ”${esc(f.ankartext)}” <span class="tag ${f.ursprung === "ai" ? "tag-ai" : "tag-warn"}">${f.ursprung === "ai" ? "AI-förslag" : "Beräknat"}</span></li>`),
      ...d.narliggande.map((x) => `<li class="beraknad">Närliggande utan länk: <b>${esc(x.a === id ? x.b_titel : x.a_titel)}</b> <span class="tag tag-warn">Beräknat</span></li>`),
    ].join("") || '<li class="beraknad">Inga rekommendationer just nu. Tryck på Hitta länkmöjligheter.</li>'}</ul>`;
  $("#kommersiellToggle").addEventListener("change", async (e) => {
    try { await api(`/api/seokarta/sida/${id}/kommersiell`, { kommersiell: e.target.checked }); await loadSeo(); valjNod(id); }
    catch (err) { toast(err.message); }
  });
  if (matchMedia("(max-width: 1079px)").matches) panel.scrollIntoView({ behavior: "smooth", block: "start" });
}
$("#seoView").addEventListener("click", (e) => {
  const b = e.target.closest("[data-nod]");
  if (b) valjNod(Number(b.dataset.nod));
});

function renderProblems() {
  const { verifierat: v, beraknat: b } = seo.data;
  const lista = (rubrik, rader, tomText) => `<div class="problem"><h3>${rubrik} <b>${rader.length}</b></h3>
    <ul>${rader.length ? rader.join("") : `<li class="tom">${tomText}</li>`}</ul></div>`;
  const nodLank = (id, titel) => `<button class="lank" type="button" data-nod="${id}">${esc(titel)}</button>`;
  $("#verifierat").innerHTML = [
    lista("Trasiga internlänkar", v.trasiga_lankar.flatMap((x) => x.lankar.map((l) => `<li>${nodLank(x.id, x.titel)} → ${esc(l.url)} <span class="muted">(”${esc(l.ankartext)}”)</span></li>`)), "Inga trasiga länkar hittades."),
    lista("Saknar inkommande länkar", v.utan_inkommande.map((x) => `<li>${nodLank(x.id, x.titel)}</li>`), "Alla sidor har inkommande länkar."),
    lista("Saknar metabeskrivning", v.saknar_metabeskrivning.map((x) => `<li>${nodLank(x.id, x.titel)}</li>`), "Inga (eller SEO-pluginet exponerar inte fältet)."),
  ].join("");
  $("#beraknat").innerHTML = [
    lista("Möjlig kannibalisering", b.mojlig_kannibalisering.map((x) => `<li>${nodLank(x.a, x.a_titel)} och ${nodLank(x.b, x.b_titel)} <span class="muted">(${Math.round(x.likhet * 100)} % lika)</span></li>`), "Inga snarlika titlar."),
    lista("Närliggande artiklar utan länk", b.saknade_lankar.map((x) => `<li>${nodLank(x.a, x.a_titel)} ↔ ${nodLank(x.b, x.b_titel)}</li>`), "Inga uppenbara luckor."),
    lista("Kommersiella sidor med få länkar", b.svaga_kommersiella.map((x) => `<li>${nodLank(x.id, x.titel)} <span class="muted">(${x.in} inkommande)</span></li>`), "Markera viktiga kommersiella sidor i detaljpanelen."),
  ].join("");
}

function renderForslag() {
  const f = seo.data?.forslag || [];
  const titel = Object.fromEntries((seo.data?.noder || []).map((n) => [n.url, n.titel]));
  const urlLista = (urler) => urler.map((u) => esc(titel[u] || u)).join(", ");
  $("#forslag").innerHTML = f.length ? f.map((x) => `
    <article class="forslag-kort">
      <span class="kl">${esc(x.kluster)} · ${esc(x.sokintention || "")}</span>
      <h3>${esc(x.huvudsokord)}</h3>
      ${x.sekundara_sokord.length ? `<div class="chips">${x.sekundara_sokord.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>` : ""}
      <p>${esc(x.motivering)}</p>
      ${x.lanka_till.length ? `<div class="rad"><b>Länkar till:</b> ${urlLista(x.lanka_till)}</div>` : ""}
      ${x.lanka_fran.length ? `<div class="rad"><b>Kan länkas från:</b> ${urlLista(x.lanka_fran)}</div>` : ""}
      ${x.krockar_med.length ? `<div class="varning">Kan konkurrera med: ${x.krockar_med.map(esc).join(", ")}</div>` : ""}
      ${x.borttagna_lankar ? `<div class="rad">${x.borttagna_lankar} föreslagna adresser fanns inte på sajten och togs bort.</div>` : ""}
      <div class="knappar">
        <button class="btn btn-primary btn-sm" type="button" data-tillkon="${x.id}">Lägg i artikelkön</button>
        <button class="btn btn-ghost btn-sm" type="button" data-avvisa-forslag="${x.id}">Avvisa</button>
      </div>
    </article>`).join("") : `<p class="muted">Inga förslag ännu. Tryck på Föreslå nya artiklar när sajten är kartlagd.</p>`;
}
$("#forslag").addEventListener("click", async (e) => {
  const till = e.target.dataset.tillkon, bort = e.target.dataset.avvisaForslag;
  try {
    if (till) { await api(`/api/seokarta/forslag/${till}/till-kon`, {}); toast("Ämnet ligger nu i artikelkön som utkast"); }
    else if (bort) { await api(`/api/seokarta/forslag/${bort}/avvisa`, {}); }
    else return;
    await loadSeo();
  } catch (err) { toast(err.message); }
});

function renderNodeTable() {
  const synliga = seo.data.noder.filter(nodSynlig).sort((a, b) => b.in - a.in);
  $("#nodeCount").textContent = `${synliga.length} av ${seo.data.noder.length}`;
  $("#nodeTable").innerHTML = synliga.slice(0, 500).map((n) => `<tr data-nod="${n.id}">
      <td>${esc(n.titel)}${n.foraldralos ? ` <span class="tag tag-warn">utan inkommande</span>` : ""}${n.kommersiell ? ` <span class="tag tag-ai">kommersiell</span>` : ""}</td>
      <td>${esc(seo.klusterNamn[n.kluster] || "")}</td><td>${ROLL_NAMN[n.roll]}</td>
      <td class="num">${n.in}</td><td class="num">${n.ut}</td><td class="num">${n.trasiga || ""}</td></tr>`).join("");
}

$("#mapBtn").addEventListener("click", async () => {
  const btn = $("#mapBtn");
  btn.disabled = true;
  btn.textContent = "Kartlägger…";
  try {
    const r = await api("/api/seokarta/kartlagg", {});
    toast(`Klart: ${r.sidor} sidor och ${r.lankar} internlänkar inlästa`);
    seo.vald = null;
    await loadSeo();
  } catch (err) { toast(err.message); }
  finally { btn.textContent = "Kartlägg sajten"; btn.disabled = false; }
});
$("#aiBtn").addEventListener("click", async () => {
  const btn = $("#aiBtn");
  btn.disabled = true;
  btn.textContent = "AI analyserar…";
  try {
    const r = await api("/api/seokarta/forslag", {});
    toast(`${r.forslag.length} nya förslag`);
    await loadSeo();
  } catch (err) { toast(err.message); }
  finally { btn.textContent = "Föreslå nya artiklar"; btn.disabled = false; }
});

/* ---------- Länkförslag ---------- */
const LANK_STATUS = {
  vantar_pa_publicering: "väntar på publicering", ny: "Ny", godkand: "Godkänd", avvisad: "Avvisad", verkstalld: "Tillagd på sajten",
  simulerad: "Simulerad", konflikt: "Konflikt", fel: "Fel", aterstalld: "Återställd",
};
const lank = { data: null, filter: "ny", valda: new Set() };

async function loadLinks() {
  try { lank.data = await api("/api/lankforslag"); } catch (err) { toast(err.message); return; }
  const lage = lank.data.lage;
  $("#linkMode").className = `link-mode ${lage}`;
  $("#linkMode").textContent = lage === "skarp"
    ? "Skarpt läge: Uppdatera sajten skriver godkända länkar till de publicerade artiklarna på klimathemmet.se."
    : `Läge: ${lage}. Uppdatera sajten simulerar ändringarna och kontrollerar dem mot WordPress om det är anslutet, men skriver ingenting till sajten.`;
  const finns = new Set(lank.data.forslag.map((f) => f.id));
  lank.valda.forEach((id) => { if (!finns.has(id)) lank.valda.delete(id); });
  $("#aiLinksBtn").disabled = !seo.data?.ai;
  $("#aiLinksBtn").title = seo.data?.ai ? "" : "Kräver ANTHROPIC_API_KEY på servern";
  renderLankforslag();
}

function synligaForslag() {
  const f = lank.data?.forslag || [];
  if (lank.filter === "ny") return f.filter((x) => ["ny", "konflikt", "fel"].includes(x.status));
  if (lank.filter === "godkand") return f.filter((x) => x.status === "godkand");
  return f.filter((x) => ["verkstalld", "simulerad", "vantar_pa_publicering"].includes(x.status));
}

function forhandsHtml(f, efter = true) {
  const i = f.stycke.indexOf(f.ankartext);
  if (i < 0) return esc(f.stycke);
  const ank = esc(f.ankartext);
  return esc(f.stycke.slice(0, i)) + (efter ? `<mark class="ny" title="${esc(f.mal_url)}">${ank}</mark>` : ank) + esc(f.stycke.slice(i + f.ankartext.length));
}

function renderLankforslag() {
  const lista = synligaForslag();
  const kanMarkeras = lank.filter !== "ovriga";
  $("#checkAll").parentElement.hidden = !kanMarkeras || !lista.length;
  $("#checkAll").checked = kanMarkeras && lista.length > 0 && lista.every((f) => lank.valda.has(f.id));
  $("#linkList").innerHTML = lista.length ? lista.map((f) => `
    <li class="lf${lank.valda.has(f.id) ? " markerad" : ""}" data-id="${f.id}">
      ${kanMarkeras ? `<input type="checkbox" class="lf-check" data-valj="${f.id}" ${lank.valda.has(f.id) ? "checked" : ""} aria-label="Markera förslaget">` : "<span></span>"}
      <div class="lf-rubrik"><b>${esc(f.kalla_titel)}</b> → <b>${esc(f.mal_titel || f.mal_artikel || f.mal_url)}</b></div>
      <div class="lf-preview">${forhandsHtml(f)}</div>
      <div class="lf-meta">
        <span class="tag ${f.ursprung === "ai" ? "tag-ai" : "tag-warn"}">${f.ursprung === "ai" ? "AI-förslag" : "Beräknat"}</span>
        <span>Ankartext: ”${esc(f.ankartext)}”</span>
        <span>${esc(LANK_STATUS[f.status] || f.status)}</span>
      </div>
      ${f.motivering ? `<div class="lf-meta">${esc(f.motivering)}</div>` : ""}
      ${f.fel ? `<div class="lf-fel">${esc(f.fel)}</div>` : ""}
      ${["ny", "konflikt", "fel"].includes(f.status) ? `<div class="lf-knappar">
        <button class="btn btn-ghost btn-sm" type="button" data-lf="redigera" data-id="${f.id}">Redigera ankartext</button>
        <button class="btn btn-ghost btn-sm btn-danger" type="button" data-lf="avvisa" data-id="${f.id}">Avvisa</button>
        <button class="btn btn-primary btn-sm" type="button" data-lf="godkann" data-id="${f.id}">Godkänn</button>
      </div>` : f.status === "godkand" ? `<div class="lf-knappar"><button class="btn btn-ghost btn-sm" type="button" data-lf="aterkalla" data-id="${f.id}">Ångra godkännande</button></div>` : ""}
    </li>`).join("") : `<li class="pub-empty">${lank.filter === "ny" ? "Inga nya förslag. Tryck på Hitta länkmöjligheter eller Låt AI föreslå länkar." : lank.filter === "godkand" ? "Inga godkända förslag väntar." : "Ingen historik ännu."}</li>`;
  const valda = [...lank.valda];
  const godkanda = (lank.data?.forslag || []).filter((f) => f.status === "godkand");
  $("#approveLinksBtn").disabled = !valda.length || lank.filter !== "ny";
  $("#rejectLinksBtn").disabled = !valda.length || lank.filter === "ovriga";
  $("#applyLinksBtn").disabled = !godkanda.length;
  $("#applyLinksBtn").textContent = godkanda.length ? `Uppdatera sajten (${godkanda.length})` : "Uppdatera sajten";
  const andr = lank.data?.andringar || [];
  const kanAterstalla = lank.data?.lage === "skarp";
  $("#changeList").innerHTML = (andr.length ? andr.map((a) => `<li><strong>${esc(a.titel)}</strong>
      <span>${a.forslag_ids.length} länk${a.forslag_ids.length === 1 ? "" : "ar"} · ${a.status === "aterstalld" ? "återställd" : "tillagd"}</span>
      <time>${formatTid(a.tid)}</time>
      ${a.status === "verkstalld" && kanAterstalla ? `<button class="btn btn-ghost btn-sm" type="button" data-aterstall="${a.id}">Återställ</button>` : ""}</li>`).join("") : "<li class=\"pub-empty\">Inga ändringar har gjorts på sajten.</li>")
    + (lank.data?.logg || []).slice(0, 15).map((h) => `<li><strong>${esc(HANDLING_NAMN[h.handling] || h.handling)}</strong><q>${esc(h.kommentar || "")}</q><time>${formatTid(h.tid)}</time></li>`).join("");
}

$$("[data-lankfilter]").forEach((b) => b.addEventListener("click", () => {
  lank.filter = b.dataset.lankfilter;
  lank.valda.clear();
  $$("[data-lankfilter]").forEach((x) => x.classList.toggle("active", x === b));
  renderLankforslag();
}));
$("#checkAll").addEventListener("change", (e) => {
  synligaForslag().forEach((f) => { if (e.target.checked) lank.valda.add(f.id); else lank.valda.delete(f.id); });
  renderLankforslag();
});
$("#linkList").addEventListener("change", (e) => {
  const id = Number(e.target.dataset.valj);
  if (!id) return;
  if (e.target.checked) lank.valda.add(id); else lank.valda.delete(id);
  renderLankforslag();
});
$("#linkList").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-lf]");
  if (!b) return;
  const id = Number(b.dataset.id);
  try {
    if (b.dataset.lf === "godkann") await api("/api/lankforslag/godkann", { ids: [id] });
    if (b.dataset.lf === "avvisa") await api("/api/lankforslag/avvisa", { ids: [id] });
    if (b.dataset.lf === "aterkalla") await api("/api/lankforslag/aterkalla", { ids: [id] });
    if (b.dataset.lf === "redigera") {
      const f = lank.data.forslag.find((x) => x.id === id);
      const ny = await askComment({ title: "Redigera ankartext", ok: "Spara", required: true,
        text: `Ankartexten måste stå ordagrant i stycket: ”${f.stycke}”` });
      if (!ny) return;
      await api(`/api/lankforslag/${id}`, { ankartext: ny }, "PATCH");
      toast("Ankartexten är ändrad");
    }
    lank.valda.delete(id);
    await loadSeo();
  } catch (err) { toast(err.message); }
});
$("#approveLinksBtn").addEventListener("click", async () => {
  try {
    const r = await api("/api/lankforslag/godkann", { ids: [...lank.valda] });
    toast(`${r.godkanda} förslag godkända. Sajten ändras först när du väljer Uppdatera sajten.`);
    lank.valda.clear();
    await loadSeo();
  } catch (err) { toast(err.message); }
});
$("#rejectLinksBtn").addEventListener("click", async () => {
  if (!confirm(`Avvisa ${lank.valda.size} förslag?`)) return;
  try { await api("/api/lankforslag/avvisa", { ids: [...lank.valda] }); lank.valda.clear(); await loadSeo(); } catch (err) { toast(err.message); }
});

// Uppdatera sajten: visar exakt vad som ändras och kräver en bekräftelse.
$("#applyLinksBtn").addEventListener("click", () => {
  const godkanda = lank.data.forslag.filter((f) => f.status === "godkand");
  const skarp = lank.data.lage === "skarp";
  $("#applyText").textContent = skarp
    ? `${godkanda.length} länk${godkanda.length === 1 ? "" : "ar"} läggs till i publicerade artiklar. Bara den markerade texten görs till en länk; resten av artikeln lämnas orörd. Har en artikel ändrats i WordPress sedan kartläggningen hoppas den över. Alla ändringar kan återställas.`
    : `Läge: ${lank.data.lage}. Ändringarna kontrolleras men skrivs inte till sajten.`;
  $("#applyList").innerHTML = godkanda.map((f) => `<li><div class="vag">${esc(f.kalla_titel)} → ${esc(f.mal_url)}</div>${forhandsHtml(f)}</li>`).join("");
  $("#applyConfirm").checked = false;
  $("#applyOk").disabled = true;
  $("#applyOk").textContent = skarp ? "Uppdatera sajten" : "Simulera";
  $("#applyDialog").showModal();
});
$("#applyConfirm").addEventListener("change", (e) => { $("#applyOk").disabled = !e.target.checked; });
$("#applyDialog").addEventListener("close", async () => {
  if ($("#applyDialog").returnValue !== "ok" || !$("#applyConfirm").checked) return;
  const ids = lank.data.forslag.filter((f) => f.status === "godkand").map((f) => f.id);
  try {
    const r = await api("/api/lankforslag/verkstall", { ids, bekraftad: true });
    const delar = [];
    if (r.verkstallda) delar.push(`${r.verkstallda} tillagda på sajten`);
    if (r.simulerade) delar.push(`${r.simulerade} simulerade`);
    if (r.konflikter) delar.push(`${r.konflikter} konflikter`);
    if (r.fel) delar.push(`${r.fel} fel`);
    toast(delar.join(", ") || "Inget att göra");
    await loadSeo();
  } catch (err) { toast(err.message); }
});
$("#changeList").addEventListener("click", async (e) => {
  const id = e.target.dataset.aterstall;
  if (!id || !confirm("Ta bort studions länkar i artikeln igen? Senare manuella ändringar behålls.")) return;
  try { const r = await api(`/api/lankandringar/${id}/aterstall`, {}); toast(r.resultat); await loadSeo(); } catch (err) { toast(err.message); }
});
$("#calcLinksBtn").addEventListener("click", async () => {
  try { const r = await api("/api/lankforslag/berakna", {}); toast(`${r.nya} nya förslag`); await loadSeo(); } catch (err) { toast(err.message); }
});
$("#aiLinksBtn").addEventListener("click", async () => {
  const btn = $("#aiLinksBtn");
  btn.disabled = true; btn.textContent = "AI läser artiklarna…";
  try { const r = await api("/api/lankforslag/ai", {}); toast(`${r.nya} nya AI-förslag`); await loadSeo(); }
  catch (err) { toast(err.message); }
  finally { btn.textContent = "Låt AI föreslå länkar"; btn.disabled = false; }
});

/* ---------- Saknade sidor ---------- */
const saknad = { lista: [], filter: "", aktuell: null };

async function loadSaknade() {
  try { saknad.lista = (await api("/api/saknade")).saknade; } catch (err) { toast(err.message); return; }
  renderSaknade();
}

function renderSaknade() {
  const lista = saknad.lista.filter((m) => !saknad.filter || m.klass === saknad.filter);
  $("#missingSection").hidden = !saknad.lista.length;
  $("#missingList").innerHTML = lista.length ? lista.map((m) => {
    const f = m.sammanhang;
    const plan = m.artikel
      ? `<div class="ms-plan">Planerad artikel: ”${esc(m.artikel.huvudsokord)}” (${esc(STATUS_NAMN[m.artikel.status] || m.artikel.status)})</div>`
      : m.klass === "planerad" ? `<div class="ms-plan">Bekräftad som planerad av dig. Ingen artikel finns i kön ännu.</div>` : "";
    return `<li class="ms">
      <div class="ms-head"><code>${esc(m.sokvag)}</code>
        <span class="tag ${m.klass === "planerad" ? "tag-ok" : "tag-bad"}">${m.klass === "planerad" ? "Planerad" : "Trasig"}</span>
        <span class="prio prio-${m.prioritet.niva}">${m.prioritet.niva} prioritet</span></div>
      <div class="ms-count"><b>${m.antal}</b>${m.antal === 1 ? "befintlig artikel" : "befintliga artiklar"} länkar hit</div>
      ${!m.artikel && f.kluster ? `<div class="ms-forslag">Troligt ämneskluster: <b>${esc(f.kluster)}</b> <span class="tag tag-warn">Beräknat</span></div>` : ""}
      <div class="ms-why">${esc(m.prioritet.skal.join(" · "))}</div>
      ${plan}
      <details><summary>Visa länkande artiklar</summary><ul>${m.kallor.map((k) => `<li><button class="lank" type="button" data-nod="${k.id}">${esc(k.titel)}</button> <span class="muted">”${esc(k.ankartext)}”</span></li>`).join("")}</ul></details>
      <div class="ms-knappar">
        ${m.artikel ? `<button class="btn btn-ghost btn-sm" type="button" data-ms="oppna" data-id="${m.artikel.id}">Öppna artikeln</button>`
          : `<button class="btn btn-primary btn-sm" type="button" data-ms="skapa" data-sokvag="${esc(m.sokvag)}">Skapa artikel</button>`}
        ${!m.artikel && m.klass === "trasig" ? `<button class="btn btn-ghost btn-sm" type="button" data-ms="planera" data-sokvag="${esc(m.sokvag)}">Markera som planerad</button>` : ""}
        ${!m.artikel && m.bekraftad ? `<button class="btn btn-ghost btn-sm" type="button" data-ms="avplanera" data-sokvag="${esc(m.sokvag)}">Ta bort planering</button>` : ""}
      </div>
    </li>`;
  }).join("") : `<li class="pub-empty">Inga ${saknad.filter === "planerad" ? "planerade" : saknad.filter === "trasig" ? "trasiga" : "saknade"} adresser.</li>`;
}
$$("[data-saknadfilter]").forEach((b) => b.addEventListener("click", () => {
  saknad.filter = b.dataset.saknadfilter;
  $$("[data-saknadfilter]").forEach((x) => x.classList.toggle("active", x === b));
  renderSaknade();
}));

$("#missingList").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ms]");
  if (!b) return;
  const sokvag = b.dataset.sokvag;
  try {
    if (b.dataset.ms === "oppna") return openArticle(Number(b.dataset.id));
    if (b.dataset.ms === "skapa") return oppnaSkapa(saknad.lista.find((m) => m.sokvag === sokvag));
    if (b.dataset.ms === "planera") { await api("/api/saknade/planera", { sokvag }); toast("Adressen är markerad som planerad"); }
    if (b.dataset.ms === "avplanera") { await api("/api/saknade/ta-bort-planering", { sokvag }); toast("Planeringen är borttagen"); }
    await loadSeo();
  } catch (err) { toast(err.message); }
});

function kanSkapa() {
  const harHuvud = $("#createHuvud").value.trim().length >= 2;
  $("#createOk").disabled = !harHuvud || !$("#createConfirm").checked;
  // Visa varför knappen är låst när du bekräftat men inte angett något huvudsökord.
  $("#createError").hidden = harHuvud || !$("#createConfirm").checked;
}

function oppnaSkapa(m) {
  saknad.aktuell = m;
  $("#createPath").textContent = m.url || m.sokvag;
  $("#createWhy").textContent = `${m.antal} befintliga artiklar länkar redan hit. Prioritet: ${m.prioritet.niva} (${m.prioritet.skal.join(", ")}).`;
  $("#createKluster").innerHTML = `<option value="">Inget valt</option>` + (seo.data?.kluster || []).filter((k) => k.id !== "ovrigt")
    .map((k) => `<option value="${esc(k.namn)}">${esc(k.namn)}</option>`).join("");
  // Sökorden väljer du själv: fälten är alltid tomma från början.
  $("#createHuvud").value = "";
  $("#createSek").value = "";
  $("#createIntent").value = m.sammanhang.sokintention || "informativ";
  $("#createKluster").value = m.sammanhang.kluster || "";
  $("#createAiBox").hidden = true;
  $("#createAiList").innerHTML = "";
  $("#createConfirm").checked = false;
  $("#createAi").disabled = !seo.data?.ai;
  $("#createAi").title = seo.data?.ai ? "" : "Kräver ANTHROPIC_API_KEY på servern";
  kanSkapa();
  $("#createDialog").showModal();
  $("#createHuvud").focus();
}
["#createConfirm", "#createHuvud"].forEach((id) => $(id).addEventListener("input", kanSkapa));
$("#createConfirm").addEventListener("change", kanSkapa);

// AI-förslag visas separat; ingenting fylls i förrän du klickar på ett förslag.
function lagTillSekundart(ord) {
  const nu = $("#createSek").value.split(",").map((x) => x.trim()).filter(Boolean);
  if (!nu.some((x) => x.toLowerCase() === ord.toLowerCase())) nu.push(ord);
  $("#createSek").value = nu.join(", ");
}
$("#createAi").addEventListener("click", async () => {
  const btn = $("#createAi");
  btn.disabled = true; btn.textContent = "AI tänker…";
  try {
    const f = await api("/api/saknade/ai-forslag", { sokvag: saknad.aktuell.sokvag });
    saknad.ai = f;
    $("#createAiList").innerHTML = `
      <div class="ai-rad">Huvudsökord: <span class="chip">${esc(f.huvudsokord)}</span>
        <button class="btn btn-ghost btn-sm" type="button" data-ai="huvud">Använd</button></div>
      ${f.sekundara_sokord.length ? `<div class="ai-rad">Sekundära: ${f.sekundara_sokord.map((x, i) =>
        `<span class="chip">${esc(x)}</span><button class="btn btn-ghost btn-sm" type="button" data-ai="sek" data-i="${i}">Lägg till</button>`).join("")}
        <button class="btn btn-ghost btn-sm" type="button" data-ai="allasek">Lägg till alla</button></div>` : ""}
      <div class="ai-rad">Sökintention: <span class="chip">${esc(f.sokintention)}</span>
        <button class="btn btn-ghost btn-sm" type="button" data-ai="intent">Använd</button></div>
      ${f.kluster ? `<div class="ai-rad">Ämneskluster: <span class="chip">${esc(f.kluster)}</span>
        <button class="btn btn-ghost btn-sm" type="button" data-ai="kluster">Använd</button></div>` : ""}
      ${f.motivering ? `<p class="muted">${esc(f.motivering)}</p>` : ""}`;
    $("#createAiBox").hidden = false;
  } catch (err) { toast(err.message); }
  finally { btn.textContent = "Föreslå sökord med AI"; btn.disabled = !seo.data?.ai; }
});
$("#createAiList").addEventListener("click", (e) => {
  const b = e.target.closest("[data-ai]");
  const f = saknad.ai;
  if (!b || !f) return;
  if (b.dataset.ai === "huvud") $("#createHuvud").value = f.huvudsokord;
  if (b.dataset.ai === "sek") lagTillSekundart(f.sekundara_sokord[Number(b.dataset.i)]);
  if (b.dataset.ai === "allasek") f.sekundara_sokord.forEach(lagTillSekundart);
  if (b.dataset.ai === "intent") $("#createIntent").value = f.sokintention;
  if (b.dataset.ai === "kluster") $("#createKluster").value = f.kluster;
  kanSkapa();
});

$("#createDialog").addEventListener("close", async () => {
  if ($("#createDialog").returnValue !== "ok" || !$("#createConfirm").checked || $("#createHuvud").value.trim().length < 2) return;
  try {
    const r = await api("/api/saknade/skapa-artikel", {
      sokvag: saknad.aktuell.sokvag, bekraftad: true, huvudsokord: $("#createHuvud").value,
      sekundara_sokord: $("#createSek").value.split(",").map((x) => x.trim()).filter(Boolean),
      sokintention: $("#createIntent").value, kluster: $("#createKluster").value,
    });
    toast(`Artikeln ligger i artikelkön som utkast (låst till ${saknad.aktuell.sokvag})`);
    await loadSeo();
    loadQueue();
    return r;
  } catch (err) { toast(err.message); }
});

/* ---------- Start ---------- */
loadStatus();
(async () => {
  await loadQueue();
  const senaste = Number(recall("kh-studio-artikel"));
  if (senaste && state.queue.some((a) => a.id === senaste)) await openArticle(senaste, { keepView: true });
  showView(["editor", "pub", "seo"].includes(recall(VIEW_KEY)) ? recall(VIEW_KEY) : "queue");
})();
