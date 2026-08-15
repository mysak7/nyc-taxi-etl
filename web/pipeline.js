/* Stránka o provozu: manifesty, prahy, co pravidla chytila. Čísla o samotných jízdách
   jsou na `index.html` -- sem se z payloadu nedostane ani mapa, ani denní řady. */

/* ---------- chart: row funnel ---------- */

function drawFunnel(host, m) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const run = m.runs[0];
  const H = 92;
  const { svg, width } = svgRoot(host, H, "Input rows split into published and quarantined");
  const pad = 2;
  const inner = width - pad * 2;
  const total = run.rows.input;
  const pubW = (run.rows.published / total) * inner - 1;
  const rejW = (run.rows.rejected / total) * inner - 1;
  const y = 30;
  const h = 26;

  svg.appendChild(el("path", { d: barPath(pad, y, pubW, h, 0), fill: "var(--s1)" }));
  // A 2px gap in the surface colour separates the segments instead of an outline.
  svg.appendChild(el("path", { d: barPath(pad + pubW + 2, y, rejW, h, 4), fill: "var(--s2)" }));

  const share = ((run.rows.rejected / total) * 100).toFixed(2);
  svg.append(
    el("text", { x: pad, y: y - 9, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 12, "font-weight": 600 }, nf.format(run.rows.published)),
    el("text", { x: pad, y: y + h + 17, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11 }, nf.format(total) + " input rows"),
    el("text", { x: width - pad, y: y - 9, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, nf.format(run.rows.rejected) + " (" + share + " %)"),
    el("text", { x: width - pad, y: y + h + 17, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, nf.format(run.rows.output) + " output rows")
  );
  host.appendChild(svg);
}

/* ---------- static parts ---------- */

function specRows(hostId, rows) {
  document.getElementById(hostId).innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

function buildHeader() {
  buildEyebrow();

  const f = DATA.freshness;
  buildChips([
    { dot: "good", text: "ingest gap: none" },
    { dot: "good", text: "source published " + f.source_newest + ", " + f.source_age_days + " days ago (threshold " + CFG.source_stale_days + ")" },
    { dot: "info", text: plural(MONTHS.length, "partition") + " · built " + DATA.generated_at },
  ]);

  const runs = MONTHS.flatMap((m) => m.runs);
  const rows = MONTHS.reduce((a, m) => a + m.rows, 0);
  // Jen nejnovější běh každé partition: starší manifesty popisují data, která už v
  // curated nejsou, a sečíst je dohromady by vstupní řádky nafouklo o přepočty.
  const latest = MONTHS.map((m) => m.runs[0]);
  const inputRows = latest.reduce((a, r) => a + r.rows.input, 0);
  const rejected = latest.reduce((a, r) => a + r.rows.rejected, 0);
  const slowest = Math.max(...runs.map((r) => r.timing.seconds));

  buildTiles([
    { k: "Source rows read", v: nf.format(Math.round(inputRows / 1e6)) + "M", s: "→ " + nf.format(rows) + " output rows" },
    { k: "Quarantined", v: pct(rejected / inputRows), s: nf.format(rejected) + " rows, kept with a reason" },
    { k: "Manifests", v: nf.format(runs.length), s: plural(runs.length, "run") + " across " + plural(MONTHS.length, "partition") },
    { k: "Slowest run", v: nf1.format(slowest) + " s", s: "one source month, map concurrency 2" },
  ]);

  document.getElementById("foot").textContent =
    "Built " + DATA.generated_at + " from " + plural(runs.length, "manifest") + " and "
    + plural(MONTHS.length, "Parquet file") + " covering " + SPAN + ".";

  specRows("spec-run", [
    ["Schedule", "daily, EventBridge Scheduler"],
    ["Orchestrator", "AWS Step Functions"],
    ["Worker", "Lambda container image"],
    ["Map concurrency", "2"],
    ["Lookback window", CFG.lookback_months + " months"],
    ["Alternative runner", "Airflow 3 DAG, same image"],
    ["Idempotence unit", "one source month"],
    ["Partition write", "one file, one PUT"],
  ]);

  specRows("spec-dq", [
    ["Quarantine ratio", "fail over " + CFG.max_reject_ratio * 100 + " %"],
    ["Volume vs. previous month", "fail over ±" + CFG.max_volume_delta * 100 + " %"],
    ["Nulled distance", "fail over " + CFG.max_null_ratio_distance * 100 + " %"],
    ["Nulled duration", "fail over " + CFG.max_null_ratio_duration * 100 + " %"],
    ["Implied speed", "≤ " + nf.format(CFG.max_speed_mph) + " mph"],
    ["Distance fallback", "≤ " + nf.format(CFG.max_distance_mi) + " mi"],
    ["Plausible duration", "≤ " + CFG.max_duration_min / 60 + " h"],
    ["Source stale after", CFG.source_stale_days + " days"],
    ["Schema contract", "checked before transform"],
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
      <td class="num">${nf.format(r.rows.rejected)}</td>
      <td class="num">${nf.format(r.rows.output)}</td>
      <td class="num">${nf1.format(r.timing.seconds)} s</td>
      <td class="mono">${r.source.etag.replace(/"/g, "")}</td>
    </tr>`).join("");

  document.getElementById("runs-more").textContent = rows.length > RUN_LIMIT
    ? "showing the " + RUN_LIMIT + " most recent of " + rows.length + " manifests"
    : plural(rows.length, "manifest");

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
    label(m) + " — the most recent of " + plural(m.runs.length, "run")
    + " for this partition, against source ETag " + run.source.etag.replace(/"/g, "") + ".";

  drawFunnel(document.getElementById("funnel"), m);

  const applied = run.thresholds_applied || {};
  const rules = Object.entries(run.rules)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({
      label: ruleLabel(k, applied),
      value: v,
      display: nf.format(v),
      tip: `${nf.format(v)} rows · ${RULE_EFFECT[k] || "—"}<br><span class="r">${k}</span>`,
    }));
  drawBars(document.getElementById("rules"), rules, { labelW: 148, valueW: 74, fill: "var(--s3)", title: "Rows touched by each data quality rule" });
}

buildHeader();
buildRuns();
buildPicker();
render();
watchResize();
