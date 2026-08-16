"use strict";

/* Sdílený základ všech stránek: formátování, SVG helpery a měsíční přepínač. Konkrétní
   grafy a to, co je na stránce vidět, patří do `data.js` / `pipeline.js` / `method.js`.

   Všechny stránky čtou týž tvar payloadu (`dataset`, `generated_at`, `source`, `config`,
   `freshness`, `months`), jen s jinými poli v `months` -- build každé stránce zapeče
   jen to, co doopravdy kreslí. Proto tenhle soubor nesmí sáhnout na nic, co má jen
   některá z nich. */

const DATA = JSON.parse(document.getElementById("payload").textContent);
const MONTHS = DATA.months;
const CFG = DATA.config;

const nf = new Intl.NumberFormat("cs-CZ");
const nf1 = new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MONTH_NAMES = ["led", "úno", "bře", "dub", "kvě", "čvn", "čvc", "srp", "zář", "říj", "lis", "pro"];

const usd = (v) => "$" + nf.format(Math.round(v));
// Průměr bez pozorování přijde z buildu jako null. Pomlčka, ne "0.00" -- to by bylo
// tvrzení o datech, které jsme neudělali.
const orDash = (v, fn) => (v == null ? "—" : fn(v));
const pct = (v) => nf1.format(v * 100) + " %";
const usdM = (v) => "$" + nf1.format(v / 1e6) + " mil.";
// Zóny se v tržbě liší o šest řádů: Midtown miliardy, Rossville tisíce. Jedna jednotka
// pro všechny by půlku legendy mapy proměnila v "$0.0M".
// Storna jsou záporná, takže se řád bere z absolutní hodnoty a znaménko se lepí zvlášť
// -- jinak by "-480432" propadlo až do poslední větve a četlo se hůř než "-$480 tis.".
const usdCompact = (v) => {
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  return sign + (
    a >= 1e9 ? "$" + nf2.format(a / 1e9) + " mld."
      : a >= 1e6 ? "$" + nf1.format(a / 1e6) + " mil."
      : a >= 1e3 ? "$" + Math.round(a / 1e3) + " tis."
      : "$" + Math.round(a)
  );
};
// Přes miliardu už "3214 mil." nikdo nepřečte.
const usdBig = (v) => (v >= 1e9 ? "$" + nf2.format(v / 1e9) + " mld." : "$" + nf.format(Math.round(v / 1e6)) + " mil.");
const label = (m) => MONTH_NAMES[m.month - 1] + " " + m.year;
// Číslo za jeden měsíc samo o sobě neříká, jestli je to hodně: vedle něj musí stát
// průměr ostatních měsíců a odchylka od něj. Pod půl procenta se směr nekreslí -- to
// je šum, ne tendence, a šipka by tvrdila víc, než v datech je.
const delta = (v, mean) => {
  if (!mean || v == null) return "";
  const d = v / mean - 1;
  const dir = Math.abs(d) < 0.005 ? "flat" : d > 0 ? "up" : "down";
  const arrow = dir === "flat" ? "→" : dir === "up" ? "▲" : "▼";
  return `<b class="dv ${dir}">${arrow} ${nf1.format(Math.abs(d) * 100)} %</b>`;
};
const dayOf = (iso) => new Date(iso + "T00:00:00");
const isWeekend = (iso) => [0, 6].includes(dayOf(iso).getDay());
const dayLabel = (iso) => { const d = dayOf(iso); return d.getDate() + ". " + MONTH_NAMES[d.getMonth()]; };

// Stránka se staví i nad jedinou partition (čerstvý bucket, lokální běh), a "1 měsíců"
// nebo "2025-01 → 2025-01" vypadá jako rozbité číslo, ne jako malý dataset.
//
// Čeština má tři tvary, ne dva: 1 měsíc, 3 měsíce, 5 měsíců. Vyšší tvary mají default
// na ten nižší, takže se dají vypustit tam, kde jsou shodné -- u genitivu (1 souboru,
// 3 souborů, 5 souborů) i u nesklonných výrazů, kde stačí jediný tvar.
const plural = (n, one, few, many) =>
  nf.format(n) + " " + (n === 1 ? one : n >= 2 && n <= 4 ? few || one : many || few || one);
const SPAN = MONTHS.length > 1 ? MONTHS[0].key + " → " + MONTHS[MONTHS.length - 1].key : MONTHS[0].key;
const MONTHS_LABEL = plural(MONTHS.length, "měsíc", "měsíce", "měsíců");

