/* Stránka pro čtenáře dat. Nic o běhu pipeline -- provozní čísla, manifesty a prahy
   jsou na `pipeline.html` a payload je sem ani nedostane.

   Celá stránka je jeden řízený pohled: nahoře se vybere měsíc a metrika a všechno pod
   tím je z toho měsíce a v té metrice. Výjimka je jediná a je logická -- graf "měsíc po
   měsíci" ukazuje všechny měsíce, protože jinak by ukazoval jeden sloupec.

   Drží to pohromadě jeden tvar dat. Payload nenese hotové průměry, ale sčitatelné
   součty a jejich jmenovatele, takže "řez" -- zóna v měsíci, jeden den, celý měsíc,
   celá historie -- je vždycky týž objekt a metrika je funkce nad ním. Mapa, denní
   křivka, žebříček zón i dlaždice proto počítají touž definicí; nemůže se stát, že by
   dvě místa na stránce tvrdila o téže věci jiné číslo. */

/* ---------- řezy dat ---------- */

const MAP = DATA.map;
const IDS = MAP.ids;
// Sloupce měsíce jsou pole zarovnaná na `MAP.ids`; obrysy jsou slovník klíčovaný
// řetězcem, tak se překlad drží tady na jednom místě.
const ZIDX = new Map(IDS.map((id, i) => [String(id), i]));
// Zóny 264/265 ("NV", "Outside of NYC") obrys nemají a mít nebudou: nejsou to místa,
// ale zbytkové kódy. Jejich jízdy se do mapy nevejdou, tak ať to stránka řekne.
const OFF_MAP = IDS.map((id, i) => i).filter((i) => !(String(IDS[i]) in MAP.paths));

const SUMS = ["trips", "revenue", "refunds", "dist_obs", "dist_sum", "dur_obs", "dur_sum", "fare_obs", "fare_sum"];
const zeroAgg = () => ({ trips: 0, revenue: 0, refunds: 0, dist_obs: 0, dist_sum: 0, dur_obs: 0, dur_sum: 0, fare_obs: 0, fare_sum: 0 });
const addRow = (a, cols, i) => { for (const k of SUMS) a[k] += cols[k][i]; return a; };
const addAgg = (a, b) => { for (const k of SUMS) a[k] += b[k]; return a; };
const rowAgg = (cols, i) => addRow(zeroAgg(), cols, i);

// Řezy se počítají jednou a drží: přepnutí metriky nesmí znamenat, že se 261 zón sčítá
// znovu. Měsíců je pár desítek, takže cache může být prostě mapa a nic se z ní nezahazuje.
const CACHE = new Map();
const memo = (key, make) => (CACHE.has(key) ? CACHE.get(key) : (CACHE.set(key, make()), CACHE.get(key)));

const zoneAggs = (m) => memo("z:" + m.key, () => IDS.map((_, i) => rowAgg(m.zones, i)));
const dayAggs = (m) => memo("d:" + m.key, () => m.daily.date.map((_, i) => rowAgg(m.daily, i)));
const monthAgg = (m) => memo("m:" + m.key, () => zoneAggs(m).reduce(addAgg, zeroAgg()));
const HISTORY = MONTHS.map(monthAgg).reduce(addAgg, zeroAgg());
const TOTAL_DAYS = MONTHS.reduce((a, m) => a + m.daily.date.length, 0);

/* ---------- metriky ---------- */

/* Tři skupiny, protože každá stojí na jiném jmenovateli a čte se jinak: součet za řez
   (obarví se prostě to, kde je nejvíc provozu), průměr na jízdu, a poměr dvou průměrů.
   Poslední skupina je odvozená -- rychlost ani cena za míli v curated jako sloupec není
   a nemůže být: průměr podílů se ze součtů dostat nedá. Proto je to podíl průměrů
   a `about` to u každé takové metriky říká nahlas.

   `value` vrací null, ne NaN, když není čím dělit: stránka má pro "nevíme" vlastní stav
   a nesmí propadnout do některého ze dvou zbylých.
   `obs` je jmenovatel, na kterém metrika stojí -- podle něj se pozná, kdy je průměr
   postavený na hrstce jízd, a u poměru dvou průměrů je to ten slabší z obou. */

const div = (n, d, s = 1) => (d > 0 ? (n / d) * s : null);
const avgDist = (a) => div(a.dist_sum, a.dist_obs);
const avgDur = (a) => div(a.dur_sum, a.dur_obs);
const avgFare = (a) => div(a.fare_sum, a.fare_obs);
const net = (a) => a.revenue + a.refunds;

