/* Metodická stránka: proč jsou čísla taková, jaká jsou. Argumenty samotné jsou pevný
   text v `method.html` -- tenhle soubor dopočítává jen to, co má být měřené: kolik
   toho která pravidla za celou historii chytila a jakou rezervu mají prahy běhu.

   Bere se vždy nejnovější manifest každé partition. Starší popisují data, která už
   v curated nejsou, a sečíst je dohromady by vstupní řádky nafouklo o přepočty. */

// Manifest je zdroj čísel i tady, ale na rozdíl od provozní stránky nezajímá "co dělal
// tenhle běh", nýbrž "co dělá pravidlo pořád".
const LATEST = MONTHS.map((m) => m.runs[0]);
const APPLIED = LATEST[LATEST.length - 1].thresholds_applied || {};
const INPUT = LATEST.reduce((a, r) => a + r.rows.input, 0);

const sum = (pick) => LATEST.reduce((a, r) => a + (pick(r) || 0), 0);

/* ---------- chart: rezerva prahů ---------- */

// Vlastní graf, ne `drawBars`: tady nejde o porovnání pravidel mezi sebou, ale každého
// zvlášť proti jeho vlastnímu prahu. Dráha = práh, výplň = nejhorší naměřený měsíc,
// takže čtyři různé jednotky (procenta karantény, procenta nulování, změna objemu) jdou
// pod sebe a pořád znamenají totéž: kolik z povoleného se vyčerpalo.
function drawGates(host, gates) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const rowH = 14, gap = 30, T = 16;
  const H = gates.length * (rowH + gap) - gap + T + 6;
  const { svg, width } = svgRoot(host, H, "Nejhorší naměřený měsíc proti každému prahu běhu");
  const labelW = Math.min(190, width * 0.4);
  const valueW = 66;
  const pw = Math.max(24, width - labelW - valueW - 12);
  const tip = tipFor(host);

  gates.forEach((g, i) => {
    const y = T + i * (rowH + gap);
    const used = Math.min(1, g.worst / g.limit);

    svg.appendChild(el("text", { x: 0, y: y - 6, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11 }, g.label));
    svg.appendChild(el("path", { d: barPath(labelW, y, pw, rowH, 4), fill: "var(--m0)" }));
    svg.appendChild(el("path", { d: barPath(labelW, y, Math.max(2, used * pw), rowH, 4), fill: "var(--s1)" }));
    // Práh je konec dráhy, ať je vidět, že zbytek není prázdné místo v grafu, ale povolená rezerva.
    svg.appendChild(el("line", { x1: labelW + pw, x2: labelW + pw, y1: y - 3, y2: y + rowH + 3, stroke: "var(--ink-3)", "stroke-width": 1 }));
    svg.appendChild(el("text", { x: labelW + pw, y: y + rowH + 16, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "end" }, "padá nad " + g.limitText));
    svg.appendChild(el("text", { x: labelW + pw + 8, y: y + rowH - 3, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 11, "font-variant-numeric": "tabular-nums" }, g.worstText));

    const hit = el("rect", { x: 0, y: y - gap / 2, width, height: rowH + gap, fill: "transparent" });
    hit.appendChild(el("title", {}, g.label + ": " + g.worstText + " z " + g.limitText));
    hit.addEventListener("mousemove", (ev) => {
      const box = svg.getBoundingClientRect();
      tip.innerHTML = `<b>${g.label}</b><br>naměřeno ${g.worstText} · padá nad ${g.limitText}`
        + `<br><span class="r">vyčerpáno ${pct(used)} rezervy · ${g.where}</span>`;
      placeTip(tip, host, ev.clientX - box.left, Math.max(0, ((y - 10) / H) * host.clientHeight - 52));
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    svg.appendChild(hit);
  });

  host.appendChild(svg);
}

// Nejhorší naměřený měsíc, ne průměr: práh se posuzuje podle toho, jak blízko se k němu
// data kdy dostala.
function worstOf(pick) {
  let best = { value: 0, key: MONTHS[0].key };
  MONTHS.forEach((m, i) => {
    const value = pick(LATEST[i], m, i);
    if (value != null && value > best.value) best = { value, key: m.key };
  });
  return best;
}

function gates() {
  const reject = worstOf((r) => r.rows.rejected / r.rows.input);
  const reversal = worstOf((r) => (r.rows.reversed || 0) / r.rows.input);
  const dist = worstOf((r) => (r.nulled.trip_distance || 0) / r.rows.input);
  const dur = worstOf((r) => (r.nulled.duration_min || 0) / r.rows.input);
  // Objem se porovnává s předchozím měsícem, takže první měsíc žádnou změnu nemá.
  const delta = worstOf((r, m, i) => (i === 0 ? null : Math.abs(r.rows.input / LATEST[i - 1].rows.input - 1)));

  const asPct = (g, label, limit) => ({
    label,
    worst: g.value,
    limit,
    worstText: pct(g.value),
    limitText: pct(limit),
    where: "nejhorší měsíc: " + g.key,
  });

  const rows = [
    asPct(reject, "Řádky v karanténě", CFG.max_reject_ratio),
    asPct(reversal, "Stornované řádky", CFG.max_reversal_ratio),
    asPct(dist, "Vynulovaná vzdálenost", CFG.max_null_ratio_distance),
    asPct(dur, "Vynulovaná doba jízdy", CFG.max_null_ratio_duration),
  ];
  if (MONTHS.length > 1) {
    rows.push({
      label: "Objem proti předchozímu měsíci",
      worst: delta.value,
      limit: CFG.max_volume_delta,
      worstText: "±" + pct(delta.value),
      limitText: "±" + pct(CFG.max_volume_delta),
      where: "největší výkyv: " + delta.key,
    });
  }
  return rows;
}

/* ---------- tabulka pravidel ---------- */

// Práh se tiskne z toho, co bylo v manifestu, ne z konfigurace: kdyby se práh změnil,
// popisek pravidla se změní až s během, který podle nového jel.
const THRESHOLD_TEXT = {
  implausible_distance: () => (APPLIED.max_distance_mi ? "> " + nf.format(APPLIED.max_distance_mi) + " mi, jen záchranná brzda" : "—"),
  impossible_speed: () => (APPLIED.max_speed_mph ? "> " + nf.format(APPLIED.max_speed_mph) + " mph odvozeně" : "—"),
  duration_over_limit: () => (APPLIED.max_duration_min ? "> " + APPLIED.max_duration_min / 60 + " h" : "—"),
  nonpositive_duration: () => "≤ 0 min",
  zero_distance: () => "≤ 0 mi",
  negative_fare: () => "&lt; $0",
  reversal: () => "&lt; $0",
  zero_total: () => "= $0",
  out_of_month: () => "mimo zdrojový měsíc",
};

function buildLedger() {
  const totals = {};
  LATEST.forEach((r) => {
    for (const [name, count] of Object.entries(r.rules)) totals[name] = (totals[name] || 0) + count;
  });

  const rows = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  document.getElementById("ledger").innerHTML = rows.map(([name, count]) => {
    const wholeRow = ROW_EFFECTS.includes(RULE_EFFECT[name]);
    return `<tr>
      <td class="wrap">${ruleLabel(name, APPLIED)}<br><span class="mono" style="color:var(--ink-3)">${name}</span></td>
      <td class="mono">${(THRESHOLD_TEXT[name] || (() => "—"))()}</td>
      <td class="wrap"><span class="tag">${RULE_EFFECT[name] || "—"}</span></td>
      <td class="mono">${wholeRow ? "celý řádek" : RULE_FIELD[name] || "—"}</td>
      <td class="num">${nf.format(count)}</td>
      <td class="num" style="color:var(--ink-2)">${nf2.format((count / INPUT) * 100)} %</td>
    </tr>`;
  }).join("");

  document.getElementById("ledger-cap").textContent =
    "Dotčené řádky za " + SPAN + ", počítáno z nejnovějšího běhu každé z "
    + plural(MONTHS.length, "partition") + " — celkem " + nf.format(INPUT) + " zdrojových řádků.";
}

/* ---------- statické části ---------- */

function specRows(hostId, rows) {
  document.getElementById(hostId).innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function buildHeader() {
  buildEyebrow();

  buildChips([
    { dot: "good", text: "prahy změřené na " + SPAN + ", ne odhadnuté" },
    { dot: "good", text: "každý běh zapisuje prahy, podle kterých jel" },
    { dot: "info", text: "nic se nemaže · odmítnuté řádky zůstávají s důvodem" },
  ]);

  const ruleNames = new Set(LATEST.flatMap((r) => Object.keys(r.rules)));
  const wholeRow = [...ruleNames].filter((n) => ROW_EFFECTS.includes(RULE_EFFECT[n])).length;
  const rejected = sum((r) => r.rows.rejected);
  const reversed = sum((r) => r.rows.reversed || 0);
  const nulled = sum((r) => Object.values(r.nulled).reduce((a, v) => a + v, 0));

  buildTiles([
    { k: "Platná pravidla", v: nf.format(ruleNames.size), s: wholeRow + " bere celý řádek · " + (ruleNames.size - wholeRow) + " odmítá jedno pole" },
    { k: "Posouzené zdrojové řádky", v: nf1.format(INPUT / 1e6) + " mil.", s: "každý řádek období " + SPAN + " jimi prošel" },
    { k: "Řádky v karanténě", v: pct(rejected / INPUT), s: nf.format(rejected) + " řádků, každý se svým důvodem" },
    { k: "Stornované řádky", v: pct(reversed / INPUT), s: nf.format(reversed) + " protizápisů, peníze jdou do refunds_usd" },
    { k: "Vynulovaná pole", v: nf.format(nulled), s: "hodnoty zahozeny, řádky i peníze zůstávají" },
  ]);

  specRows("spec-sev", [
    ["Chybí povinný sloupec", "běh padá před transformací"],
    ["Špatný typ povinného sloupce", "běh padá před transformací"],
    ["Nový sloupec ve zdroji", "zapsáno do manifestu"],
    ["Zmizel volitelný sloupec", "zapsáno do manifestu"],
    ["Žádná účtenka (celkem ≤ $0)", "řádek do karantény"],
    ["Nevěrohodné měření", "vynuluje se jen to pole"],
    ["Překročený práh", "běh padá, nic se nezveřejní"],
  ]);

  specRows("spec-keep", [
    ["Odmítnuté řádky", "rejects/…/rejects.parquet"],
    ["Důvod odmítnutí", "u každého řádku"],
    ["Objem v karanténě", "refunds_usd"],
    ["Jmenovatel každého průměru", "sloupce *_obs"],
    ["Prahy běhu", "_runs/&lt;run_id&gt;.json"],
    ["Identita zdroje", "ETag + sha256"],
    ["Schéma, jak bylo viděno", "počet sloupců + drift"],
  ]);

  document.getElementById("foot").textContent =
    "Spočítáno " + DATA.generated_at + " z nejnovějšího manifestu "
    + plural(MONTHS.length, "partition") + " pokrývajících " + SPAN + ".";
}

/* ---------- render ---------- */

function render() {
  drawGates(document.getElementById("gates"), gates());
}

buildHeader();
buildLedger();
render();
watchResize();
