import { API_BASE, ask, getInvestigations, getMeasurements, getStats, getSteps, getTests, isOnline } from "./api.js";

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
const state = { tests: [], invs: [], group: "", inv: "", mat: "", selected: null };

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

// ------------------------------------------------------------------ O2 vs pressure map
function renderMap(tests) {
  const box = $("#chart-map");
  box.innerHTML = "";
  const valid = tests.filter((r) => r.is_valid);
  const pts = valid.filter((r) => r.o2_frac != null && r.pressure_kpa != null);
  const missing = tests.length - pts.length;
  $("#note-map").textContent = (missing ? `${missing} test(s) in this filter have no O2 or pressure (e.g. BASS, SAFFIRE, SPICE) or are flagged invalid, and are not plotted. ` : "") +
    "For inverse flames (CFI-G, Flame Design) O2 is the burner jet, not the ambient.";
  if (!pts.length) {
    box.innerHTML = `<p class="empty">No test in this filter reports both O2 and pressure. Solids (BASS, SAFFIRE) and smoke (SAME) ran at cabin pressure: see their outcomes below.</p>`;
    return;
  }

  // Drawn at the container's real width so text stays at 11–12 px.
  const W = Math.max(300, box.clientWidth), H = W < 560 ? 420 : 560;
  const m = { t: 14, r: 20, b: 48, l: W < 560 ? 48 : 58 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xMax = Math.max(0.4, ...pts.map((r) => r.o2_frac)) * 1.05;
  // Pressure on a log axis: most tests sit at 70-220 kPa and would otherwise pile up in one band.
  const yMin = Math.min(40, ...pts.map((r) => r.pressure_kpa)) * 0.9;
  const yMax = Math.max(150, ...pts.map((r) => r.pressure_kpa)) * 1.15;
  const lg = Math.log(yMax / yMin);
  const x = (v) => m.l + (v / xMax) * iw;
  const y = (v) => m.t + ih - (Math.log(v / yMin) / lg) * ih;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Scatter plot of tests: oxygen mole fraction against pressure, colored by outcome" }, box);
  const step = xMax > 0.6 ? (W < 560 ? 0.2 : 0.1) : 0.05;
  for (let v = 0; v <= xMax + 1e-9; v += step) {
    el("line", { class: "gridline", x1: x(v), x2: x(v), y1: m.t, y2: m.t + ih }, svg);
    el("text", { x: x(v), y: m.t + ih + 16, "text-anchor": "middle" }, svg).textContent = `${Math.round(v * 100)} %`;
  }
  for (const v of [40, 50, 75, 100, 150, 200, 300, 500, 750]) {
    if (v < yMin || v > yMax) continue;
    el("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v, 0);
  }
  el("line", { class: "baseline", x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }, svg);
  el("text", { class: "axis-title", x: m.l + iw / 2, y: H - 8, "text-anchor": "middle" }, svg).textContent = "Oxygen (mole fraction)";
  el("text", { class: "axis-title", transform: `translate(14 ${m.t + ih / 2}) rotate(-90)`, "text-anchor": "middle" }, svg).textContent = "Pressure (kPa, log scale)";

  // Overlap: tests with (nearly) the same conditions land on the same pixel. Group them by
  // screen cell and fan each group out in a sunflower pattern around its true position,
  // with the same outcomes side by side, so every test stays visible and clickable.
  const CELL = 12, SPREAD = 5.2;
  const rank = { ext: 0, burn: 1, other: 2 };
  const cells = new Map();
  for (const r of pts) {
    const key = `${Math.round(x(r.o2_frac) / CELL)},${Math.round(y(r.pressure_kpa) / CELL)}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(r);
  }
  const dots = [];
  let fanned = 0;
  for (const group of cells.values()) {
    group.sort((a, b) => rank[classOf(a).key] - rank[classOf(b).key] || a.test_id.localeCompare(b.test_id));
    const cx0 = group.reduce((s, r) => s + x(r.o2_frac), 0) / group.length;
    const cy0 = group.reduce((s, r) => s + y(r.pressure_kpa), 0) / group.length;
    if (group.length > 1) fanned += group.length;
    group.forEach((r, i) => {
      const a = i * 2.399963, rad = group.length > 1 ? SPREAD * Math.sqrt(i + 0.5) : 0;
      dots.push({ r, cx: cx0 + rad * Math.cos(a), cy: cy0 + rad * Math.sin(a) });
    });
  }
  // "Other" first (bottom), so meaningful outcomes stay on top.
  dots.sort((a, b) => (classOf(a.r).key === "other" ? -1 : 0) - (classOf(b.r).key === "other" ? -1 : 0));
  for (const { r, cx, cy } of dots) {
    const c = classOf(r);
    const attrs = c.hollow
      ? { fill: "var(--surface)", stroke: c.color, "stroke-width": 1.5, r: 3.4 }
      : { fill: c.color, stroke: "var(--surface)", "stroke-width": 1, r: 3.8 };
    const d = el("circle", { class: "dot", cx, cy, ...attrs }, svg);
    d.dataset.id = r.test_id;
    if (state.selected === r.test_id) d.classList.add("selected");
  }
  if (fanned) $("#note-map").textContent += " Tests with (nearly) identical conditions are fanned out in a cluster; the centre of each cluster is the real value.";

  for (const ref of REFERENCES) {
    if (ref.o2 > xMax || ref.p > yMax) continue;
    const cx = x(ref.o2), cy = y(ref.p);
    el("path", { d: `M${cx - 7} ${cy}H${cx + 7}M${cx} ${cy - 7}V${cy + 7}`, stroke: "var(--ref)", "stroke-width": 2, "pointer-events": "none" }, svg);
    const right = cx < m.l + iw * 0.7;
    el("text", { class: "ref-label", x: cx + (right ? 10 : -10), y: cy - 8, "text-anchor": right ? "start" : "end", "pointer-events": "none" }, svg).textContent = ref.label;
  }

  // Hit target: the dot nearest to the pointer within 18 px, larger than the mark.
  const toLocal = (ev) => {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  };
  const nearest = (ev) => {
    const p = toLocal(ev);
    let best = null, bd = 9 * (W / svg.getBoundingClientRect().width);
    for (const d of dots) {
      const dist = Math.hypot(d.cx - p.x, d.cy - p.y);
      if (dist < bd) { bd = dist; best = d; }
    }
    return best;
  };
  svg.addEventListener("mousemove", (ev) => {
    const d = nearest(ev);
    svg.style.cursor = d ? "pointer" : "default";
    if (!d) return hideTip();
    const r = d.r;
    showTip(`<b>${esc(r.acronym)} · ${esc(r.test_id)}</b>` +
      row("Outcome", r.outcome_label + (r.outcome_status === "provisional" ? " (provisional)" : "")) +
      row("Material", r.material ?? "—") +
      row("O2", pct(r.o2_frac)) + row("Pressure", `${fmtAuto(r.pressure_kpa)} kPa`) +
      (r.burn_time_s != null ? row("Burn time", `${fmt(r.burn_time_s, 1)} s`) : "") +
      (r.flame_type ? row("Flame", r.flame_type) : ""), ev.clientX, ev.clientY);
  });
  svg.addEventListener("mouseleave", hideTip);
  svg.addEventListener("click", (ev) => { const d = nearest(ev); if (d) selectTest(d.r); });
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
  document.querySelectorAll(".dot.selected").forEach((n) => n.classList.remove("selected"));
  document.querySelector(`.dot[data-id="${CSS.escape(r.test_id)}"]`)?.classList.add("selected");
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
  $("#detail").innerHTML =
    `<dl>${fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` +
    `<h3>Measurements</h3><dl>${ms.map((m) => `<dt>${esc(m.variable_label)}${m.measured_or_calculated === "calculated" ? " (calc.)" : ""}</dt><dd>${esc(value(m))}${
      m.flag && m.flag !== "approx" ? ` <span class="pill">${esc(m.flag)}</span>` : ""}</dd>`).join("")}</dl>` +
    (steps.length ? `<h3>Flow sequence</h3><p class="flags">${esc(steps.map((s) => s.flow_cm_s ?? s.note).join(" → "))} cm/s · “${esc(steps[0].raw)}”</p>` : "") +
    (flags.length ? `<p class="flags">Quality flags: ${esc(flags.join(", "))}</p>` : "") +
    `<p class="flags">Source: <a href="${esc(r.doi_url)}" target="_blank" rel="noopener">${esc(r.doi_url)}</a> · ${esc(r.source_file)}, row ${r.source_row}</p>`;
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

// ------------------------------------------------------------------ ask
function setupAsk() {
  const form = $("#chat-form"), input = $("#chat-input"), btn = $("#chat-btn"), out = $("#answer");
  if (!isOnline()) {
    input.disabled = btn.disabled = true;
    $("#chat-sub").textContent = "Needs the backend API. This page is running offline on static data.";
    return;
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim() || input.placeholder;
    btn.disabled = true;
    out.innerHTML = `<p class="flags">Thinking…</p>`;
    try {
      const a = await ask(text);
      const p = a.predictions[0];
      out.innerHTML =
        `<p>${esc(a.text)}</p>` +
        (a.unknown_terms.length ? `<p class="flags">Not measured by NASA: ${esc(a.unknown_terms.join(", "))}</p>` : "") +
        (p ? `<p class="flags">Estimate: ${p.probability == null ? "—" : pct(p.probability)} self-extinguished` +
          (p.ci_low != null ? ` (95 % CI ${pct(p.ci_low)}–${pct(p.ci_high)})` : "") + ` · ${p.in_domain ? "inside" : "outside"} the tested range · ${esc(p.model_version)}</p>` : "") +
        (a.evidence.length ? `<h3>Closest NASA tests</h3><ul class="evidence">${a.evidence.map((e) =>
          `<li><b>${esc(e.test_id)}</b> · ${esc(e.outcome)} · ${esc(e.source)}</li>`).join("")}</ul>` : "") +
        (a.citations.length ? `<h3>Documents</h3><ul class="evidence">${a.citations.map((c) =>
          `<li>${esc(c.snippet)} <span class="flags">${esc(c.title ?? c.doc_id)}${c.page_or_section ? `, ${esc(c.page_or_section)}` : ""}</span></li>`).join("")}</ul>` : "");
    } catch (err) {
      out.innerHTML = `<p class="flags">Could not get an answer: ${esc(err.message)}</p>`;
    } finally {
      btn.disabled = false;
    }
  });
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
  $(id).addEventListener("change", (e) => { state[key] = e.target.value; refreshFilters(); render(); });
}

// ------------------------------------------------------------------ boot
try {
  $("#mode").textContent = isOnline() ? `Live API · ${new URL(API_BASE).host}` : "Offline data";
  const [stats, invs, tests] = await Promise.all([getStats(), getInvestigations(), getTests()]);
  Object.assign(state, { tests, invs });
  renderTiles(stats);
  $("#legend-map").innerHTML = CLASSES.map(legendItem).join("") +
    `<li><svg width="12" height="12"><path d="M1 6H11M6 1V11" stroke="var(--ref)" stroke-width="2"/></svg>Reference atmosphere</li>`;
  $("#legend-bars").innerHTML = CLASSES.map(legendItem).join("");
  renderInvestigations(invs);
  refreshFilters();
  render();
  setupAsk();
  let raf;
  new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(render); }).observe($("#chart-bars"));
} catch (err) {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">Could not load the data: ${esc(err.message)}</p>`);
  console.error(err);
}
