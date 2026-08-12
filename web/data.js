/* Stránka pro čtenáře dat: mapa, měsíční řady, zóny. Nic o běhu pipeline -- provozní
   čísla, manifesty a prahy jsou na `pipeline.html` a payload je sem ani nedostane. */

/* ---------- chart: the whole history ---------- */

function drawHistory(host) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const H = 172;
  const { svg, width } = svgRoot(host, H, "Published trips per source month across the whole dataset");
  const L = 46, R = 8, T = 14, B = 36;
  const pw = width - L - R;
  const ph = H - T - B;
  const max = Math.max(...MONTHS.map((m) => m.trips));
  const top = max * 1.08;
  const slot = pw / MONTHS.length;
  const bw = Math.max(2, Math.min(26, slot - 3));
  const y = (v) => T + ph - (v / top) * ph;
  const tip = tipFor(host);

  for (let t = 0; t <= 3; t++) {
    const v = (top / 3) * t;
    svg.appendChild(el("line", { x1: L, x2: L + pw, y1: y(v), y2: y(v), stroke: "var(--rule)", "stroke-width": 1 }));
    svg.appendChild(el("text", { x: L - 9, y: y(v) + 4, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "end" }, t === 0 ? "0" : nf1.format(v / 1e6) + "M"));
  }

  const every = Math.max(1, Math.ceil(MONTHS.length / Math.max(2, Math.floor(pw / 56))));

  MONTHS.forEach((m, i) => {
    const x = L + i * slot + (slot - bw) / 2;
    const on = m.key === current;
    // Selection is UI state, not a data category: the same hue, dimmed.
    svg.appendChild(el("path", {
      d: barPathUp(x, y(m.trips), bw, ph - (y(m.trips) - T), 3),
      fill: "var(--s1)",
      opacity: on ? 1 : 0.32,
    }));
    if (on) svg.appendChild(el("rect", { x: L + i * slot, y: T + ph + 3, width: slot, height: 2, fill: "var(--accent)" }));
    if (i % every === 0) {
      svg.appendChild(el("text", { x: x + bw / 2, y: H - 12, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10, "text-anchor": "middle" }, m.key));
    }

    const hit = el("rect", { x: L + i * slot, y: T, width: slot, height: ph, fill: "transparent", style: "cursor:pointer", role: "button", tabindex: 0 });
    hit.appendChild(el("title", {}, label(m) + ": " + nf.format(m.trips) + " trips"));
    hit.addEventListener("mousemove", () => {
      tip.innerHTML = `<b>${label(m)}</b><br>${nf.format(m.trips)} trips<br>`
        + `<span class="r">${usdM(m.revenue)} revenue</span>`;
      placeTip(tip, host, ((L + i * slot + slot / 2) / width) * host.clientWidth, Math.max(0, ((y(m.trips) - 4) / H) * host.clientHeight - 64));
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    hit.addEventListener("click", () => select(m.key));
    hit.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(m.key); } });
    svg.appendChild(hit);
  });

  host.appendChild(svg);
}

/* ---------- chart: daily line ---------- */

