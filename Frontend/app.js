import { API_BASE, getInvestigations, getMeasurements, getStats, getSteps, getTests, isOnline } from "./api.js";
import { setupChat } from "./chat.js";

const SVG = "http://www.w3.org/2000/svg";
const $ = (sel) => document.querySelector(sel);

// Three outcome classes for color (the 11 detailed outcomes go in the tooltip).
const CLASSES = [
  { key: "ext",   label: "Self-extinguished",  color: "var(--out-ext)",   test: (r) => r.self_extinguished === true },
  { key: "burn",  label: "Kept burning",       color: "var(--out-burn)",  test: (r) => r.self_extinguished === false },
  { key: "other", label: "Other / no outcome", color: "var(--out-other)", test: () => true, hollow: true },
];
const classOf = (r) => CLASSES.find((c) => c.test(r));

// Reference atmospheres (context, not from the database).
const REFERENCES = [
  { label: "ISS cabin (21 %, 101 kPa)", o2: 0.21, p: 101.3 },
  // NASA Exploration Atmospheres Working Group: 8.2 psia / 34 % O2, proposed for lunar habitats.
  { label: "Exploration habitat (34 %, 56.5 kPa)", o2: 0.34, p: 56.5 },
];

const GROUP_NAMES = { droplet: "Droplets", gas: "Gaseous flames", solid: "Solids", aerosol: "Smoke and aerosols", ground: "Ground studies" };