// Dlaždice, jejíž hodnota je součet celé historie, to musí mít napsané v nadpisu: pod ní
// stojí stránka o jednom vybraném měsíci, a bez rozsahu si čtenář jde součet hledat tam.
// Nad jedinou partition se nepřipisuje nic -- "za 1 měsíc" je šum, žádná jiná volba není.
const SUM_SCOPE = MONTHS.length > 1 ? " · " + MONTHS_LABEL : "";

// Popisky dlaždic se skládají z dílů, které někdy chybí (nad jedinou partition není
// s čím měsíc porovnat).
const dots = (...parts) => parts.filter(Boolean).join(" · ");

let current = MONTHS[MONTHS.length - 1].key;
const idx = () => MONTHS.findIndex((m) => m.key === current);
const month = () => MONTHS[idx()];

// `render` definuje každá stránka sama; deklarace funkcí se hoistují, takže na tenhle
// odkaz stačí, že obojí skončí v témže `<script>`.
function select(key) {
  current = key;
  render();
}

/* ---------- slovník pravidel ---------- */

/* Čte je provozní stránka (co pravidlo chytilo v jednom běhu) i metodická (co chytilo
   za celou historii), takže je sdílený -- jinak by se dvě stránky rozešly v tom, jak
   se totéž pravidlo jmenuje.

   Popisky s číslem se berou z prahů toho běhu (`thresholds_applied` v manifestu), ne
   z aktuální konfigurace. Když se práh změní, starý běh se pořád popisuje tím, podle
   čeho se doopravdy řídil. */
const RULE_LABELS = {
  negative_fare: () => "záporné jízdné",
  zero_distance: () => "nulová vzdálenost",
  reversal: () => "storno jízdy",
  zero_total: () => "jízda bez tržby",
  nonpositive_duration: () => "nekladná doba jízdy",
  out_of_month: () => "vyzvednutí mimo měsíc",
  duration_over_limit: (t) => (t.max_duration_min ? "doba jízdy přes " + t.max_duration_min / 60 + " h" : "doba jízdy přes limit"),
  implausible_distance: (t) => (t.max_distance_mi ? "vzdálenost přes " + nf.format(t.max_distance_mi) + " mi" : "nevěrohodná vzdálenost"),
  impossible_speed: (t) => (t.max_speed_mph ? "odvozená rychlost přes " + nf.format(t.max_speed_mph) + " mph" : "nemožná rychlost"),
};

/* "karanténa" a "storno" oboje berou celý řádek, ale znamenají opak: karanténa je
   "tomuhle řádku nevěřím", storno "tomuhle řádku věřím, jenom to není jízda". Držet je
   pod jedním důsledkem znamenalo tvrdit, že se 1,8 % dat zahazuje jako vadných. */
const RULE_EFFECT = {
  reversal: "storno",
  zero_total: "jen se počítá",
  out_of_month: "karanténa",
  negative_fare: "pole vynulováno",
  zero_distance: "pole vynulováno",
  nonpositive_duration: "pole vynulováno",
  duration_over_limit: "pole vynulováno",
  implausible_distance: "pole vynulováno",
  impossible_speed: "pole vynulováno",
};

// Které pole pravidlo vynuluje. Tabulka pravidel na metodické stránce to ukazuje vedle
// důsledku: "vynulováno" samo o sobě neříká, které z měření se tím ztratí.
const RULE_FIELD = {
  negative_fare: "fare_amount",
  zero_distance: "trip_distance",
  implausible_distance: "trip_distance",
  impossible_speed: "trip_distance",
  nonpositive_duration: "duration_min",
  duration_over_limit: "duration_min",
};

// Důsledky, které berou celý řádek. Pole u nich nemá smysl, tabulka místo něj píše
// "celý řádek".
const ROW_EFFECTS = ["karanténa", "storno"];

const ruleLabel = (name, applied) => (RULE_LABELS[name] ? RULE_LABELS[name](applied || {}) : name);

