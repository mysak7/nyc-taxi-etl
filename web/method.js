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
  const { svg, width } = svgRoot(host, H, "Worst month measured against each run threshold");
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
    svg.appendChild(el("text", { x: labelW + pw, y: y + rowH + 16, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "end" }, "fails over " + g.limitText));
    svg.appendChild(el("text", { x: labelW + pw + 8, y: y + rowH - 3, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 11, "font-variant-numeric": "tabular-nums" }, g.worstText));

    const hit = el("rect", { x: 0, y: y - gap / 2, width, height: rowH + gap, fill: "transparent" });
    hit.appendChild(el("title", {}, g.label + ": " + g.worstText + " of " + g.limitText));
    hit.addEventListener("mousemove", (ev) => {
      const box = svg.getBoundingClientRect();
      tip.innerHTML = `<b>${g.label}</b><br>${g.worstText} measured · fails over ${g.limitText}`
        + `<br><span class="r">${pct(used)} of the allowance used · ${g.where}</span>`;
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
    where: "worst month: " + g.key,
  });

  const rows = [
    asPct(reject, "Rows quarantined", CFG.max_reject_ratio),
    asPct(dist, "Distance nulled", CFG.max_null_ratio_distance),
    asPct(dur, "Duration nulled", CFG.max_null_ratio_duration),
  ];
  if (MONTHS.length > 1) {
    rows.push({
      label: "Volume vs. previous month",
      worst: delta.value,
      limit: CFG.max_volume_delta,
      worstText: "±" + pct(delta.value),
      limitText: "±" + pct(CFG.max_volume_delta),
      where: "largest swing: " + delta.key,
    });
  }
  return rows;
}

/* ---------- tabulka pravidel ---------- */

// Práh se tiskne z toho, co bylo v manifestu, ne z konfigurace: kdyby se práh změnil,
// popisek pravidla se změní až s během, který podle nového jel.
const THRESHOLD_TEXT = {
  implausible_distance: () => (APPLIED.max_distance_mi ? "> " + nf.format(APPLIED.max_distance_mi) + " mi, backstop only" : "—"),
  impossible_speed: () => (APPLIED.max_speed_mph ? "> " + nf.format(APPLIED.max_speed_mph) + " mph implied" : "—"),
  duration_over_limit: () => (APPLIED.max_duration_min ? "> " + APPLIED.max_duration_min / 60 + " h" : "—"),
  nonpositive_duration: () => "≤ 0 min",
  zero_distance: () => "≤ 0 mi",
  negative_fare: () => "&lt; $0",
  nonpositive_total: () => "≤ $0",
  out_of_month: () => "outside the source month",
};

function buildLedger() {
  const totals = {};
  LATEST.forEach((r) => {
    for (const [name, count] of Object.entries(r.rules)) totals[name] = (totals[name] || 0) + count;
  });

  const rows = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  document.getElementById("ledger").innerHTML = rows.map(([name, count]) => {
    const quarantine = RULE_EFFECT[name] === "quarantined";
    return `<tr>
      <td class="wrap">${ruleLabel(name, APPLIED)}<br><span class="mono" style="color:var(--ink-3)">${name}</span></td>
      <td class="mono">${(THRESHOLD_TEXT[name] || (() => "—"))()}</td>
      <td class="wrap"><span class="tag">${RULE_EFFECT[name] || "—"}</span></td>
      <td class="mono">${quarantine ? "whole row" : RULE_FIELD[name] || "—"}</td>
      <td class="num">${nf.format(count)}</td>
      <td class="num" style="color:var(--ink-2)">${nf2.format((count / INPUT) * 100)} %</td>
    </tr>`;
  }).join("");

  document.getElementById("ledger-cap").textContent =
    "Rows touched across " + SPAN + ", counted from the most recent run of each of "
    + plural(MONTHS.length, "partition") + " — " + nf.format(INPUT) + " source rows in total.";
}

/* ---------- statické části ---------- */

function specRows(hostId, rows) {
  document.getElementById(hostId).innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function buildHeader() {
  buildEyebrow();

  buildChips([
    { dot: "good", text: "thresholds measured on " + SPAN + ", not guessed" },
    { dot: "good", text: "every run records the thresholds it ran under" },
    { dot: "info", text: "nothing deleted · refused rows kept with a reason" },
  ]);

  const ruleNames = new Set(LATEST.flatMap((r) => Object.keys(r.rules)));
  const quarantining = [...ruleNames].filter((n) => RULE_EFFECT[n] === "quarantined").length;
  const rejected = sum((r) => r.rows.rejected);
  const nulled = sum((r) => Object.values(r.nulled).reduce((a, v) => a + v, 0));

  buildTiles([
    { k: "Rules in force", v: nf.format(ruleNames.size), s: quarantining + " refuse the row · " + (ruleNames.size - quarantining) + " refuse one field" },
    { k: "Source rows judged", v: nf1.format(INPUT / 1e6) + "M", s: "every row of " + SPAN + " passed all of them" },
    { k: "Rows quarantined", v: pct(rejected / INPUT), s: nf.format(rejected) + " rows, each with its reason" },
    { k: "Fields nulled", v: nf.format(nulled), s: "values dropped, rows and money kept" },
  ]);

  specRows("spec-sev", [
    ["Missing required column", "run fails before transform"],
    ["Wrong type, required column", "run fails before transform"],
    ["New column in the source", "recorded in the manifest"],
    ["Optional column gone", "recorded in the manifest"],
    ["No receipt (total ≤ $0)", "row quarantined"],
    ["Implausible measurement", "that field nulled only"],
    ["Threshold breached", "run fails, nothing published"],
  ]);

  specRows("spec-keep", [
    ["Refused rows", "rejects/…/rejects.parquet"],
    ["Reason it was refused", "kept per row"],
    ["Quarantined volume", "refunds_usd"],
    ["Denominator of each mean", "*_obs columns"],
    ["Thresholds of the run", "_runs/&lt;run_id&gt;.json"],
    ["Source identity", "ETag + sha256"],
    ["Schema as seen", "column count + drift"],
  ]);

  document.getElementById("foot").textContent =
    "Counted " + DATA.generated_at + " from the most recent manifest of "
    + plural(MONTHS.length, "partition") + " covering " + SPAN + ".";
}

/* ---------- render ---------- */

function render() {
  drawGates(document.getElementById("gates"), gates());
}

buildHeader();
buildLedger();
render();
watchResize();
