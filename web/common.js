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

const nf = new Intl.NumberFormat("en-US");
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const usd = (v) => "$" + nf.format(Math.round(v));
// Průměr bez pozorování přijde z buildu jako null. Pomlčka, ne "0.00" -- to by bylo
// tvrzení o datech, které jsme neudělali.
const orDash = (v, fn) => (v == null ? "—" : fn(v));
const pct = (v) => nf1.format(v * 100) + " %";
const usdM = (v) => "$" + nf1.format(v / 1e6) + "M";
// Zóny se v tržbě liší o šest řádů: Midtown miliardy, Rossville tisíce. Jedna jednotka
// pro všechny by půlku legendy mapy proměnila v "$0.0M".
const usdCompact = (v) =>
  v >= 1e9 ? "$" + nf2.format(v / 1e9) + "B"
    : v >= 1e6 ? "$" + nf1.format(v / 1e6) + "M"
    : v >= 1e3 ? "$" + Math.round(v / 1e3) + "k"
    : "$" + Math.round(v);
// Přes miliardu už "3214M" nikdo nepřečte.
const usdBig = (v) => (v >= 1e9 ? "$" + nf2.format(v / 1e9) + "B" : "$" + nf.format(Math.round(v / 1e6)) + "M");
const label = (m) => MONTH_NAMES[m.month - 1] + " " + m.year;
const dayOf = (iso) => new Date(iso + "T00:00:00");
const isWeekend = (iso) => [0, 6].includes(dayOf(iso).getDay());
const dayLabel = (iso) => { const d = dayOf(iso); return MONTH_NAMES[d.getMonth()] + " " + d.getDate(); };

// Stránka se staví i nad jedinou partition (čerstvý bucket, lokální běh), a "1 months"
// nebo "2025-01 → 2025-01" vypadá jako rozbité číslo, ne jako malý dataset.
const plural = (n, one, many) => nf.format(n) + " " + (n === 1 ? one : many || one + "s");
const SPAN = MONTHS.length > 1 ? MONTHS[0].key + " → " + MONTHS[MONTHS.length - 1].key : MONTHS[0].key;

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
  negative_fare: () => "negative fare",
  zero_distance: () => "zero distance",
  nonpositive_total: () => "non-positive total",
  nonpositive_duration: () => "non-positive duration",
  out_of_month: () => "pickup out of month",
  duration_over_limit: (t) => (t.max_duration_min ? "duration over " + t.max_duration_min / 60 + " h" : "duration over limit"),
  implausible_distance: (t) => (t.max_distance_mi ? "distance over " + nf.format(t.max_distance_mi) + " mi" : "implausible distance"),
  impossible_speed: (t) => (t.max_speed_mph ? "implied speed over " + nf.format(t.max_speed_mph) + " mph" : "impossible speed"),
};

const RULE_EFFECT = {
  nonpositive_total: "quarantined",
  out_of_month: "quarantined",
  negative_fare: "field nulled",
  zero_distance: "field nulled",
  nonpositive_duration: "field nulled",
  duration_over_limit: "field nulled",
  implausible_distance: "field nulled",
  impossible_speed: "field nulled",
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

// Mono 11px is ~6.6px per glyph. Truncate by available space, not by character count,
// or a long label escapes the viewBox on the left inside a narrow card.
function fit(text, px) {
  const max = Math.floor(px / 6.6);
  return text.length <= max ? text : text.slice(0, Math.max(1, max - 1)) + "…";
}

// Rounded corners on the data end only; the other end sits flat on the baseline.
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
    svg.appendChild(el("path", { d: barPath(labelW + 12, y, w, rowH, 4), fill: "var(--s1)" }));
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
    "nyc-taxi-etl · dataset=" + DATA.dataset + " · curated on " + DATA.source.store
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