/* ---------- svg helpers ---------- */

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs, text) {
  const node = document.createElementNS(NS, name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgRoot(host, height, title) {
  const width = Math.max(280, host.clientWidth || 320);
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": title });
  return { svg, width, height };
}

// Mono 11px má ~6,6 px na znak. Zkracuje se podle místa, ne podle počtu znaků -- jinak
// dlouhý popisek v úzké kartě uteče vlevo mimo viewBox.
function fit(text, px) {
  const max = Math.floor(px / 6.6);
  return text.length <= max ? text : text.slice(0, Math.max(1, max - 1)) + "…";
}

// Zaoblený je jen konec s daty; druhý sedí naplocho na základní čáře.
function barPath(x, y, w, h, r) {
  if (w <= 0.5) return `M${x},${y} h0.5 v${h} h-0.5 Z`;
  const rr = Math.min(r, w, h / 2);
  return `M${x},${y} H${x + w - rr} A${rr},${rr} 0 0 1 ${x + w},${y + rr} V${y + h - rr} A${rr},${rr} 0 0 1 ${x + w - rr},${y + h} H${x} Z`;
}

function barPathUp(x, y, w, h, r) {
  if (h <= 0.5) return `M${x},${y + h} h${w} v0.5 h${-w} Z`;
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + rr} A${rr},${rr} 0 0 1 ${x + rr},${y} H${x + w - rr} A${rr},${rr} 0 0 1 ${x + w},${y + rr} V${y + h} H${x} Z`;
}

function tipFor(host) {
  let tip = host.querySelector(".tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tip";
    tip.hidden = true;
    host.appendChild(tip);
  }
  return tip;
}

function placeTip(tip, host, x, y) {
  tip.hidden = false;
  const w = tip.offsetWidth;
  const max = host.clientWidth - w - 4;
  tip.style.left = Math.max(4, Math.min(max, x - w / 2)) + "px";
  tip.style.top = Math.max(0, y) + "px";
}

/* ---------- chart: horizontal bars ---------- */

// `opts.fill` je odstín celého grafu, ne jednotlivých pruhů: obarvit pruhy podle pořadí
// by tvrdilo, že první zóna je jiná kategorie než druhá -- je to táž veličina, jen menší.
function drawBars(host, items, opts) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const rowH = 20, gap = 9, T = 4;
  const H = items.length * (rowH + gap) - gap + T + 4;
  const { svg, width } = svgRoot(host, H, opts.title);
  const labelW = Math.min(opts.labelW, width * 0.42);
  const pw = Math.max(20, width - labelW - opts.valueW - 12);
  const max = Math.max(...items.map((d) => d.value));
  const tip = tipFor(host);

  items.forEach((d, i) => {
    const y = T + i * (rowH + gap);
    const w = (d.value / max) * pw;
    svg.appendChild(el("text", { x: labelW, y: y + rowH / 2 + 4, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, fit(d.label, labelW)));
    svg.appendChild(el("path", { d: barPath(labelW + 12, y, w, rowH, 4), fill: opts.fill || "var(--s1)" }));
    svg.appendChild(el("text", { x: labelW + 12 + w + 8, y: y + rowH / 2 + 4, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 11, "font-variant-numeric": "tabular-nums" }, d.display));

    const hit = el("rect", { x: 0, y: y - gap / 2, width, height: rowH + gap, fill: "transparent" });
    hit.appendChild(el("title", {}, (d.full || d.label) + ": " + d.display));
    hit.addEventListener("mousemove", (ev) => {
      const box = svg.getBoundingClientRect();
      tip.innerHTML = `<b>${d.full || d.label}</b><br>${d.tip}`;
      placeTip(tip, host, ev.clientX - box.left, Math.max(0, ((y - 4) / H) * host.clientHeight - 44));
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    svg.appendChild(hit);
  });

  host.appendChild(svg);
}

/* ---------- společné části stránky ---------- */

function buildEyebrow() {
  // Bez jména bucketu: to nese číslo AWS účtu a stránka je veřejná.
  document.getElementById("src").textContent =
    "nyc-taxi-etl · dataset=" + DATA.dataset + " · curated na " + DATA.source.store
    + " (" + DATA.source.region + ")";
}

function buildChips(chips) {
  document.getElementById("status").innerHTML =
    chips.map((c) => `<span class="chip"><i class="dot ${c.dot}"></i>${c.text}</span>`).join("");
}

function buildTiles(tiles) {
  document.getElementById("kpis").innerHTML = tiles.map((t) =>
    `<div class="tile"><span class="k">${t.k}</span><span class="v${t.name ? " name" : ""}">${t.v}</span><span class="s">${t.s}</span></div>`
  ).join("");
}

function buildPicker() {
  const sel = document.getElementById("month-select");
  sel.innerHTML = MONTHS.map((m) => `<option value="${m.key}">${label(m)}</option>`).join("");
  sel.addEventListener("change", (e) => select(e.target.value));
  document.getElementById("prev").addEventListener("click", () => { if (idx() > 0) select(MONTHS[idx() - 1].key); });
  document.getElementById("next").addEventListener("click", () => { if (idx() < MONTHS.length - 1) select(MONTHS[idx() + 1].key); });
}

function syncPicker() {
  const i = idx();
  document.getElementById("month-select").value = current;
  document.getElementById("prev").disabled = i === 0;
  document.getElementById("next").disabled = i === MONTHS.length - 1;
}

function watchResize() {
  let timer;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(render, 120);
  });
}