const METRICS = {
  trips: {
    group: "totals",
    additive: true,
    label: "Zveřejněné jízdy",
    peak: "Nejvytíženější nástup",
    about: "Počet jízd přiřazených k zóně nástupu. Jediná metrika, která se nedělí ničím: součet je přesný.",
    value: (a) => a.trips,
    obs: () => null,
    full: (v) => nf.format(v) + " jízd",
    brief: (v) => nf.format(v) + " jízd",
    short: (v) => (v >= 1e6 ? nf1.format(v / 1e6) + " mil." : v >= 1e3 ? Math.round(v / 1e3) + " tis." : nf.format(v)),
  },
  revenue: {
    group: "totals",
    additive: true,
    label: "Tržby",
    peak: "Nejvýdělečnější zóna",
    about: "<code>total_amount</code> zveřejněných jízd — se spropitným, mýtným i příplatky — minus storna. Hrubá částka i objem storen mají v curated vlastní sloupce.",
    value: net,
    obs: () => null,
    full: (v) => usd(v),
    brief: usdCompact,
    short: usdCompact,
  },
  revenue_per_trip: {
    group: "means",
    label: "Tržba / jízdu",
    peak: "Nejdražší jízda",
    about: "Čisté tržby dělené počtem jízd. Celkovou částku nese každý řádek, takže je tenhle jmenovatel přesný. Čitatel je po odečtení storen, jmenovatel bez nich: storno není jízda.",
    value: (a) => div(net(a), a.trips),
    obs: (a) => a.trips,
    full: (v) => "$" + nf2.format(v),
    short: (v) => "$" + nf1.format(v),
  },
  avg_fare_usd: {
    group: "means",
    label: "Průměrné jízdné",
    peak: "Nejvyšší jízdné",
    about: "Jen <code>fare_amount</code>: taxametr bez spropitného, mýtného a příplatků. Vždy nižší než tržba na jízdu; rozdíl jsou právě příplatky.",
    value: avgFare,
    obs: (a) => a.fare_obs,
    full: (v) => "$" + nf2.format(v),
    // Ne na celé dolary: v jednom měsíci se šest pásem legendy vejde do pár dolarů
    // a zaokrouhlení by z nich udělalo dvakrát "$15".
    short: (v) => "$" + nf1.format(v),
  },
  avg_distance_mi: {
    group: "means",
    label: "Průměrná vzdálenost",
    peak: "Nejdelší jízdy",
    median: "median_dist",
    about: "Míle na jízdu. Průměr táhne nahoru několik extrémních řádků, tabulka dole ho staví vedle mediánu.",
    value: avgDist,
    obs: (a) => a.dist_obs,
    full: (v) => nf2.format(v) + " mi",
    short: (v) => nf1.format(v),
  },
  avg_duration_min: {
    group: "means",
    label: "Průměrná doba jízdy",
    peak: "Nejdelší čas v autě",
    median: "median_dur",
    about: "Minuty na jízdu, od nástupu po výstup. Nekladné doby vynuluje pravidlo kvality a do průměru nevstupují.",
    value: avgDur,
    obs: (a) => a.dur_obs,
    full: (v) => nf1.format(v) + " min",
    short: (v) => nf1.format(v),
  },
  speed_mph: {
    group: "ratios",
    label: "Odvozená rychlost",
    peak: "Nejrychlejší zóna",
    median: "median_dur",
    about: "Průměrná vzdálenost dělená průměrnou dobou: podíl dvou průměrů, ne průměr rychlostí, a každý stojí na jiné množině řádků. Kde je pokrytí vzdálenosti výrazně pod pokrytím doby, je výsledek nespolehlivý; bublina proto nese obě čísla.",
    value: (a) => (a.dist_obs > 0 && a.dur_sum > 0 ? div(avgDist(a), avgDur(a), 60) : null),
    obs: (a) => Math.min(a.dist_obs, a.dur_obs),
    full: (v) => nf1.format(v) + " mph",
    short: (v) => nf1.format(v),
  },
  fare_per_mile: {
    group: "ratios",
    label: "Jízdné / míli",
    peak: "Nejdražší míle",
    median: "median_dist",
    about: "Průměrné jízdné dělené průměrnou vzdáleností. Vysoké u krátkých jízd s popojížděním, nízké na dlouhých tazích: taxametr účtuje čas i vzdálenost.",
    value: (a) => (a.fare_obs > 0 && a.dist_sum > 0 ? div(avgFare(a), avgDist(a)) : null),
    obs: (a) => Math.min(a.fare_obs, a.dist_obs),
    full: (v) => "$" + nf2.format(v) + " / mi",
    short: (v) => "$" + nf1.format(v),
  },
};

let metric = "trips";
const SPEC = () => METRICS[metric];

/* Průměr z hrstky jízd není průměr. Zóna se šesti jízdami za měsíc dá "rychlost" 65 mph
   -- není to nález, je to šum vydávaný za nález.

   Prahy jsou ale dva, protože mapa a žebříček se ptají na dvě různé věci.

   Mapa se ptá "má tenhle průměr vůbec smysl ukázat". To je vlastnost té jedné zóny:
   stabilita průměru stojí na počtu měření v ní, ne na tom, kolik se v ten měsíc jezdilo
   ve zbytku města. Měřítkem je proto průměrná kreslená zóna měsíce -- v lednu 2025 asi
   13 tisíc jízd -- a práh je její procento. Původní práh 0,1 % jízd celého města je
   v téhle jednotce 26 % průměrné zóny a dělal ze čtyř pětin mapy šedou plochu: obarvil
   59 z 259 zón, i když těch 200 zbylých hodnoty má. Mapa pak netvrdila "nevíme", ale
   "tady nic není" -- a to je jiné tvrzení.

   Kde přesně je hranice, ale není fakt o datech. Je to rozhodnutí, kolik šumu čtenář
   snese, tak ho drží posuvník: výchozí 1 % průměrné zóny (asi 130 měření, obarví ~215
   zón), nula ukáže všechno, co se vůbec dalo spočítat, a 10 % nechá jen zóny s provozem.
   Hodnota se čte vedle posuvníku v obou jednotkách, aby se vědělo, co je zrovna zapnuté.

   Žebříček se ptá "která zóna je v tom nejvyšší", a to je jiná otázka: pořadí vybírá
   extrémy, takže do prvních deseti se přes malý práh dostane zóna se 165 měřeními
   a 23 mph -- žádné letiště, jen málo dat. Tam relativní práh zůstává pevný a přísný;
   posuvník na mapě ho nehýbe, protože žebříček není o tom, co se dá ukázat.

   Všechny se měří na jmenovateli té konkrétní metriky, ne na počtu jízd -- zóna může mít
   jízd dost a měření vzdálenosti skoro žádné. Součty práh nemají žádný: sto jízd je sto
   jízd, na tom není co odhadovat. */
