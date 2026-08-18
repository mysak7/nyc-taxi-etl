/* Stránka o provozu: manifesty, prahy, co pravidla chytila. Čísla o samotných jízdách
   jsou na `index.html` -- sem se z payloadu nedostane ani mapa, ani denní řady. */

/* ---------- graf: trychtýř řádků ---------- */

function drawFunnel(host, m) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const run = m.runs[0];
  const H = 92;
  const { svg, width } = svgRoot(host, H, "Vstupní řádky rozdělené na zveřejněné, stornované a v karanténě");
  const pad = 2;
  const inner = width - pad * 2;
  const total = run.rows.input;
  const reversed = run.rows.reversed || 0;
  const pubW = (run.rows.published / total) * inner - 1;
  const revW = (reversed / total) * inner - 1;
  const rejW = (run.rows.rejected / total) * inner - 1;
  const y = 30;
  const h = 26;

  svg.appendChild(el("path", { d: barPath(pad, y, pubW, h, 0), fill: "var(--s1)" }));
  // Segmenty odděluje 2px mezera v barvě podkladu, ne obrys. Storna jsou vlastní
  // segment: berou celý řádek jako karanténa, ale vadná data to nejsou.
  if (revW > 0) svg.appendChild(el("path", { d: barPath(pad + pubW + 2, y, revW, h, 0), fill: "var(--s2)" }));
  svg.appendChild(el("path", { d: barPath(pad + pubW + Math.max(revW + 2, 0) + 2, y, rejW, h, 4), fill: "var(--s3)" }));

  const share = (((reversed + run.rows.rejected) / total) * 100).toFixed(2);
  svg.append(
    el("text", { x: pad, y: y - 9, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 12, "font-weight": 600 }, nf.format(run.rows.published)),
    el("text", { x: pad, y: y + h + 17, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11 }, nf.format(total) + " vstupních řádků"),
    el("text", { x: width - pad, y: y - 9, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, nf.format(reversed + run.rows.rejected) + " (" + share + " %)"),
    el("text", { x: width - pad, y: y + h + 17, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, nf.format(run.rows.output) + " výstupních řádků")
  );
  host.appendChild(svg);
}

/* ---------- statické části ---------- */

function specRows(hostId, rows) {
  document.getElementById(hostId).innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function buildHeader() {
  buildEyebrow();

  const f = DATA.freshness;
  buildChips([
    { dot: "good", text: "mezera v příjmu dat: žádná" },
    { dot: "good", text: "zdroj publikoval " + f.source_newest + ", před " + f.source_age_days + " dny (práh " + CFG.source_stale_days + ")" },
    { dot: "info", text: plural(MONTHS.length, "partition") + " · postaveno " + DATA.generated_at },
  ]);

  const runs = MONTHS.flatMap((m) => m.runs);
  const rows = MONTHS.reduce((a, m) => a + m.rows, 0);
  // Jen nejnovější běh každé partition: starší manifesty popisují data, která už v
  // curated nejsou, a sečíst je dohromady by vstupní řádky nafouklo o přepočty.
  const latest = MONTHS.map((m) => m.runs[0]);
  const inputRows = latest.reduce((a, r) => a + r.rows.input, 0);
  const rejected = latest.reduce((a, r) => a + r.rows.rejected, 0);
  const reversed = latest.reduce((a, r) => a + (r.rows.reversed || 0), 0);
  const slowest = Math.max(...runs.map((r) => r.timing.seconds));

  buildTiles([
    { k: "Přečtené zdrojové řádky" + SUM_SCOPE, v: nf.format(Math.round(inputRows / 1e6)) + " mil.", s: "→ " + nf.format(rows) + " výstupních řádků" },
    { k: "V karanténě" + SUM_SCOPE, v: pct(rejected / inputRows), s: nf.format(rejected) + " řádků · " + nf.format(reversed) + " dalších je storno" },
    { k: "Manifesty", v: nf.format(runs.length), s: plural(runs.length, "běh", "běhy", "běhů") + " přes " + plural(MONTHS.length, "partition") },
    { k: "Nejpomalejší běh", v: nf1.format(slowest) + " s", s: "jeden zdrojový měsíc, souběžnost map 2" },
  ]);

  document.getElementById("foot").textContent =
    "Postaveno " + DATA.generated_at + " z " + plural(runs.length, "manifestu", "manifestů") + " a "
    + plural(MONTHS.length, "souboru Parquet", "souborů Parquet") + " pokrývajících " + SPAN + ".";

  specRows("spec-run", [
    ["Plán", "denně, EventBridge Scheduler"],
    ["Orchestrátor", "AWS Step Functions"],
    ["Worker", "Lambda, kontejnerový image"],
    ["Souběžnost map", "2"],
    ["Okno zpětného pohledu", CFG.lookback_months + " měsíců"],
    ["Alternativní runner", "Airflow 3 DAG, tentýž image"],
    ["Jednotka idempotence", "jeden zdrojový měsíc"],
    ["Zápis partition", "jeden soubor, jeden PUT"],
  ]);

  specRows("spec-dq", [
    ["Podíl karantény", "padá nad " + CFG.max_reject_ratio * 100 + " %"],
    ["Podíl storn", "padá nad " + CFG.max_reversal_ratio * 100 + " %"],
    ["Objem proti předchozímu měsíci", "padá nad ±" + CFG.max_volume_delta * 100 + " %"],
    ["Vynulovaná vzdálenost", "padá nad " + CFG.max_null_ratio_distance * 100 + " %"],
    ["Vynulovaná doba jízdy", "padá nad " + CFG.max_null_ratio_duration * 100 + " %"],
    ["Odvozená rychlost", "≤ " + nf.format(CFG.max_speed_mph) + " mph"],
    ["Záchranná brzda vzdálenosti", "≤ " + nf.format(CFG.max_distance_mi) + " mi"],
    ["Věrohodná doba jízdy", "≤ " + CFG.max_duration_min / 60 + " h"],
    ["Zdroj je zastaralý po", CFG.source_stale_days + " dnech"],
    ["Kontrakt schématu", "ověřen před transformací"],
  ]);
}

const RUN_LIMIT = 14;

function buildRuns() {
  const rows = [];
  MONTHS.forEach((m) => m.runs.forEach((r) => rows.push({ m, r })));
  rows.sort((a, b) => (a.m.key < b.m.key ? 1 : a.m.key > b.m.key ? -1 : 0));

  document.getElementById("runs").innerHTML = rows.slice(0, RUN_LIMIT).map(({ m, r }) => `
    <tr class="pick" data-key="${m.key}" tabindex="0">
      <td>${m.key}</td>
      <td><span class="tag">${r.trigger}</span></td>
      <td class="mono">${r.run_id.slice(0, 8)}</td>
      <td class="num">${nf.format(r.rows.input)}</td>
      <td class="num">${nf.format(r.rows.published)}</td>
      <td class="num">${nf.format(r.rows.reversed || 0)}</td>
      <td class="num">${nf.format(r.rows.rejected)}</td>
      <td class="num">${nf.format(r.rows.output)}</td>
      <td class="num">${nf1.format(r.timing.seconds)} s</td>
      <td class="mono">${r.source.etag.replace(/"/g, "")}</td>
    </tr>`).join("");

  document.getElementById("runs-more").textContent = rows.length > RUN_LIMIT
    ? "zobrazeno " + RUN_LIMIT + " nejnovějších z " + rows.length + " manifestů"
    : plural(rows.length, "manifest", "manifesty", "manifestů");

  document.querySelectorAll("#runs tr").forEach((tr) => {
    const pick = () => select(tr.dataset.key);
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
}

/* ---------- render ---------- */

function render() {
  const m = month();
  syncPicker();
  document.querySelectorAll("#runs tr").forEach((tr) => tr.classList.toggle("on", tr.dataset.key === current));

  const run = m.runs[0];
  document.getElementById("funnel-cap").textContent =
    label(m) + " — nejnovější z " + plural(m.runs.length, "běhu", "běhů")
    + " téhle partition, proti zdrojovému ETagu " + run.source.etag.replace(/"/g, "") + ".";

  drawFunnel(document.getElementById("funnel"), m);

  const applied = run.thresholds_applied || {};
  const rules = Object.entries(run.rules)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({
      label: ruleLabel(k, applied),
      value: v,
      display: nf.format(v),
      tip: `${nf.format(v)} řádků · ${RULE_EFFECT[k] || "—"}<br><span class="r">${k}</span>`,
    }));
  drawBars(document.getElementById("rules"), rules, { labelW: 148, valueW: 74, fill: "var(--s3)", title: "Řádky dotčené jednotlivými pravidly kvality dat" });
}

buildHeader();
buildRuns();
buildPicker();
render();
watchResize();