const fmt = (v, d = 2) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
const fmtAuto = (v) => { const a = Math.abs(v); return fmt(v, a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 4); };
const pct = (v) => (v == null ? "—" : `${fmt(v * 100, 1)} %`);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function el(name, attrs = {}, parent) {
  const n = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

// ------------------------------------------------------------------ tooltip
const tip = $("#tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const { width, height } = tip.getBoundingClientRect();
  const left = Math.min(x + 14, window.innerWidth - width - 8);
  const top = y + height + 20 > window.innerHeight ? y - height - 10 : y + 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}
const hideTip = () => { tip.hidden = true; };
const row = (k, v) => `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`;

// ------------------------------------------------------------------ state
const state = { tests: [], invs: [], group: "", inv: "", mat: "", selected: null, cell: null, cellView: null };

function filtered() {
  return state.tests.filter((r) =>
    (!state.group || r.family_group === state.group) &&
    (!state.inv || r.psi_id === state.inv) &&
    (!state.mat || r.material_code === state.mat));
}

// ------------------------------------------------------------------ tiles
function renderTiles(s) {
  const tiles = [
    [s.n_investigations, "combustion investigations"],
    [fmt(s.n_tests, 0), "tests loaded"],
    [fmt(s.n_tests_with_outcome, 0), "tests with a flame outcome"],
    [s.n_log_entries, "operations-day and log records"],
    [`${s.n_tables_mapped} / ${s.n_tables_total}`, "experimental tables mapped"],
    [s.n_documents, "documents for the search service"],
  ];
  $("#tiles").innerHTML = tiles.map(([v, l]) => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
}

function legendItem(c) {
  const sw = c.hollow
    ? `<svg width="12" height="12"><circle cx="6" cy="6" r="4" fill="none" stroke="${c.color}" stroke-width="2"/></svg>`
    : `<svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="${c.color}"/></svg>`;
  return `<li>${sw}${esc(c.label)}</li>`;
}

// ------------------------------------------------------------------ O2 vs pressure heatmap
// One cell = a range of oxygen x a range of pressure. Colour = share of the tests with a known
// outcome that self-extinguished; strength = how many tests the cell holds; empty cell = NASA
// has no test there (that is the point of the map: where the evidence ends).
const O2_BIN = 0.025, P_BINS = 16;

const cellKey = (i, j) => `${i}:${j}`;
const mixColor = (frac) => `color-mix(in srgb, var(--out-ext) ${Math.round(frac * 100)}%, var(--out-burn))`;

function legendMap() {
  return [
    `<li><span class="grad"></span>Mostly kept burning → mostly self-extinguished</li>`,
    `<li><svg width="12" height="12"><rect width="12" height="12" rx="2" fill="var(--out-other)" opacity=".6"/></svg>Only other / no outcome</li>`,
    `<li><svg width="12" height="12"><rect width="12" height="12" rx="2" fill="var(--grid)" opacity=".6"/></svg>No NASA test</li>`,
    `<li>Stronger colour = more tests</li>`,
    `<li><svg width="12" height="12"><path d="M1 6H11M6 1V11" stroke="var(--ref)" stroke-width="2"/></svg>Reference atmosphere</li>`,
  ].join("");
}

function renderMap(tests) {
  const box = $("#chart-map");
  box.innerHTML = "";
  const valid = tests.filter((r) => r.is_valid);
  const pts = valid.filter((r) => r.o2_frac != null && r.pressure_kpa != null);
  const missing = tests.length - pts.length;
  $("#note-map").textContent = (missing ? `${missing} test(s) in this filter have no O2 or pressure (e.g. BASS, SAFFIRE, SPICE) or are flagged invalid, and are not plotted. ` : "") +
    "For inverse flames (CFI-G, Flame Design) O2 is the burner jet, not the ambient. Click a cell to list its tests.";
  if (!pts.length) {
    box.innerHTML = `<p class="empty">No test in this filter reports both O2 and pressure. Solids (BASS, SAFFIRE) and smoke (SAME) ran at cabin pressure: see their outcomes below.</p>`;
    return;
  }

  // Drawn at the container's real width so text stays at 11-12 px.
  const W = Math.max(300, box.clientWidth), H = W < 560 ? 400 : 540;
  const m = { t: 14, r: 20, b: 48, l: W < 560 ? 48 : 58 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const nx = Math.ceil((Math.max(0.4, ...pts.map((r) => r.o2_frac)) + 1e-9) / O2_BIN);
  const xMax = nx * O2_BIN;
  const pMin = Math.min(40, ...pts.map((r) => r.pressure_kpa)) * 0.99;
  const pMax = Math.max(600, ...pts.map((r) => r.pressure_kpa)) * 1.001;
  const lg = Math.log(pMax / pMin), ratio = Math.exp(lg / P_BINS);   // pressure bins are equal on a log axis
  const cw = iw / nx, ch = ih / P_BINS;
  const x = (v) => m.l + (v / xMax) * iw;
  const y = (v) => m.t + ih - (Math.log(v / pMin) / lg) * ih;
  const edge = (j) => pMin * Math.pow(ratio, j);

  const cells = new Map();
  for (const r of pts) {
    const i = Math.min(nx - 1, Math.floor(r.o2_frac / O2_BIN + 1e-9));
    const j = Math.min(P_BINS - 1, Math.max(0, Math.floor(Math.log(r.pressure_kpa / pMin) / Math.log(ratio))));
    const k = cellKey(i, j);
    if (!cells.has(k)) cells.set(k, { i, j, tests: [], ext: 0, burn: 0, other: 0 });
    const c = cells.get(k);
    c.tests.push(r);
    c[classOf(r).key]++;
  }
  const maxN = Math.max(...[...cells.values()].map((c) => c.tests.length));
  const strength = (n) => Math.log(1 + n) / Math.log(1 + maxN);

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Heat map of tests: oxygen mole fraction against pressure; colour is the share that self-extinguished" }, box);

  // Background: every cell of the grid, so empty cells (no NASA test) are visible.
  for (let i = 0; i < nx; i++) for (let j = 0; j < P_BINS; j++) {
    if (cells.has(cellKey(i, j))) continue;
    el("rect", { x: m.l + i * cw + 1, y: m.t + ih - (j + 1) * ch + 1, width: cw - 2, height: ch - 2, rx: 3, fill: "var(--grid)", opacity: 0.45 }, svg);
  }

  // Axes
  for (let v = 0; v <= xMax + 1e-9; v += W < 560 ? 0.2 : 0.1) {
    el("text", { x: x(v), y: m.t + ih + 16, "text-anchor": "middle" }, svg).textContent = `${Math.round(v * 100)} %`;
  }
  for (const v of [40, 50, 75, 100, 150, 200, 300, 500]) {
    if (v < pMin || v > pMax) continue;
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v, 0);
  }
  el("text", { class: "axis-title", x: m.l + iw / 2, y: H - 8, "text-anchor": "middle" }, svg).textContent = "Oxygen (mole fraction)";
  el("text", { class: "axis-title", transform: `translate(14 ${m.t + ih / 2}) rotate(-90)`, "text-anchor": "middle" }, svg).textContent = "Pressure (kPa, log scale)";

  // Cells with tests
  const rects = new Map();
  for (const [k, c] of cells) {
    const n = c.tests.length, decided = c.ext + c.burn;
    const s = strength(n);
    const style = decided
      ? `fill:${mixColor(c.ext / decided)};opacity:${(0.35 + 0.65 * s).toFixed(2)}`
      : `fill:var(--out-other);opacity:${(0.25 + 0.4 * s).toFixed(2)}`;
    const rect = el("rect", { class: "cell", x: m.l + c.i * cw + 1, y: m.t + ih - (c.j + 1) * ch + 1, width: cw - 2, height: ch - 2, rx: 3, style }, svg);
    if (state.cell === k) rect.classList.add("selected");
    rects.set(k, rect);
    if (cw >= 22 && ch >= 20) {
      el("text", { class: "cell-n", x: m.l + (c.i + 0.5) * cw, y: m.t + ih - (c.j + 0.5) * ch + 4, "text-anchor": "middle", "pointer-events": "none" }, svg).textContent = n;
    }
  }

  for (const ref of REFERENCES) {
    if (ref.o2 > xMax || ref.p > pMax || ref.p < pMin) continue;
    const cx = x(ref.o2), cy = y(ref.p);
    el("path", { d: `M${cx - 8} ${cy}H${cx + 8}M${cx} ${cy - 8}V${cy + 8}`, stroke: "var(--ref)", "stroke-width": 2.5, "pointer-events": "none" }, svg);
    const right = cx < m.l + iw * 0.7;
    el("text", { class: "ref-label", x: cx + (right ? 12 : -12), y: cy - 10, "text-anchor": right ? "start" : "end", "pointer-events": "none" }, svg).textContent = ref.label;
  }

  // Pointer -> cell (the grid is regular, so no search is needed).
  const locate = (ev) => {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    if (p.x < m.l || p.x > m.l + iw || p.y < m.t || p.y > m.t + ih) return null;
    const i = Math.min(nx - 1, Math.floor((p.x - m.l) / cw));
    const j = Math.min(P_BINS - 1, Math.floor((m.t + ih - p.y) / ch));
    return { i, j, key: cellKey(i, j), cell: cells.get(cellKey(i, j)) };
  };
  const ranges = (i, j) => ({ o2: `${fmt(i * O2_BIN * 100, 1)}–${fmt((i + 1) * O2_BIN * 100, 1)} %`, p: `${fmtAuto(edge(j))}–${fmtAuto(edge(j + 1))} kPa` });
  svg.addEventListener("mousemove", (ev) => {
    const h = locate(ev);
    svg.style.cursor = h?.cell ? "pointer" : "default";
    if (!h) return hideTip();
    const rg = ranges(h.i, h.j);
    if (!h.cell) return showTip(`<b>No NASA test here</b>` + row("Oxygen", rg.o2) + row("Pressure", rg.p), ev.clientX, ev.clientY);
    const c = h.cell, acr = [...new Set(c.tests.map((r) => r.acronym))];
    showTip(`<b>${c.tests.length} test${c.tests.length > 1 ? "s" : ""}</b>` + row("Oxygen", rg.o2) + row("Pressure", rg.p) +
      row("Self-extinguished", c.ext) + row("Kept burning", c.burn) + row("Other / no outcome", c.other) +
      row("Experiments", acr.slice(0, 3).join(", ") + (acr.length > 3 ? ` +${acr.length - 3}` : "")), ev.clientX, ev.clientY);
  });
  svg.addEventListener("mouseleave", hideTip);
  svg.addEventListener("click", (ev) => {
    const h = locate(ev);
    if (!h?.cell) return;
    rects.forEach((r) => r.classList.remove("selected"));
    rects.get(h.key).classList.add("selected");
    showCell(h.cell, h.key, ranges(h.i, h.j));
  });
}

// List of the tests of one cell, in the detail card; pick one to see its measurements.
function showCell(c, key, rg) {
  state.cell = key;
  state.cellView = { c, key, rg };
  state.selected = null;
  $("#detail-card").hidden = false;
  $("#detail-sub").textContent = `Oxygen ${rg.o2} · pressure ${rg.p} · ${c.tests.length} test${c.tests.length > 1 ? "s" : ""}`;
  const order = { ext: 0, burn: 1, other: 2 };
  const list = [...c.tests].sort((a, b) => order[classOf(a).key] - order[classOf(b).key] || a.test_id.localeCompare(b.test_id));
  $("#detail").innerHTML = `<ul class="cell-list">${list.map((r) =>
    `<li><button type="button" data-id="${esc(r.test_id)}"><span class="swatch" style="background:${classOf(r).hollow ? "transparent" : classOf(r).color};border-color:${classOf(r).color}"></span>` +
    `<b>${esc(r.acronym)}</b> ${esc(r.test_id)}<span class="meta">${esc(r.outcome_label)}${r.material ? " · " + esc(r.material) : ""}</span></button></li>`).join("")}</ul>`;
  $("#detail").querySelectorAll("button[data-id]").forEach((b) =>
    b.addEventListener("click", () => selectTest(c.tests.find((r) => r.test_id === b.dataset.id))));
  $("#detail-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ------------------------------------------------------------------ bars by experiment
function renderBars(tests) {
  const box = $("#chart-bars");
  box.innerHTML = "";
  if (!tests.length) { box.innerHTML = `<p class="empty">No tests in this filter.</p>`; return; }

  const groups = new Map();
  for (const r of tests) {
    const key = `${r.acronym} · ${r.source_table}`;
    // "CFI-G_Normal Flames" -> "CFI-G · Normal Flames"; single-name tables use the acronym.
    const label = r.source_table.includes("_") ? r.source_table.replace(/_/g, " · ") : r.acronym;
    if (!groups.has(key)) groups.set(key, { key, label, tests: [] });
    groups.get(key).tests.push(r);
  }
  const rows = [...groups.values()].sort((a, b) => b.tests.length - a.tests.length);
  const max = Math.max(...rows.map((g) => g.tests.length));

  const W = Math.max(300, box.clientWidth), rowH = 30, labelW = W < 560 ? 150 : 210, m = { t: 4, r: 40, b: 24 };
  const H = m.t + rows.length * rowH + m.b;
  const iw = W - labelW - m.r;
  const xs = (v) => (v / max) * iw;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Stacked bars: tests by outcome for each experiment" }, box);

  const tickStep = (max > 200 ? 50 : max > 50 ? 25 : max > 10 ? 5 : 1) * (W < 560 ? 2 : 1);
  for (let v = 0; v <= max; v += tickStep) {
    el("line", { class: "gridline", x1: labelW + xs(v), x2: labelW + xs(v), y1: m.t, y2: H - m.b }, svg);
    el("text", { x: labelW + xs(v), y: H - 6, "text-anchor": "middle" }, svg).textContent = v;
  }

  rows.forEach((g, i) => {
    const yTop = m.t + i * rowH + 7, bh = rowH - 14;
    el("text", { x: labelW - 10, y: yTop + bh / 2 + 4, "text-anchor": "end", class: "axis-title" }, svg).textContent = g.label;
    let x0 = labelW;
    for (const c of CLASSES) {
      const seg = g.tests.filter((r) => classOf(r) === c);
      if (!seg.length) continue;
      const w = Math.max(xs(seg.length) - 2, 1.5);   // 2 px gap between segments
      const rect = el("rect", { x: x0, y: yTop, width: w, height: bh, rx: 3, fill: c.hollow ? "var(--surface)" : c.color,
        stroke: c.hollow ? c.color : "none", "stroke-width": c.hollow ? 1.5 : 0 }, svg);
      const detail = Object.entries(seg.reduce((acc, r) => ((acc[r.outcome_label] = (acc[r.outcome_label] || 0) + 1), acc), {}));
      rect.addEventListener("mousemove", (ev) => showTip(`<b>${esc(g.label)}</b>` + row(c.label, seg.length) +
        detail.map(([k, n]) => row(`· ${k}`, n)).join(""), ev.clientX, ev.clientY));
      rect.addEventListener("mouseleave", hideTip);
      x0 += xs(seg.length);
    }
    el("text", { x: x0 + 6, y: yTop + bh / 2 + 4, class: "ref-label" }, svg).textContent = g.tests.length;
  });
  el("line", { class: "baseline", x1: labelW, x2: labelW, y1: m.t, y2: H - m.b }, svg);
}

// ------------------------------------------------------------------ detail
async function selectTest(r) {
  state.selected = r.test_id;
  $("#detail-sub").textContent = `${r.acronym} · ${r.test_id}`;
  $("#detail-card").hidden = false;
  const [ms, steps] = await Promise.all([getMeasurements(r.test_id), getSteps(r.test_id)]);
  const fields = [
    ["Outcome", r.outcome_label + (r.outcome_status === "provisional" ? " (provisional)" : "")],
    ["Material", r.material], ["Date", r.test_date], ["Flame type", r.flame_type], ["Flow direction", r.flow_direction],
    ["Valid", r.is_valid ? "yes" : `no — ${r.invalid_reason}`], ["Comment", r.comment],
  ].filter(([, v]) => v != null && v !== "");
  const value = (m) => {
    if (m.value_num != null) return `${m.is_approx ? "≈ " : ""}${fmtAuto(m.value_num)} ${m.unit === "1" ? "" : m.unit}`;
    if (m.flag === "censored") return `${m.censored} ${fmtAuto(m.censor_limit)} ${m.unit}`;
    return m.value_text;
  };
  const flags = (r.quality_flags || []).filter((f) => f !== "outcome_not_in_table");
  const back = state.cellView ? `<button type="button" class="back" id="back-cell">← All ${state.cellView.c.tests.length} tests in this cell</button>` : "";
  $("#detail").innerHTML = back +
    `<dl>${fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` +
    `<h3>Measurements</h3><dl>${ms.map((m) => `<dt>${esc(m.variable_label)}${m.measured_or_calculated === "calculated" ? " (calc.)" : ""}</dt><dd>${esc(value(m))}${
      m.flag && m.flag !== "approx" ? ` <span class="pill">${esc(m.flag)}</span>` : ""}</dd>`).join("")}</dl>` +
    (steps.length ? `<h3>Flow sequence</h3><p class="flags">${esc(steps.map((s) => s.flow_cm_s ?? s.note).join(" → "))} cm/s · “${esc(steps[0].raw)}”</p>` : "") +
    (flags.length ? `<p class="flags">Quality flags: ${esc(flags.join(", "))}</p>` : "") +
    `<p class="flags">Source: <a href="${esc(r.doi_url)}" target="_blank" rel="noopener">${esc(r.doi_url)}</a> · ${esc(r.source_file)}, row ${r.source_row}</p>`;
  $("#back-cell")?.addEventListener("click", () => showCell(state.cellView.c, state.cellView.key, state.cellView.rg));
  $("#detail-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ------------------------------------------------------------------ investigations table
function renderInvestigations(invs) {
  const rows = [...invs].sort((a, b) => b.n_tests - a.n_tests || b.n_log_entries - a.n_log_entries || b.rag_value_rank - a.rag_value_rank);
  const period = (i) => (i.start_date || i.end_date ? `${i.start_date?.slice(0, 4) ?? "?"}–${i.end_date?.slice(0, 4) ?? "?"}` : "—");
  $("#inv-table").innerHTML = `
    <thead><tr><th>PSI</th><th>Acronym</th><th>Title</th><th>Family</th><th>Period</th>
      <th class="num">Tests</th><th class="num">With outcome</th><th class="num">Log</th><th class="num">Docs</th><th>DOI</th></tr></thead>
    <tbody>${rows.map((i) => `<tr>
      <td>${esc(i.psi_id)}</td><td>${esc(i.acronym ?? "—")}</td><td class="title">${esc(i.title)}</td>
      <td><span class="pill">${esc(GROUP_NAMES[i.family_group] ?? i.family_group)}</span></td>
      <td>${period(i)}</td>
      <td class="num">${i.n_tests || "—"}</td><td class="num">${i.n_tests_with_outcome || "—"}</td>
      <td class="num">${i.n_log_entries || "—"}</td><td class="num">${i.n_documents || "—"}</td>
      <td><a href="${esc(i.doi_url)}" target="_blank" rel="noopener">${esc(i.doi)}</a></td></tr>`).join("")}</tbody>`;
}

// ------------------------------------------------------------------ filters
function fillSelect(sel, items) {
  const keep = sel.value;
  sel.innerHTML = sel.options[0].outerHTML + items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  sel.value = items.some(([v]) => v === keep) ? keep : "";
}

function refreshFilters() {
  const byGroup = state.tests.filter((r) => !state.group || r.family_group === state.group);
  const invs = [...new Map(byGroup.map((r) => [r.psi_id, `${r.acronym} (${r.psi_id})`])).entries()];
  fillSelect($("#f-inv"), invs.sort((a, b) => a[1].localeCompare(b[1])));
  state.inv = $("#f-inv").value;
  const byInv = byGroup.filter((r) => !state.inv || r.psi_id === state.inv);
  const mats = [...new Map(byInv.filter((r) => r.material_code).map((r) => [r.material_code, r.material])).entries()];
  fillSelect($("#f-mat"), mats.sort((a, b) => a[1].localeCompare(b[1])));
  state.mat = $("#f-mat").value;
}

function render() {
  const tests = filtered();
  $("#f-count").textContent = `${tests.length} of ${state.tests.length} tests`;
  renderMap(tests);
  renderBars(tests);
}

for (const [id, key] of [["#f-group", "group"], ["#f-inv", "inv"], ["#f-mat", "mat"]]) {
  $(id).addEventListener("change", (e) => { state[key] = e.target.value; state.cell = null; state.cellView = null; refreshFilters(); render(); });
}

// ------------------------------------------------------------------ boot
// The chat does not need the dashboard data: start it first, so a question sent while the charts load is not
// submitted as a plain form (which would reload the page and lose ?api=).
setupChat();
try {
  $("#mode").textContent = isOnline() ? `Live API · ${new URL(API_BASE).host}` : "Offline data";
  const [stats, invs, tests] = await Promise.all([getStats(), getInvestigations(), getTests()]);
  Object.assign(state, { tests, invs });
  renderTiles(stats);
  $("#legend-map").innerHTML = legendMap();
  $("#legend-bars").innerHTML = CLASSES.map(legendItem).join("");
  renderInvestigations(invs);
  refreshFilters();
  render();
  let raf;
  new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(render); }).observe($("#chart-bars"));
} catch (err) {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">Could not load the data: ${esc(err.message)}</p>`);
  console.error(err);
}