const RANK_SHARE = 0.001;
const rankFloor = (m) => Math.round(monthAgg(m).trips * RANK_SHARE);

// Průměr se počítá jen ze zón, které mapa kreslí: zbytkové kódy bez obrysu (264/265) do
// "průměrné zóny mapy" nepatří, jinak by práh hýbal něco, co není vidět.
const DRAWN = Object.keys(MAP.paths);
const zoneMean = (m) => memo("mean:" + m.key, () => {
  const aggs = zoneAggs(m);
  const sum = DRAWN.reduce((s, id) => s + (ZIDX.has(id) ? aggs[ZIDX.get(id)].trips : 0), 0);
  return sum / DRAWN.length;
});

let showShare = 0.01;
const showFloor = (m) => Math.round(zoneMean(m) * showShare);
// Krok posuvníku je čtvrt procenta, takže vlastní formát: `pct` má jedno desetinné místo
// a udělalo by z "0,25 %" a "0,3 %" totéž číslo. Celá procenta zůstávají bez desetin.
const nfShare = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 2 });
const shareLabel = (s) => nfShare.format(s * 100) + " %";
const solid = (a, floor) => {
  if (SPEC().additive) return a.trips > 0;
  const v = SPEC().value(a);
  return v != null && isFinite(v) && SPEC().obs(a) >= floor;
};

/* ---------- popisky ---------- */

// Čeština skloňuje, takže měsíc potřebuje dva tvary: "leden 2025" do nadpisu a
// "v lednu 2025" do věty. Zkratka z `common.js` ("led 2025") zůstává na osy grafů, kde
// je málo místa.
const MONTH_FULL = ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen", "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"];
const MONTH_IN = ["lednu", "únoru", "březnu", "dubnu", "květnu", "červnu", "červenci", "srpnu", "září", "říjnu", "listopadu", "prosinci"];
const longLabel = (m) => MONTH_FULL[m.month - 1] + " " + m.year;
const lowerLabel = (m) => MONTH_FULL[m.month - 1].toLowerCase() + " " + m.year;
const inLabel = (m) => "v " + MONTH_IN[m.month - 1] + " " + m.year;
const fmt = (v, how = "full") => (v == null || !isFinite(v) ? "—" : (SPEC()[how] || SPEC().full)(v));
// Součty jsou dlouhá čísla a čtou se zkráceně ("3,4 mil. jízd"); průměry jsou krátká
// a zkrácení by z nich ubralo přesnost i jednotku ("$18" místo "$18,04").
const disp = (v) => fmt(v, SPEC().additive ? "short" : "full");
// Pokrytí je podíl jízd, na kterých se metrika vůbec dala změřit. U součtů je to
// pojem bez obsahu -- nic se nedělí -- a dlaždice na to má vlastní větev.
const coverageOf = (a) => (SPEC().additive || !a.trips ? null : SPEC().obs(a) / a.trips);

/* ---------- graf: měsíc po měsíci ---------- */

/* Sloupce od nuly u součtů, čára u průměrů. Nula je smysluplná základna pro počet jízd
   a plocha pod ní něco znamená; u průměrné rychlosti by osa od nuly stlačila celý
   rozptyl mezi měsíci do dvou pixelů a graf by tvrdil, že se nic neděje. Čára proto osu
   od nuly nedrží a popisek pod grafem to říká. */
function yDomain(values, additive) {
  const known = values.filter((v) => v != null && isFinite(v));
  const max = Math.max(...known);
  if (additive) return [0, max * 1.08];
  const min = Math.min(...known);
  const pad = Math.max((max - min) * 0.25, max * 0.02) || 1;
  return [Math.max(0, min - pad), max + pad];
}

// Čára se láme jen přes body, které se opravdu daly spočítat. Řez bez jediného měření
// je za měsíc i za den nepravděpodobný, ale `null` v `d` atributu by rozbil celý path,
// ne jeden bod -- a graf by zmizel beze slova.
const points = (values) => values.map((v, i) => ({ v, i })).filter((p) => p.v != null && isFinite(p.v));
const polyline = (pts, x, y) => pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");

function axisTicks(svg, L, pw, y, lo, hi, steps, format) {
  for (let t = 0; t <= steps; t++) {
    const v = lo + ((hi - lo) / steps) * t;
    svg.appendChild(el("line", { x1: L, x2: L + pw, y1: y(v), y2: y(v), stroke: "var(--rule)", "stroke-width": 1 }));
    svg.appendChild(el("text", { x: L - 9, y: y(v) + 4, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "end" }, format(v)));
  }
}