function drawDaily(host, m) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const days = m.daily;
  const H = 250;
  const { svg, width } = svgRoot(host, H, "Trips per day for " + label(m));
  const L = 54, R = 14, T = 18, B = 30;
  const pw = width - L - R;
  const ph = H - T - B;
  const max = Math.max(...days.map((d) => d.trips));
  const top = max * 1.1;
  const x = (i) => L + (days.length === 1 ? pw / 2 : (i / (days.length - 1)) * pw);
  const y = (v) => T + ph - (v / top) * ph;
  const step = pw / Math.max(1, days.length - 1);

  days.forEach((d, i) => {
    if (!isWeekend(d.date)) return;
    const x0 = Math.max(L, x(i) - step / 2);
    const x1 = Math.min(L + pw, x(i) + step / 2);
    svg.appendChild(el("rect", { x: x0, y: T, width: x1 - x0, height: ph, fill: "var(--band)" }));
  });

  for (let t = 0; t <= 4; t++) {
    const v = (top / 4) * t;
    svg.appendChild(el("line", { x1: L, x2: L + pw, y1: y(v), y2: y(v), stroke: "var(--rule)", "stroke-width": 1 }));
    svg.appendChild(el("text", { x: L - 10, y: y(v) + 4, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "end" }, t === 0 ? "0" : nf.format(Math.round(v / 1000)) + "k"));
  }

  const line = days.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.trips).toFixed(1)}`).join(" ");
  svg.appendChild(el("path", { d: `${line} L${x(days.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z`, fill: "var(--s1-soft)" }));
  svg.appendChild(el("path", { d: line, fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  const every = Math.max(2, Math.ceil(days.length / Math.max(2, Math.floor(pw / 64))));
  days.forEach((d, i) => {
    if (i % every !== 0 || i > days.length - 2) return;
    svg.appendChild(el("text", { x: x(i), y: H - 9, fill: "var(--ink-3)", "font-family": "var(--mono)", "font-size": 10.5, "text-anchor": "middle" }, dayLabel(d.date)));
  });

  // Direct-label the peak only; a number on every point goes unread.
  const peak = days.reduce((a, b, i) => (b.trips > days[a].trips ? i : a), 0);
  svg.appendChild(el("circle", { cx: x(peak), cy: y(days[peak].trips), r: 5, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": 2 }));
  const anchor = peak > days.length - 5 ? "end" : peak < 4 ? "start" : "middle";
  svg.appendChild(el("text", { x: x(peak), y: y(days[peak].trips) - 12, fill: "var(--ink)", "font-family": "var(--mono)", "font-size": 11, "font-weight": 600, "text-anchor": anchor }, nf.format(days[peak].trips)));

  const cross = el("line", { x1: 0, x2: 0, y1: T, y2: T + ph, stroke: "var(--rule-strong)", "stroke-width": 1, opacity: 0 });
  const knob = el("circle", { r: 5, fill: "var(--s1)", stroke: "var(--surface)", "stroke-width": 2, opacity: 0 });
  svg.append(cross, knob);

  const tip = tipFor(host);
  const hit = el("rect", { x: L, y: T, width: pw, height: ph, fill: "transparent" });
  svg.appendChild(hit);

  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * width;
    const i = Math.max(0, Math.min(days.length - 1, Math.round(((px - L) / pw) * (days.length - 1))));
    const d = days[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("opacity", 1);
    knob.setAttribute("cx", x(i)); knob.setAttribute("cy", y(d.trips)); knob.setAttribute("opacity", 1);
    tip.innerHTML = `<b>${dayLabel(d.date)}</b>${isWeekend(d.date) ? " <span class='r'>weekend</span>" : ""}<br>`
      + `${nf.format(d.trips)} trips<br><span class="r">revenue ${usd(d.revenue)}<br>refunds ${usd(d.refunds)}</span>`;
    placeTip(tip, host, (x(i) / width) * host.clientWidth, Math.max(4, (y(d.trips) / H) * host.clientHeight - 84));
  });

  hit.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.setAttribute("opacity", 0);
    knob.setAttribute("opacity", 0);
  });

  host.appendChild(svg);
  buildDailyTable(m);
}

/* ---------- map: pickup zones ---------- */

// Součty jdou sečíst, průměry ne -- vážený průměr přes zóny je v payloadu už spočítaný,
// tady se jen vybírá sloupec a formátuje.
//
// Tři skupiny, protože každá stojí na jiném jmenovateli a čte se jinak: součet za zónu
// (obarví se prostě to, kde je nejvíc provozu), průměr na jízdu, a poměr dvou průměrů.
// Poslední skupina je odvozená -- rychlost ani cena za míli v curated jako sloupec není
// a nemůže být: průměr podílů se z předpočítaného průměru nedá dostat zpátky. Proto je
// to podíl průměrů a `about` to u každé takové metriky říká nahlas.
// Jmenovatel nula nebo chybějící čitatel dá null, ne NaN: mapa má tři stavy a "nevíme"
// nesmí propadnout do některého z těch dvou zbylých.
const ratio = (num, den, scale = 1) => (num != null && den ? (num / den) * scale : null);

const METRICS = {
  trips: {
    group: "totals",
    label: "Published trips",
    about: "Trips attributed to the pickup zone, summed over the whole history.",
    of: (z) => z.trips,
    full: (v) => nf.format(v) + " trips",
    short: (v) => (v >= 1e6 ? nf1.format(v / 1e6) + "M" : v >= 1e3 ? Math.round(v / 1e3) + "k" : nf.format(v)),
  },
  revenue: {
    group: "totals",
    label: "Revenue",
    about: "Gross <code>total_amount</code> of the published trips — tips, tolls and surcharges included. Refunds are reported separately and are not netted off.",
    of: (z) => z.revenue,
    full: (v) => usd(v),
    brief: usdCompact,
    short: usdCompact,
  },
  revenue_per_trip: {
    group: "means",
    label: "Revenue / trip",
    about: "Revenue divided by trips. Every published row has a total, so this one denominator is exact — unlike the means below it, which are built on the rows that had a usable value.",
    of: (z) => ratio(z.revenue, z.trips),
    full: (v) => "$" + nf2.format(v),
    short: (v) => "$" + nf1.format(v),
  },
  avg_fare_usd: {
    group: "means",
    label: "Mean fare",
    about: "<code>fare_amount</code> only: the metered fare, without tip, tolls or surcharges. Lower than revenue per trip everywhere, and the gap is itself the interesting part.",
    of: (z) => z.avg_fare_usd,
    full: (v) => "$" + nf2.format(v),
    short: (v) => "$" + Math.round(v),
  },
  avg_distance_mi: {
    group: "means",
    label: "Mean distance",
    about: "Miles per trip. The mean is pulled up by a handful of enormous rows — the table at the bottom of the page puts it next to the median.",
    of: (z) => z.avg_distance_mi,
    full: (v) => nf2.format(v) + " mi",
    short: (v) => nf1.format(v),
  },
  avg_duration_min: {
    group: "means",
    label: "Mean duration",
    about: "Minutes per trip, from pickup to dropoff. Non-positive durations are nulled by a quality rule, so they do not drag this down.",
    of: (z) => z.avg_duration_min,
    full: (v) => nf1.format(v) + " min",
    short: (v) => nf1.format(v),
  },
  speed_mph: {
    group: "ratios",
    label: "Implied speed",
    about: "Mean distance over mean duration — a ratio of two means, not the mean of per-trip speeds, and the two are built on different sets of rows. Where a zone's distance coverage sits far below its duration coverage the result is an artefact of that gap rather than a speed, which is why the tooltip carries both.",
    of: (z) => ratio(z.avg_distance_mi, z.avg_duration_min, 60),
    full: (v) => nf1.format(v) + " mph",
    short: (v) => nf1.format(v),
  },
  fare_per_mile: {
    group: "ratios",
    label: "Fare / mile",
    about: "Mean fare over mean distance. High where trips are short and stop-start, low where they are long runs — the meter charges for time as well as distance.",
    of: (z) => ratio(z.avg_fare_usd, z.avg_distance_mi),
    full: (v) => "$" + nf2.format(v) + " / mi",
    short: (v) => "$" + nf1.format(v),
  },
};

const BUCKETS = 6;
let metric = "trips";

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

function drawMap(host) {
  const M = DATA.map;
  const spec = METRICS[metric];
  host.querySelectorAll("svg").forEach((n) => n.remove());

  const stats = new Map(M.zones.map((z) => [String(z.location_id), z]));
  const values = Object.keys(M.paths)
    .map((id) => stats.get(id))
    .filter((z) => z && spec.of(z) != null && isFinite(spec.of(z)))
    .map(spec.of);
  const edges = quantileEdges(values, BUCKETS);
  const lows = [Math.min(...values), ...edges];

  const svg = el("svg", {
    viewBox: `0 0 ${M.width} ${M.height}`,
    role: "img",
    "aria-label": spec.label + " by pickup zone across the whole history",
  });
  // Bublina přežívá překreslení (visí na hostu, ne na SVG), takže po přepnutí metriky
  // je jinak vidět dál -- s čísly, co jsou napůl z předchozího pohledu.
  const tip = tipFor(host);
  tip.hidden = true;
  // Zvýraznění je jedna cesta navrch, ne silnější obrys na místě: SVG nemá z-index,
  // takže obrys zóny kreslené dřív by zmizel pod jejími sousedy.
  const highlight = el("path", { class: "hl", d: "" });

  for (const [id, d] of Object.entries(M.paths)) {
    const z = stats.get(id);
    const value = z ? spec.of(z) : null;
    const known = value != null && isFinite(value);
    // Jméno z curated (tam ho pipeline joinovala z lookupu), a když zóna v curated
    // není, tak aspoň jméno ze shapefilu.
    const named = z ? [z.zone, z.borough] : M.names[id] || ["zone " + id, ""];

    // Tři různé stavy, ne dva: zóna bez řádku v curated, zóna s jízdami ale bez
    // pozorování, ze kterého by šel průměr spočítat, a zóna s číslem.
    const missing = z ? "no " + spec.label.toLowerCase() + " recorded" : "no trips in the data";
    // Bublina nese celý profil zóny, ne jen obarvenou metriku, a tučně je v něm to, čím
    // je zrovna obarveno. Přepínač tak nemění, co se dá přečíst -- jen kam se dívat.
    const cell = (key) => {
      const s = METRICS[key];
      const v = z ? s.of(z) : null;
      const text = v == null || !isFinite(v) ? "—" : (s.brief || s.full)(v);
      return key === metric ? `<b>${text}</b>` : text;
    };
    const shape = el("path", { d, fill: known ? `var(--m${bucketOf(value, edges) + 1})` : "var(--m0)" });
    shape.appendChild(el("title", {}, named[0] + ": " + (known ? spec.full(value) : missing)));

    // Bez tabindex: 263 zón by znamenalo 263 zastávek tabulátoru mezi grafy. Táž čísla
    // jsou v tabulkách níž, mapa je jejich obrázek.
    shape.addEventListener("mousemove", (event) => {
      highlight.setAttribute("d", d);
      const box = host.getBoundingClientRect();
      tip.innerHTML = z
        ? [
            `<b>${named[0]}</b> · ${named[1]}`,
            `${cell("trips")} · ${cell("revenue")}`,
            `<span class="r">${cell("revenue_per_trip")} / trip · ${cell("avg_fare_usd")} fare`
              + ` · ${cell("avg_distance_mi")} · ${cell("avg_duration_min")}</span>`,
            `<span class="r">${cell("speed_mph")} · ${cell("fare_per_mile")}</span>`,
            `<span class="r">coverage ${orDash(z.coverage, pct)} distance`
              + ` · ${orDash(z.duration_coverage, pct)} duration</span>`,
          ].concat(known ? [] : [`<span class="r">${missing}</span>`]).join("<br>")
        : `<b>${named[0]}</b> · ${named[1]}<br><span class="r">${missing}</span>`;
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

  document.getElementById("map-legend").innerHTML =
    `<div><div class="steps">${lows.map((_, i) => `<i style="background:var(--m${i + 1})"></i>`).join("")}</div>`
    + `<div class="edges">${lows.map((v) => `<span>${spec.short(v)}</span>`).join("")}</div></div>`
    + `<span class="nodata"><i class="swatch" style="background:var(--m0)"></i>no trips in the data</span>`;

  document.getElementById("map-title").textContent = spec.label + " by pickup zone";
  document.getElementById("map-about").innerHTML = spec.about;
  document.querySelectorAll("#metric-picker .mbtn").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.metric === metric));
  });

  const off = M.off_map;
  document.getElementById("map-foot").textContent =
    Object.keys(M.paths).length + " zones · " + SPAN
    + " (the whole history, not the month selected below) · " + nf.format(off.trips)
    + " trips sit in " + off.zones + " codes that have no shape on any map (unknown, outside NYC)";
}

/* ---------- tables ---------- */

function buildDailyTable(m) {
  document.getElementById("daily-table").innerHTML =
    "<thead><tr><th>Day</th><th class='num'>Trips</th><th class='num'>Revenue</th><th class='num'>Refunds</th></tr></thead><tbody>"
    + m.daily.map((d) =>
      `<tr><td class="mono">${d.date}${isWeekend(d.date) ? " · weekend" : ""}</td><td class="num">${nf.format(d.trips)}</td><td class="num">${usd(d.revenue)}</td><td class="num">${usd(d.refunds)}</td></tr>`
    ).join("") + "</tbody>";
}

function buildZoneTable(m) {
  document.getElementById("ztable").innerHTML = m.zones.slice(0, 12).map((z) => {
    const low = z.coverage != null && z.coverage < 0.9;
    return `<tr>
      <td>${z.zone}</td>
      <td class="dim">${z.borough}</td>
      <td class="num">${nf.format(z.trips)}</td>
      <td class="num">${orDash(z.avg_distance_mi, (v) => nf2.format(v))}</td>
      <td class="num">${orDash(z.median_distance_mi, (v) => nf2.format(v))}</td>
      <td class="num" ${low ? 'style="color:var(--s2)"' : ""}>${orDash(z.coverage, (v) => nf1.format(v * 100) + " %")}</td>
      <td class="num">${orDash(z.avg_fare_usd, (v) => "$" + nf2.format(v))}</td>
    </tr>`;
  }).join("");
}

/* ---------- static parts ---------- */

function buildHeader() {
  buildEyebrow();

  const f = DATA.freshness;
  buildChips([
    { dot: "good", text: plural(MONTHS.length, "month") + " · " + SPAN },
    { dot: "good", text: "newest month published by the city " + f.source_age_days + " days ago (" + f.source_newest + ")" },
    { dot: "info", text: "built " + DATA.generated_at },
  ]);

  const trips = MONTHS.reduce((a, m) => a + m.trips, 0);
  const revenue = MONTHS.reduce((a, m) => a + m.revenue, 0);
  // Nejvytíženější zóna se bere z mapy, protože ta jediná nese celou historii; měsíční
  // `zones` je jen top 25 za jeden měsíc.
  const busiest = DATA.map.zones.reduce((a, z) => (z.trips > a.trips ? z : a));

  buildTiles([
    { k: "Trips", v: nf1.format(trips / 1e6) + "M", s: nf.format(trips) + " over " + plural(MONTHS.length, "month") },
    { k: "Fares paid", v: usdBig(revenue), s: "gross, tips and tolls included" },
    { k: "Average ride", v: "$" + nf2.format(revenue / trips), s: "revenue per trip, whole history" },
    { k: "Busiest pickup", v: busiest.zone, s: nf.format(busiest.trips) + " trips · " + pct(busiest.trips / trips) + " of all", name: true },
  ]);

  document.getElementById("foot").textContent =
    "Built " + DATA.generated_at + " from " + plural(MONTHS.length, "Parquet file") + " covering " + SPAN + ".";
}

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
      drawMap(document.getElementById("zonemap"));
    });
  });
}

/* ---------- render ---------- */

function render() {
  const m = month();
  syncPicker();

  drawHistory(document.getElementById("history"));
  drawDaily(document.getElementById("daily"), m);

  document.getElementById("zones-cap").textContent =
    "By published trips in " + label(m) + ", attributed to the pickup zone.";

  const zones = m.zones.slice(0, 10).map((z) => ({
    label: z.zone,
    full: z.zone,
    value: z.trips,
    display: nf.format(z.trips),
    tip: `${nf.format(z.trips)} trips · ${z.borough}<br><span class="r">${usdM(z.revenue)} revenue · mean ${orDash(z.avg_distance_mi, (v) => nf2.format(v) + " mi")}</span>`,
  }));
  drawBars(document.getElementById("zones"), zones, { labelW: 170, valueW: 76, title: "Pickup zones by trip count" });

  buildZoneTable(m);
}

buildHeader();
buildPicker();
buildMetricPicker();
// Mapa je za celou historii a škáluje ji CSS, takže se kreslí jednou -- ani měsíční
// přepínač, ani resize s ní nehnou.
drawMap(document.getElementById("zonemap"));
render();
watchResize();
