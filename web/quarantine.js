/* Stránka o karanténě: co se z datasetu ztratilo, měsíc po měsíci.

   Provozní stránka ukazuje jeden běh, metodická celou historii v jednom součtu -- tady
   jde o řadu: který měsíc kolik odmítl a proč. Zdroj čísel je tentýž manifest, bere se
   nejnovější z každé partition (starší popisují data, která už v curated nejsou).

   Odmítnuté řádky samotné tu nejsou a nebudou: role, která stránku v CI staví, čte jen
   prefix `curated/`. Počty a důvody z manifestu ano, surové řádky ne. */

const LATEST = MONTHS.map((m) => m.runs[0]);
const APPLIED = LATEST[LATEST.length - 1].thresholds_applied || {};
const INPUT = LATEST.reduce((a, r) => a + r.rows.input, 0);
const REJECTED = LATEST.reduce((a, r) => a + r.rows.rejected, 0);

// Pravidla, která odmítají celý řádek. Odvozuje se ze sdíleného slovníku důsledků, ne
// z druhého seznamu jmen -- jinak by přibylo pravidlo a stránka o něm mlčela.
const QUARANTINE_RULES = Object.keys(RULE_EFFECT).filter((n) => RULE_EFFECT[n] === "karanténa");
// Pořadí ve stohu i v legendě. Nekladná částka je o tři řády častější, patří dolů.
const STACK_ORDER = ["nonpositive_total", "out_of_month"];
const STACK_FILL = { nonpositive_total: "var(--s2)", out_of_month: "var(--s3)" };

const ruleCount = (run, name) => run.rules[name] || 0;
const nulledCount = (run, column) => run.nulled[column] || 0;
const share = (part, whole) => (whole > 0 ? part / whole : null);
// Podíly karantény jsou desetiny procenta, `pct` s jedním desetinným místem by z půlky
// měsíců udělalo "0,1 %" a řada by vypadala plochá.
const pct2 = (v) => nf2.format(v * 100) + " %";

/* ---------- graf: stoh po měsících ---------- */