function drawHistory(host) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const spec = SPEC();
  const H = 172;
  const { svg, width } = svgRoot(host, H, spec.label + " po zdrojových měsících za celý dataset");
  const L = 52, R = 8, T = 14, B = 36;
  const pw = width - L - R;
  const ph = H - T - B;
  const values = MONTHS.map((m) => spec.value(monthAgg(m)));
  const [lo, hi] = yDomain(values, spec.additive);
  const slot = pw / MONTHS.length;
  const bw = Math.max(2, Math.min(26, slot - 3));
  const y = (v) => T + ph - ((v - lo) / (hi - lo)) * ph;
  const cx = (i) => L + i * slot + slot / 2;
  const tip = tipFor(host);
  tip.hidden = true;

  axisTicks(svg, L, pw, y, lo, hi, 3, (v) => (spec.additive && v === 0 ? "0" : spec.short(v)));

  if (!spec.additive) {
    svg.appendChild(el("path", { d: polyline(points(values), cx, y), fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round" }));
  }

  const every = Math.max(1, Math.ceil(MONTHS.length / Math.max(2, Math.floor(pw / 56))));

  MONTHS.forEach((m, i) => {
    const v = values[i];
    const on = m.key === current;
    // Výběr je stav UI, ne kategorie dat: týž odstín, jen ztlumený.
    if (v != null && isFinite(v)) {
      const x = L + i * slot + (slot - bw) / 2;
      svg.appendChild(spec.additive
        ? el("path", { d: barPathUp(x, y(v), bw, ph - (y(v) - T), 3), fill: "var(--s1)", opacity: on ? 1 : 0.32 })
        : el("circle", { cx: cx(i), cy: y(v), r: on ? 5 : 3, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": on ? 2 : 0, opacity: on ? 1 : 0.55 }));
    }
    if (on) svg.appendChild(el("rect", { x: L + i * slot, y: T + ph + 3, width: slot, height: 2, fill: "var(--accent)" }));
    if (i % every === 0) {
      svg.appendChild(el("text", { x: cx(i), y: H - 12, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10, "text-anchor": "middle" }, m.key));
    }

    const a = monthAgg(m);
    const hit = el("rect", { x: L + i * slot, y: T, width: slot, height: ph, fill: "transparent", style: "cursor:pointer", role: "button", tabindex: 0 });
    hit.appendChild(el("title", {}, label(m) + ": " + fmt(v)));
    hit.addEventListener("mousemove", () => {
      tip.innerHTML = `<b>${label(m)}</b><br>${fmt(v)}<br>`
        + `<span class="r">${nf.format(a.trips)} jízd · ${usdCompact(net(a))} tržeb</span>`;
      placeTip(tip, host, (cx(i) / width) * host.clientWidth, Math.max(0, ((y(v) - 4) / H) * host.clientHeight - 64));
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    hit.addEventListener("click", () => select(m.key));
    hit.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(m.key); } });
    svg.appendChild(hit);
  });

  host.appendChild(svg);
}

/* ---------- graf: denní křivka ---------- */

function drawDaily(host, m) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const spec = SPEC();
  const days = dayAggs(m);
  const dates = m.daily.date;
  const H = 250;
  const { svg, width } = svgRoot(host, H, spec.label + " po dnech za " + label(m));
  const L = 58, R = 14, T = 18, B = 30;
  const pw = width - L - R;
  const ph = H - T - B;
  const values = days.map(spec.value);
  const [lo, hi] = yDomain(values, spec.additive);
  const x = (i) => L + (days.length === 1 ? pw / 2 : (i / (days.length - 1)) * pw);
  const y = (v) => T + ph - ((v - lo) / (hi - lo)) * ph;
  const step = pw / Math.max(1, days.length - 1);

  dates.forEach((date, i) => {
    if (!isWeekend(date)) return;
    const x0 = Math.max(L, x(i) - step / 2);
    const x1 = Math.min(L + pw, x(i) + step / 2);
    svg.appendChild(el("rect", { x: x0, y: T, width: x1 - x0, height: ph, fill: "var(--band)" }));
  });

  axisTicks(svg, L, pw, y, lo, hi, 4, (v) => (spec.additive && v === 0 ? "0" : spec.short(v)));

  const pts = points(values);
  const line = polyline(pts, x, y);
  // Plocha pod křivkou má význam jen tam, kde má význam nula: součet přes dny je zase
  // součet, průměr přes dny není nic.
  if (spec.additive) {
    svg.appendChild(el("path", { d: `${line} L${x(pts[pts.length - 1].i).toFixed(1)},${y(lo)} L${x(pts[0].i).toFixed(1)},${y(lo)} Z`, fill: "var(--s2-soft)" }));
  }
  svg.appendChild(el("path", { d: line, fill: "none", stroke: "var(--s2)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  const every = Math.max(2, Math.ceil(days.length / Math.max(2, Math.floor(pw / 64))));
  dates.forEach((date, i) => {
    if (i % every !== 0 || i > days.length - 2) return;
    svg.appendChild(el("text", { x: x(i), y: H - 9, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "middle" }, dayLabel(date)));
  });

  // Popiskem se označí jen vrchol; číslo u každého bodu si nikdo nepřečte.
  const peak = pts.reduce((best, p) => (p.v > values[best] ? p.i : best), pts[0].i);
  svg.appendChild(el("circle", { cx: x(peak), cy: y(values[peak]), r: 5, fill: "var(--s2)", stroke: "var(--surface)", "stroke-width": 2 }));
  const anchor = peak > days.length - 5 ? "end" : peak < 4 ? "start" : "middle";
  svg.appendChild(el("text", { x: x(peak), y: y(values[peak]) - 12, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 11, "font-weight": 600, "text-anchor": anchor }, fmt(values[peak], "short")));

  const cross = el("line", { x1: 0, x2: 0, y1: T, y2: T + ph, stroke: "var(--rule-strong)", "stroke-width": 1, opacity: 0 });
  const knob = el("circle", { r: 5, fill: "var(--s2)", stroke: "var(--surface)", "stroke-width": 2, opacity: 0 });
  svg.append(cross, knob);

  const tip = tipFor(host);
  tip.hidden = true;
  const hit = el("rect", { x: L, y: T, width: pw, height: ph, fill: "transparent" });
  svg.appendChild(hit);

  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * width;
    const i = Math.max(0, Math.min(days.length - 1, Math.round(((px - L) / pw) * (days.length - 1))));
    const a = days[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("opacity", 1);
    knob.setAttribute("cx", x(i)); knob.setAttribute("cy", y(values[i])); knob.setAttribute("opacity", 1);
    tip.innerHTML = `<b>${dayLabel(dates[i])}</b>${isWeekend(dates[i]) ? " <span class='r'>víkend</span>" : ""}<br>`
      + `${fmt(values[i])}<br><span class="r">${nf.format(a.trips)} jízd · tržby ${usdCompact(net(a))}</span>`;
    placeTip(tip, host, (x(i) / width) * host.clientWidth, Math.max(4, (y(values[i]) / H) * host.clientHeight - 84));
  });

  hit.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.setAttribute("opacity", 0);
    knob.setAttribute("opacity", 0);
  });

  host.appendChild(svg);
  buildDailyTable(m, days, values);
}

/* ---------- mapa: zóny nástupu ---------- */

const BUCKETS = 6;

// Kvantily, ne stejně široká pásma. Jízdy jsou rozdělené tak, že Midtown je řádově nad
// zbytkem -- lineární pásma by obarvila celé město nejsvětlejším odstínem a mapa by
// tvrdila, že se nikde nic neděje.
function quantileEdges(values, n) {
  const sorted = [...values].sort((a, b) => a - b);
  const edges = [];
  for (let i = 1; i < n; i++) edges.push(sorted[Math.floor((i * sorted.length) / n)]);
  return edges;
}

const bucketOf = (v, edges) => edges.reduce((acc, e) => acc + (v >= e ? 1 : 0), 0);

function drawMap(host, m) {
  const spec = SPEC();
  const aggs = zoneAggs(m);
  const floor = showFloor(m);
  host.querySelectorAll("svg").forEach((n) => n.remove());
  syncThresh(m, floor);

  // Rampa se mění po skupinách, ne po metrikách: odstín nese "čteš součet / průměr na
  // jízdu / poměr dvou průměrů", což je ta informace, kvůli které se jinak musí číst
  // titulek. Uvnitř skupiny zůstává stejný, aby šly dvě příbuzné metriky porovnat.
  // Sada `--m1..--m6` je pořád sekvenční, jednoodstínová -- mění se jen který odstín.
  document.documentElement.dataset.ramp = spec.group;

  /* Dvě množiny, protože práh a barva jsou dvě různá rozhodnutí.

     `known` je všechno, co se vůbec dalo spočítat, a určuje pásma. `shown` je to nad
     prahem a určuje, co dostane barvu. Kdyby pásma stála na `shown`, tažení posuvníku by
     přebarvovalo i zóny, které v mapě zůstávají: hranice by se posunuly, Midtown by
     přeskočil o odstín a legenda pod rukou měnila čísla. Odstín by pak neznamenal "tolik
     mil na jízdu", ale "tolik mil na jízdu při téhle poloze posuvníku".

     Platí se za to tím, že pásma zná i hodnoty, kterým stránka nevěří. Kvantil je ale
     řadová statistika -- hrstka nevěrohodných zón hranicemi pohne o kousek, nepřevrátí
     je -- a šedá pořád říká, které to jsou. Stabilní význam barvy je za to lepší koupě. */
  const known = new Map();
  const shown = new Map();
  for (const id of DRAWN) {
    const i = ZIDX.get(id);
    if (i === undefined) continue;
    if (solid(aggs[i], 0)) known.set(id, spec.value(aggs[i]));
    if (solid(aggs[i], floor)) shown.set(id, spec.value(aggs[i]));
  }
  const edges = quantileEdges([...known.values()], BUCKETS);
  const lows = [Math.min(...known.values()), ...edges];

  const svg = el("svg", {
    viewBox: `0 0 ${MAP.width} ${MAP.height}`,
    role: "img",
    "aria-label": spec.label + " podle zóny nástupu za " + label(m),
  });
  // Bublina přežívá překreslení (visí na hostu, ne na SVG), takže po přepnutí metriky
  // je jinak vidět dál -- s čísly, co jsou napůl z předchozího pohledu.
  const tip = tipFor(host);
  tip.hidden = true;
  // Zvýraznění je jedna cesta navrch, ne silnější obrys na místě: SVG nemá z-index,
  // takže obrys zóny kreslené dřív by zmizel pod jejími sousedy.
  const highlight = el("path", { class: "hl", d: "" });

  for (const [id, d] of Object.entries(MAP.paths)) {
    const i = ZIDX.get(id);
    const a = i === undefined ? null : aggs[i];
    const lit = shown.has(id);
    // Jméno z curated (tam ho pipeline joinovala z lookupu), a když zóna v curated
    // není, tak aspoň jméno ze shapefilu.
    const named = i === undefined ? MAP.names[id] || ["zóna " + id, ""] : [MAP.zone[i], MAP.borough[i]];

    // Tři různé stavy, ne dva: v tomhle měsíci žádná jízda, jízdy ale málo měření na
    // to, aby průměr něco znamenal, a číslo. Prostřední stav je jediný, který přibyl
    // tím, že mapa přestala ukazovat celou historii -- za 29 měsíců se hrstka jízd
    // nasčítá skoro všude, za jeden měsíc ne.
    const missing = !a || !a.trips
      ? "v tomhle měsíci žádné jízdy"
      : !spec.obs(a)
        ? "jízdy ano, měření téhle metriky žádné"
        : "málo měření na průměr (" + nf.format(spec.obs(a)) + " z " + nf.format(floor) + ")";

    // Bublina nese celý profil zóny, ne jen obarvenou metriku, a tučně je v něm to, čím
    // je zrovna obarveno. Přepínač tak nemění, co se dá přečíst -- jen kam se dívat.
    const cell = (key) => {
      const s = METRICS[key];
      const v = a ? s.value(a) : null;
      const text = v == null || !isFinite(v) ? "—" : (s.brief || s.full)(v);
      return key === metric ? `<b>${text}</b>` : text;
    };
    const shape = el("path", { d, fill: lit ? `var(--m${bucketOf(shown.get(id), edges) + 1})` : "var(--m0)" });
    shape.appendChild(el("title", {}, named[0] + ": " + (lit ? spec.full(shown.get(id)) : missing)));

    // Bez tabindex: 263 zón by znamenalo 263 zastávek tabulátoru mezi grafy. Táž čísla
    // jsou v tabulkách níž, mapa je jejich obrázek.
    shape.addEventListener("mousemove", (event) => {
      highlight.setAttribute("d", d);
      const box = host.getBoundingClientRect();
      tip.innerHTML = a
        ? [
            `<b>${named[0]}</b> · ${named[1]}`,
            `${cell("trips")} · ${cell("revenue")}`,
            `<span class="r">hrubě ${usdCompact(a.revenue)} · storna ${usdCompact(a.refunds)}</span>`,
            `<span class="r">${cell("revenue_per_trip")} / jízdu · ${cell("avg_fare_usd")} jízdné`
              + ` · ${cell("avg_distance_mi")} · ${cell("avg_duration_min")}</span>`,
            `<span class="r">${cell("speed_mph")} · ${cell("fare_per_mile")}</span>`,
            `<span class="r">pokrytí ${orDash(div(a.dist_obs, a.trips), pct)} vzdálenost`
              + ` · ${orDash(div(a.dur_obs, a.trips), pct)} doba jízdy</span>`,
          ].concat(lit ? [] : [`<span class="r">${missing}</span>`]).join("<br>")
        : `<b>${named[0]}</b> · ${named[1]}<br><span class="r">v datech žádné jízdy</span>`;
      placeTip(tip, host, event.clientX - box.left, event.clientY - box.top - 104);
    });
    svg.appendChild(shape);
  }

  svg.appendChild(highlight);
  svg.addEventListener("mouseleave", () => {
    tip.hidden = true;
    highlight.setAttribute("d", "");
  });
  host.appendChild(svg);

  const grey = spec.additive
    ? "žádná jízda v tomhle měsíci"
    : floor > 0
      ? "žádné jízdy nebo míň než " + nf.format(floor) + " měření"
      : "žádné měření";
  document.getElementById("map-legend").innerHTML =
    `<div><div class="steps">${lows.map((_, i) => `<i style="background:var(--m${i + 1})"></i>`).join("")}</div>`
    + `<div class="edges">${lows.map((v) => `<span>${spec.short(v)}</span>`).join("")}</div></div>`
    + `<span class="nodata"><i class="swatch" style="background:var(--m0)"></i>${grey}</span>`;

  const off = OFF_MAP.reduce((sum, i) => sum + aggs[i].trips, 0);
  const drawn = Object.keys(MAP.paths).length;
  document.getElementById("map-foot").textContent =
    (spec.additive
      ? `${shown.size} z ${drawn} zón má ${inLabel(m)} aspoň jednu jízdu`
      : `${shown.size} z ${drawn} zón má ${inLabel(m)} aspoň ${nf.format(floor)} měření na průměr`
        + ` (práh ${shareLabel(showShare)} průměrné zóny, ta má ${nf.format(Math.round(zoneMean(m)))} jízd)`)
    + " · " + nf.format(off) + " jízd spadá do " + OFF_MAP.length
    + " kódů, které na žádné mapě obrys nemají (neznámé, mimo NYC)";
}

/* ---------- tabulky ---------- */

function buildDailyTable(m, days, values) {
  const spec = SPEC();
  // U součtů by sloupec s metrikou jen zopakoval ten vedle sebe.
  const own = !spec.additive;
  const head = ["Den"].concat(own ? [spec.label] : [], ["Jízdy", "Tržby", "Storna"]);
  document.getElementById("daily-table").innerHTML =
    "<thead><tr>" + head.map((h, i) => `<th${i ? " class='num'" : ""}>${h}</th>`).join("") + "</tr></thead><tbody>"
    + m.daily.date.map((date, i) => {
      const a = days[i];
      return `<tr><td class="mono">${date}${isWeekend(date) ? " · víkend" : ""}</td>`
        + (own ? `<td class="num">${fmt(values[i])}</td>` : "")
        + `<td class="num">${nf.format(a.trips)}</td>`
        + `<td class="num">${usd(net(a))}</td>`
        // Znaménko před značku měny, ne za ni: "$-90 459" se čte jako rozbitý formát.
        + `<td class="num dim">−${usd(-a.refunds)}</td></tr>`;
    }).join("") + "</tbody>";
}

/* Průměr vedle mediánu je exponát o kvalitě dat, ne o vybrané metrice: ukazuje, že
   průměr táhne nahoru hrstka nevěrohodných řádků a medián zůstává na místě. Curated
   nese medián vzdálenosti i doby jízdy, takže tabulka umí obojí a přepne se podle toho,
   o čem se zrovna čte; pro jízdné medián neexistuje a zůstane vzdálenost. Řadí se
   pořád podle jízd -- je to tvrzení o největších zónách, ne žebříček. */
const MEDIAN_VIEWS = {
  median_dist: { title: "vzdálenosti", unit: "mi", value: avgDist, obs: (a) => a.dist_obs, fmt: (v) => nf2.format(v) },
  median_dur: { title: "doby jízdy", unit: "min", value: avgDur, obs: (a) => a.dur_obs, fmt: (v) => nf1.format(v) },
};

function buildMeanMedian(m) {
  const key = SPEC().median || "median_dist";
  const view = MEDIAN_VIEWS[key];
  const aggs = zoneAggs(m);
  const rows = IDS.map((_, i) => i).sort((a, b) => aggs[b].trips - aggs[a].trips).slice(0, 12);

  document.getElementById("mm-title").textContent = "Průměr vedle mediánu — " + view.title;
  document.getElementById("mm-cap").innerHTML =
    `Dvanáct nejvytíženějších zón ${inLabel(m)}. Extrémní řádky táhnou průměr nahoru,`
    + ` medián zůstává. Pokrytí je podíl jízd, na kterých se průměr dal změřit. Medián je`
    + ` medián denních mediánů: mediány se přes partition nesčítají.`;
  document.getElementById("mm-head").innerHTML =
    "<tr><th>Zóna</th><th>Obvod</th><th class='num'>Jízdy</th>"
    + `<th class='num'>Průměr (${view.unit})</th><th class='num'>Medián (${view.unit})</th>`
    + "<th class='num'>Pokrytí</th><th class='num'>Prům. jízdné</th></tr>";

  document.getElementById("ztable").innerHTML = rows.map((i) => {
    const a = aggs[i];
    const cov = div(view.obs(a), a.trips);
    const low = cov != null && cov < 0.9;
    return `<tr>
      <td>${MAP.zone[i]}</td>
      <td class="dim">${MAP.borough[i]}</td>
      <td class="num">${nf.format(a.trips)}</td>
      <td class="num">${orDash(view.value(a), view.fmt)}</td>
      <td class="num">${orDash(m.zones[key][i], view.fmt)}</td>
      <td class="num" ${low ? 'style="color:var(--s2)"' : ""}>${orDash(cov, (v) => nf1.format(v * 100) + " %")}</td>
      <td class="num">${orDash(avgFare(a), (v) => "$" + nf2.format(v))}</td>
    </tr>`;
  }).join("");
}

/* ---------- žebříček zón ---------- */

function drawZones(m) {
  const spec = SPEC();
  const aggs = zoneAggs(m);
  const floor = rankFloor(m);
  const ranked = IDS.map((_, i) => i)
    .filter((i) => solid(aggs[i], floor))
    .sort((a, b) => spec.value(aggs[b]) - spec.value(aggs[a]))
    .slice(0, 10);

  document.getElementById("zones-title").textContent = "Žebříček zón · " + spec.label;
  document.getElementById("zones-cap").textContent = spec.additive
    ? "Deset nejvytíženějších zón nástupu " + inLabel(m) + ", podle zveřejněných jízd."
    : "Za " + lowerLabel(m) + ", jen zóny s aspoň 0,1 % jízd měsíce (≥ " + nf.format(floor)
      + "). Přísnější práh než mapa: pořadí vybírá extrémy, a ty vyhraje zóna se stovkou jízd.";

  drawBars(document.getElementById("zones"), ranked.map((i) => {
    const a = aggs[i];
    return {
      label: MAP.zone[i],
      full: MAP.zone[i],
      value: spec.value(a),
      display: disp(spec.value(a)),
      tip: `${fmt(spec.value(a))} · ${MAP.borough[i]}<br>`
        + `<span class="r">${nf.format(a.trips)} jízd · tržby ${usdCompact(net(a))}</span>`,
    };
  }), { labelW: 170, valueW: 92, fill: "var(--s3)", title: spec.label + " podle zóny nástupu" });
}

/* ---------- dlaždice vybraného měsíce ---------- */

/* Čtyři dlaždice, všechny o vybrané metrice: kolik jí je, jak je to proti celé historii,
   z čeho to stojí (na den u součtu, pokrytí u průměru) a kde je jí nejvíc.

   Součty se srovnávají na den, ne na měsíc. Únor má o desetinu dní míň než leden, takže
   měsíční součty by kreslily propad tam, kde je jen kratší kalendář. Průměry žádnou
   normalizaci nepotřebují -- délka měsíce se v nich vykrátí sama. */
function buildTilesFor(m) {
  const spec = SPEC();
  const a = monthAgg(m);
  const days = m.daily.date.length;
  const value = spec.value(a);
  const aggs = zoneAggs(m);
  // Dlaždice "nejvyšší zóna" je žebříček o jednom řádku, takže bere jeho práh, ne ten
  // mapový: vítěz vybraný přes 100 měření je vítěz vybraný z šumu.
  const floor = rankFloor(m);

  // Základna je celá historie spočítaná jako jeden řez, ne průměr měsíčních průměrů:
  // ten by dal každému měsíci stejnou váhu bez ohledu na to, kolik se v něm jezdilo.
  const base = spec.additive ? spec.value(HISTORY) / TOTAL_DAYS : spec.value(HISTORY);
  const mine = spec.additive ? value / days : value;

  const best = IDS.map((_, i) => i)
    .filter((i) => solid(aggs[i], floor))
    .sort((x, y) => spec.value(aggs[y]) - spec.value(aggs[x]))[0];

  const cover = coverageOf(a);
  const tiles = [
    { k: spec.label, v: disp(value), s: "celá historie " + disp(spec.value(HISTORY)) },
    {
      k: "Proti celé historii",
      v: delta(mine, base) || "—",
      s: spec.additive ? "srovnáno na den, aby krátký únor nebyl propad" : "proti váženému průměru celé historie",
    },
    spec.additive
      ? { k: "Na den", v: disp(value / days), s: plural(days, "den", "dny", "dní") + " v měsíci" }
      : {
          k: "Pokrytí",
          v: orDash(cover, pct),
          // Tržba na jízdu má jmenovatel, který nic neztrácí -- každý zveřejněný řádek
          // nese celkovou částku. Sto procent je tam tvrzení, ne shoda náhod.
          s: cover === 1 ? "jmenovatel je přesný, ne odhadnutý" : "podíl jízd, na kterých se to dalo změřit",
        },
    best === undefined
      ? { k: spec.peak, v: "—", s: "žádná zóna nad prahem", name: true }
      : {
          k: spec.peak,
          v: MAP.zone[best],
          s: spec.additive
            ? pct(spec.value(aggs[best]) / value) + " z měsíce · " + disp(spec.value(aggs[best]))
            : disp(spec.value(aggs[best])) + " · " + nf.format(aggs[best].trips) + " jízd",
          name: true,
        },
  ];
  buildTiles(tiles);

  document.getElementById("kpis-cap").innerHTML =
    longLabel(m) + " proti celé historii (" + SPAN + "). Tržby jsou po odečtení storen,"
    + " která byla " + inLabel(m) + " " + pct(-a.refunds / a.revenue) + " hrubého objemu.";
}

/* ---------- statické části ---------- */

function buildHeader() {
  buildEyebrow();

  const f = DATA.freshness;
  buildChips([
    { dot: "good", text: plural(MONTHS.length, "měsíc", "měsíce", "měsíců") + " · " + SPAN },
    { dot: "info", text: nf1.format(HISTORY.trips / 1e6) + " mil. jízd · " + usdBig(net(HISTORY)) + " jízdného" },
    { dot: "good", text: "nejnovější měsíc město zveřejnilo před " + f.source_age_days + " dny (" + f.source_newest + ")" },
    { dot: "info", text: "postaveno " + DATA.generated_at },
  ]);

  document.getElementById("foot").textContent =
    "Postaveno " + DATA.generated_at + " z " + plural(MONTHS.length, "souboru Parquet", "souborů Parquet")
    + " pokrývajících " + SPAN + " · " + plural(IDS.length, "zóna", "zóny", "zón") + " nástupu.";
}

/* Posuvník prahu. Odpovídá jen za mapu, tak se s ní překresluje jen ona -- 263 cest je
   levné, ale žebříček ani dlaždice se tím nehýbou a překreslovat je by bylo zavádějící.

   U součtů je práh pojem bez obsahu (nic se nedělí), takže se řádek skryje, a jeho
   hodnota se drží: přepnutí na jízdy a zpátky nastavení nezruší. */
function buildThreshold() {
  const input = document.getElementById("thresh");
  input.value = String(showShare * 100);
  input.addEventListener("input", () => {
    showShare = Number(input.value) / 100;
    renderMap(month());
  });
}

function syncThresh(m, floor) {
  document.getElementById("map-thresh").hidden = SPEC().additive;
  document.getElementById("thresh-out").textContent =
    shareLabel(showShare) + " průměrné zóny · " + (floor > 0 ? "od " + nf.format(floor) + " měření" : "bez prahu");
}

/* Přepínač metriky: viditelná řada, ne `select`. Osm možností se do řady vejde a je na
   první pohled vidět, že se stránka dá přepnout -- rozbalovátko to netvrdí. Skupiny
   odděluje mezera: součty, průměry na jízdu, poměry mezi průměry. */
function buildMetricPicker() {
  const host = document.getElementById("metric-picker");
  let group = null;
  host.innerHTML = Object.entries(METRICS).map(([key, spec]) => {
    const sep = group && spec.group !== group ? '<span class="sep" aria-hidden="true"></span>' : "";
    group = spec.group;
    return sep + `<button class="mbtn" type="button" data-metric="${key}" aria-pressed="false">${spec.label}</button>`;
  }).join("");

  host.querySelectorAll(".mbtn").forEach((btn) => {
    btn.addEventListener("click", () => {
      metric = btn.dataset.metric;
      render();
    });
  });
}

/* ---------- render ---------- */

// Vlastní funkce, protože mapa se překresluje i sama: posuvník prahu hýbe jen jí.
function renderMap(m) {
  const spec = SPEC();
  document.getElementById("map-title").textContent = spec.label + " podle zóny nástupu · " + lowerLabel(m);
  document.getElementById("map-cap").textContent = spec.additive
    ? "Barva je pořadí: šest pásem, v každém stejný počet zón. Najetím na zónu se zobrazí celý její profil za tenhle měsíc."
    : "Barva je pořadí: šest pásem, v každém stejný počet spočítatelných zón. Šedé mají za tenhle měsíc míň"
      + " měření, než žádá posuvník. Pásma na posuvníku nezávisí, takže tažení mapu nepřebarvuje, jen posílá"
      + " zóny do šedé a zpátky. Žebříček zón níž má práh pevný a vyšší: pořadí vybírá extrémy.";
  drawMap(document.getElementById("zonemap"), m);
}

// Překresluje se všechno včetně mapy: měsíc i metrika s ní hýbou a 261 cest je levnější
// než dvě cesty kódu, které se mají udržet v souladu.
function render() {
  const m = month();
  const spec = SPEC();
  syncPicker();
  document.querySelectorAll("#metric-picker .mbtn").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.metric === metric));
  });

  document.getElementById("view-title").textContent = spec.label + " · " + longLabel(m);
  document.getElementById("view-sub").textContent =
    plural(Object.keys(METRICS).length, "metrika", "metriky", "metrik")
    + " · " + plural(MONTHS.length, "měsíc", "měsíce", "měsíců");
  document.getElementById("view-about").innerHTML = spec.about;

  buildTilesFor(m);

  renderMap(m);

  document.getElementById("history-title").textContent = spec.label + " měsíc po měsíci";
  drawHistory(document.getElementById("history"));

  document.getElementById("daily-title").textContent = spec.label + " den po dni · " + lowerLabel(m);
  drawDaily(document.getElementById("daily"), m);

  drawZones(m);
  buildMeanMedian(m);
}

buildHeader();
buildPicker();
buildMetricPicker();
buildThreshold();
render();
watchResize();