// Sloupcový stoh, ne `drawBars`: kategorie (důvod) je uvnitř sloupce, osa x je čas.
// Výška je podíl na vstupu téhož měsíce, ne absolutní počet -- měsíce se liší objemem
// o desítky procent a absolutní počty by o kvalitě dat tvrdily něco jiného než data.
function drawStack(host, cols, opts) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const T = 12, B = 30, L = 46;
  const H = 208;
  const { svg, width } = svgRoot(host, H, opts.title);
  const plotH = H - T - B;
  const base = T + plotH;
  const pw = Math.max(40, width - L);
  const step = pw / cols.length;
  const bw = Math.max(2, Math.min(44, step - 6));
  const max = Math.max(...cols.map((c) => c.total)) || 1;
  const tip = tipFor(host);

  // Dvě vodicí čáry stačí: maximum říká měřítko, půlka dovolí odhadnout zbytek.
  [max, max / 2].forEach((value) => {
    const y = base - (value / max) * plotH;
    svg.appendChild(el("line", { x1: L, x2: L + pw, y1: y, y2: y, stroke: "var(--rule)", "stroke-width": 1 }));
    svg.appendChild(el("text", { x: L - 8, y: y + 4, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "end" }, opts.fmt(value)));
  });
  svg.appendChild(el("line", { x1: L, x2: L + pw, y1: base, y2: base, stroke: "var(--rule-strong)", "stroke-width": 1 }));

  cols.forEach((c, i) => {
    const x = L + i * step + (step - bw) / 2;
    let y = base;
    // Kreslí se odspodu nahoru, aby zaoblený byl jen vršek celého sloupce.
    c.parts.forEach((p, k) => {
      const h = (p.value / max) * plotH;
      if (h <= 0) return;
      y -= h;
      svg.appendChild(el("path", { d: barPathUp(x, y, bw, h, k === c.parts.length - 1 ? 3 : 0), fill: p.fill }));
    });

    // Popisek měsíce se vejde, jen když je sloupec dost široký; rok se tiskne u ledna
    // a u prvního sloupce, aby řada přes několik let nebyla bezejmenná.
    if (step >= 26) {
      svg.appendChild(el("text", { x: x + bw / 2, y: base + 14, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "middle" }, MONTH_NAMES[c.month - 1]));
    }
    if (c.month === 1 || i === 0) {
      svg.appendChild(el("text", { x: x + bw / 2, y: base + (step >= 26 ? 26 : 14), fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "middle" }, String(c.year)));
    }

    const hit = el("rect", { x: L + i * step, y: T, width: step, height: plotH + B, fill: "transparent" });
    hit.appendChild(el("title", {}, c.label + ": " + opts.fmt(c.total)));
    // Klik, ne najetí: výběr měsíce překreslí celý graf, a překreslit ho pod kurzorem,
    // který se jen mihl, by tooltip utrhl uprostřed pohybu.
    hit.addEventListener("click", () => select(c.key));
    hit.addEventListener("mousemove", (ev) => {
      const box = svg.getBoundingClientRect();
      tip.innerHTML = `<b>${c.label}</b><br>${c.tip}`;
      placeTip(tip, host, ev.clientX - box.left, Math.max(0, ((base - (c.total / max) * plotH - 8) / H) * host.clientHeight - 62));
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    svg.appendChild(hit);
  });

  host.appendChild(svg);
}

function stackCols() {
  return MONTHS.map((m, i) => {
    const run = LATEST[i];
    const parts = STACK_ORDER.map((name) => ({
      name,
      value: share(ruleCount(run, name), run.rows.input) || 0,
      fill: STACK_FILL[name],
    }));
    const rows = STACK_ORDER.map(
      (name) => `${ruleLabel(name, run.thresholds_applied || {})}: ${nf.format(ruleCount(run, name))} řádků`
    ).join("<br>");
    return {
      key: m.key,
      year: m.year,
      month: m.month,
      label: label(m),
      total: share(run.rows.rejected, run.rows.input) || 0,
      parts,
      tip:
        `v karanténě ${nf.format(run.rows.rejected)} z ${nf.format(run.rows.input)} řádků`
        + `<br>${rows}<br><span class="r">${pct2(share(run.rows.rejected, run.rows.input) || 0)} vstupu · práh ${pct(CFG.max_reject_ratio)}</span>`,
    };
  });
}

/* ---------- graf: trychtýř vybraného měsíce ---------- */

// Táž kresba jako na provozní stránce, ale pro měsíc vybraný tady. Sdílet by se dala,
// jenže na provozní stránce je to jediný graf o jednom běhu a tady jeden ze dvou o řadě
// -- společný by musel umět obojí, aby ušetřil dvacet řádků.
function drawFunnel(host, run) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const H = 92;
  const { svg, width } = svgRoot(host, H, "Vstupní řádky rozdělené na zveřejněné a v karanténě");
  const pad = 2;
  const inner = width - pad * 2;
  const total = run.rows.input;
  const pubW = (run.rows.published / total) * inner - 1;
  const rejW = (run.rows.rejected / total) * inner - 1;
  const y = 30;
  const h = 26;

  svg.appendChild(el("path", { d: barPath(pad, y, pubW, h, 0), fill: "var(--s1)" }));
  svg.appendChild(el("path", { d: barPath(pad + pubW + 2, y, rejW, h, 4), fill: "var(--s2)" }));

  svg.append(
    el("text", { x: pad, y: y - 9, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 12, "font-weight": 600 }, nf.format(run.rows.published)),
    el("text", { x: pad, y: y + h + 17, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11 }, nf.format(total) + " vstupních řádků"),
    el("text", { x: width - pad, y: y - 9, fill: "var(--ink-2)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, nf.format(run.rows.rejected) + " (" + pct2(run.rows.rejected / total) + ")"),
    el("text", { x: width - pad, y: y + h + 17, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 11, "text-anchor": "end" }, nf.format(run.rows.output) + " výstupních řádků")
  );
  host.appendChild(svg);
}

/* ---------- tabulka měsíců ---------- */

function buildMonths() {
  const cells = (m, run) => [
    ["", label(m)],
    ["num", nf.format(run.rows.input)],
    ["num", nf.format(run.rows.rejected)],
    ["num", pct2(share(run.rows.rejected, run.rows.input) || 0)],
    ["num", nf.format(ruleCount(run, "out_of_month"))],
    ["num", nf.format(ruleCount(run, "nonpositive_total"))],
    // Storna jsou záporná; znaménko se nechává, protože se od hrubé tržby odečítají.
    ["num", m.refunds ? usdCompact(m.refunds) : "—"],
    ["num", nf.format(nulledCount(run, "trip_distance"))],
    ["num", nf.format(nulledCount(run, "duration_min"))],
    ["num", nf.format(nulledCount(run, "fare_amount"))],
  ];

  document.getElementById("months").innerHTML = MONTHS.map((m, i) => {
    const row = cells(m, LATEST[i]).map(([cls, text]) => `<td class="${cls}">${text}</td>`).join("");
    return `<tr class="pick" data-key="${m.key}" tabindex="0">${row}</tr>`;
  }).join("");

  const total = (pick) => LATEST.reduce((a, r) => a + pick(r), 0);
  const refunds = MONTHS.reduce((a, m) => a + (m.refunds || 0), 0);
  document.getElementById("months-total").innerHTML = `<tr>
    <td>celkem</td>
    <td class="num">${nf.format(INPUT)}</td>
    <td class="num">${nf.format(REJECTED)}</td>
    <td class="num">${pct2(share(REJECTED, INPUT) || 0)}</td>
    <td class="num">${nf.format(total((r) => ruleCount(r, "out_of_month")))}</td>
    <td class="num">${nf.format(total((r) => ruleCount(r, "nonpositive_total")))}</td>
    <td class="num">${refunds ? usdCompact(refunds) : "—"}</td>
    <td class="num">${nf.format(total((r) => nulledCount(r, "trip_distance")))}</td>
    <td class="num">${nf.format(total((r) => nulledCount(r, "duration_min")))}</td>
    <td class="num">${nf.format(total((r) => nulledCount(r, "fare_amount")))}</td>
  </tr>`;

  document.getElementById("months-foot").textContent =
    plural(MONTHS.length, "měsíc", "měsíce", "měsíců") + " · " + SPAN
    + " · nejnovější manifest z každé partition";

  document.querySelectorAll("#months tr").forEach((tr) => {
    const pick = () => select(tr.dataset.key);
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
}

/* ---------- statické části ---------- */

function buildHeader() {
  buildEyebrow();

  const worst = MONTHS.reduce(
    (best, m, i) => {
      const value = share(LATEST[i].rows.rejected, LATEST[i].rows.input) || 0;
      return value > best.value ? { value, key: m.key } : best;
    },
    { value: 0, key: MONTHS[0].key }
  );

  // Chip nesmí tvrdit "je to v pohodě" nezávisle na datech: měří se skutečná rezerva
  // nejhoršího měsíce proti prahu, a pod polovinou vyčerpané rezervy se to teprve
  // hlásí jako dobré.
  const headroom = worst.value / CFG.max_reject_ratio;
  buildChips([
    headroom < 0.5
      ? { dot: "good", text: "žádný měsíc nevyčerpal ani polovinu rezervy k prahu " + pct(CFG.max_reject_ratio) }
      : { dot: "warn", text: "nejhorší měsíc vyčerpal " + pct(headroom) + " rezervy k prahu " + pct(CFG.max_reject_ratio) },
    { dot: "info", text: "nejvyšší podíl: " + worst.key + " (" + pct2(worst.value) + ")" },
    { dot: "info", text: plural(MONTHS.length, "měsíc", "měsíce", "měsíců") + " · " + SPAN },
  ]);

  const nulled = LATEST.reduce((a, r) => a + Object.values(r.nulled).reduce((b, v) => b + v, 0), 0);
  const refunds = MONTHS.reduce((a, m) => a + (m.refunds || 0), 0);

  buildTiles([
    { k: "Řádky v karanténě", v: nf.format(REJECTED), s: pct2(share(REJECTED, INPUT) || 0) + " z " + nf.format(INPUT) + " vstupních řádků" },
    { k: "Nejvyšší měsíční podíl", v: pct2(worst.value), s: worst.key + " · práh běhu " + pct(CFG.max_reject_ratio) },
    { k: "Objem storn", v: refunds ? usdCompact(refunds) : "—", s: "odmítnuté řádky, jejichž peníze se vykazují dál" },
    { k: "Vynulovaná pole", v: nf.format(nulled), s: "hodnota zahozena, řádek i jeho peníze zůstávají" },
  ]);

  document.getElementById("reject-limit").textContent = pct(CFG.max_reject_ratio) + " vstupních řádků";

  document.getElementById("spec-keep").innerHTML = [
    ["Odmítnuté řádky", "rejects/…/rejects.parquet"],
    ["Důvod u každého řádku", "reject_reason"],
    ["Sloupce, které si nesou", "6 zdrojových + důvod"],
    ["Objem storn v curated", "refunds_usd"],
    ["Počty za běh", "_runs/&lt;run_id&gt;.json"],
    ["Retence", "žádná, nic se nemaže"],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");

  document.getElementById("stack-cap").textContent =
    "Podíl odmítnutých řádků na zdrojových, " + SPAN + ". Kliknutím na sloupec se níž načte rozpis toho měsíce.";

  document.getElementById("stack-legend").innerHTML = STACK_ORDER.map(
    (name) => `<span><i class="swatch" style="background:${STACK_FILL[name]}"></i>${ruleLabel(name, APPLIED)}</span>`
  ).join("");

  document.getElementById("foot").textContent =
    "Spočítáno " + DATA.generated_at + " z nejnovějšího manifestu "
    + plural(MONTHS.length, "partition") + " pokrývajících " + SPAN + ".";
}

/* ---------- render ---------- */

function render() {
  const m = month();
  const run = LATEST[idx()];
  syncPicker();
  document.querySelectorAll("#months tr").forEach((tr) => tr.classList.toggle("on", tr.dataset.key === current));

  drawStack(document.getElementById("stack"), stackCols(), {
    title: "Podíl odmítnutých řádků po měsících, rozpad podle důvodu",
    fmt: pct2,
  });

  document.getElementById("funnel-cap").textContent =
    label(m) + " — nejnovější z " + plural(m.runs.length, "běhu", "běhů")
    + " téhle partition, proti zdrojovému ETagu " + run.source.etag.replace(/"/g, "") + ".";
  drawFunnel(document.getElementById("funnel"), run);

  const applied = run.thresholds_applied || {};
  const rows = Object.entries(run.rules).sort((a, b) => b[1] - a[1]);
  document.getElementById("rules").innerHTML = rows.map(([name, count]) => {
    const quarantine = RULE_EFFECT[name] === "karanténa";
    return `<tr>
      <td class="wrap">${ruleLabel(name, applied)}<br><span class="mono" style="color:var(--ink-3)">${name}</span></td>
      <td class="wrap"><span class="tag">${RULE_EFFECT[name] || "—"}</span></td>
      <td class="mono">${quarantine ? "celý řádek" : RULE_FIELD[name] || "—"}</td>
      <td class="num">${nf.format(count)}</td>
      <td class="num" style="color:var(--ink-2)">${count ? pct2(count / run.rows.input) : "—"}</td>
    </tr>`;
  }).join("");

  document.getElementById("rules-cap").textContent =
    label(m) + " — " + nf.format(run.rows.input) + " posouzených zdrojových řádků. Počítá se"
    + " nezávisle: jeden řádek může porušit několik pravidel naráz.";

  // Kolik řádků porušilo obě odmítací pravidla zároveň. Manifest to přímo nenese, ale
  // plyne to z rozdílu: součet dotčených minus ti, co doopravdy skončili v karanténě.
  const flagged = QUARANTINE_RULES.reduce((a, name) => a + ruleCount(run, name), 0);
  const overlap = flagged - run.rows.rejected;
  document.getElementById("overlap").innerHTML = overlap > 0
    ? `Odmítací pravidla se dotkla ${nf.format(flagged)} řádků, v karanténě jich je `
      + `${nf.format(run.rows.rejected)}: ${plural(overlap, "řádek", "řádky", "řádků")} porušil`
      + `${overlap === 1 ? "" : "y"} obě naráz a počítá se jednou. Štítek `
      + `<code>reject_reason</code> takový řádek dostane podle pořadí pravidel, od nejvzácnějšího.`
    : `Odmítací pravidla se dotkla ${nf.format(flagged)} řádků a přesně tolik jich je `
      + `v karanténě — v tomhle měsíci žádný řádek neporušil obě naráz.`;
}

buildHeader();
buildMonths();
buildPicker();
render();
watchResize();
