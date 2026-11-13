import { getStats, getInvestigations, getTestRuns, getMeasurements } from "./api.js";

const SVG = "http://www.w3.org/2000/svg";
const $ = (sel) => document.querySelector(sel);

// Tres clases de resultado para el color (las 9 categorías finas van en el tooltip).
const CLASSES = [
  { key: "ext",   label: "Se apagó sola",   color: "var(--out-ext)",   test: (r) => r.self_extinguished === true },
  { key: "burn",  label: "Siguió ardiendo", color: "var(--out-burn)",  test: (r) => r.self_extinguished === false },
  { key: "other", label: "Otro / sin dato", color: "var(--out-other)", test: () => true, hollow: true },
];
const classOf = (r) => CLASSES.find((c) => c.test(r));

// Atmósferas de referencia (no salen de la base; son datos de contexto).
const REFERENCES = [
  { label: "ISS (21 %, 1 atm)", o2: 0.21, p: 1.0 },
  // NASA Exploration Atmospheres Working Group: 8.2 psia / 34 % O2, propuesta para hábitats lunares.
  { label: "Hábitat de exploración (34 %, 0,56 atm)", o2: 0.34, p: 0.558 },
];

const GROUP_NAMES = { droplet: "Gotas", gas: "Llamas de gas", solid: "Sólidos", aerosol: "Humo y aerosoles", ground: "Tierra" };

const fmt = (v, d = 2) => (v == null ? "—" : Number(v).toLocaleString("es-AR", { maximumFractionDigits: d }));
// Decimales según magnitud: 2284 K, 19 s, 1,09 atm, 0,0981.
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

// ------------------------------------------------------------------ estado
const state = { runs: [], invs: [], group: "", inv: "", mat: "", selected: null };

function filtered() {
  return state.runs.filter((r) =>
    (!state.group || r.family_group === state.group) &&
    (!state.inv || r.accession === state.inv) &&
    (!state.mat || r.material_code === state.mat));
}

// ------------------------------------------------------------------ tiles
function renderTiles(s) {
  const tiles = [
    [s.n_investigations, "investigaciones de combustión"],
    [s.n_test_runs, "ensayos con datos estructurados"],
    [`${s.n_tables_mapped} / ${s.n_tables_total}`, "tablas experimentales mapeadas"],
    [s.n_rag_documents, "documentos para el asistente"],
    [`${fmt(s.total_tb, 1)} TB`, "publicados en PSI"],
  ];
  $("#tiles").innerHTML = tiles.map(([v, l]) => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
}

function legendItem(c) {
  const sw = c.hollow
    ? `<svg width="12" height="12"><circle cx="6" cy="6" r="4" fill="none" stroke="${c.color}" stroke-width="2"/></svg>`
    : `<svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="${c.color}"/></svg>`;
  return `<li>${sw}${esc(c.label)}</li>`;
}

// ------------------------------------------------------------------ mapa O2 vs presión
function renderMap(runs) {
  const box = $("#chart-map");
  box.innerHTML = "";
  const pts = runs.filter((r) => r.o2_mole_fraction != null && r.pressure_atm != null);
  const missing = runs.length - pts.length;
  $("#note-map").textContent = (missing ? `${missing} ensayo(s) del filtro no reportan O2 o presión y no aparecen en el mapa (por ejemplo, SAFFIRE). ` : "") +
    "En las llamas inversas de CFI-G el O2 es el del chorro del quemador, no el del ambiente.";
  if (!pts.length) {
    box.innerHTML = `<p class="empty">Ningún ensayo de este filtro reporta O2 y presión. Los sólidos (SAFFIRE) se quemaron a presión de cabina: mirá su resultado en el gráfico de abajo.</p>`;
    return;
  }

  // Se dibuja al ancho real del contenedor: el texto queda siempre a 11-12 px.
  const W = Math.max(300, box.clientWidth), H = W < 560 ? 300 : 380;
  const m = { t: 12, r: 16, b: 44, l: W < 560 ? 44 : 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xMax = Math.max(0.4, ...pts.map((r) => r.o2_mole_fraction)) * 1.05;
  const yMax = Math.max(1.2, ...pts.map((r) => r.pressure_atm)) * 1.08;
  const x = (v) => m.l + (v / xMax) * iw;
  const y = (v) => m.t + ih - (v / yMax) * ih;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Dispersión de ensayos: fracción de oxígeno contra presión, coloreado por resultado" }, box);
  const step = xMax > 0.6 ? (W < 560 ? 0.2 : 0.1) : 0.05;
  for (let v = 0; v <= xMax + 1e-9; v += step) {
    el("line", { class: "gridline", x1: x(v), x2: x(v), y1: m.t, y2: m.t + ih }, svg);
    el("text", { x: x(v), y: m.t + ih + 16, "text-anchor": "middle" }, svg).textContent = `${Math.round(v * 100)} %`;
  }
  for (let v = 0; v <= yMax + 1e-9; v += 0.5) {
    el("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }, svg);
    el("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v, 1);
  }
  el("line", { class: "baseline", x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }, svg);
  el("text", { class: "axis-title", x: m.l + iw / 2, y: H - 6, "text-anchor": "middle" }, svg).textContent = "Oxígeno (fracción molar)";
  el("text", { class: "axis-title", transform: `translate(14 ${m.t + ih / 2}) rotate(-90)`, "text-anchor": "middle" }, svg).textContent = "Presión (atm)";

  // "Otro" primero (abajo), así los resultados con significado quedan arriba.
  const order = [...pts].sort((a, b) => (classOf(a).key === "other" ? -1 : 0) - (classOf(b).key === "other" ? -1 : 0));
  const dots = [];
  for (const r of order) {
    const c = classOf(r);
    const attrs = c.hollow
      ? { fill: "var(--surface)", stroke: c.color, "stroke-width": 1.5, r: 3.5 }
      : { fill: c.color, stroke: "var(--surface)", "stroke-width": 1, r: 4 };
    const d = el("circle", { class: "dot", cx: x(r.o2_mole_fraction), cy: y(r.pressure_atm), ...attrs }, svg);
    d.dataset.id = r.test_run_id;
    if (state.selected === r.test_run_id) d.classList.add("selected");
    dots.push({ r, cx: x(r.o2_mole_fraction), cy: y(r.pressure_atm), node: d });
  }

  for (const ref of REFERENCES) {
    if (ref.o2 > xMax || ref.p > yMax) continue;
    const cx = x(ref.o2), cy = y(ref.p);
    el("path", { d: `M${cx - 7} ${cy}H${cx + 7}M${cx} ${cy - 7}V${cy + 7}`, stroke: "var(--ref)", "stroke-width": 2, "pointer-events": "none" }, svg);
    const right = cx < m.l + iw * 0.7;
    el("text", { class: "ref-label", x: cx + (right ? 10 : -10), y: cy - 8, "text-anchor": right ? "start" : "end", "pointer-events": "none" }, svg).textContent = ref.label;
  }

  // Hit target: el punto más cercano al cursor (radio 18 px), más grande que la marca.
  const toLocal = (ev) => {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  };
  const nearest = (ev) => {
    const p = toLocal(ev);
    let best = null, bd = 18 * (W / svg.getBoundingClientRect().width);
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
    showTip(`<b>${esc(r.acronym)} · ensayo ${esc(r.test_code ?? r.source_row)}</b>` +
      row("Resultado", r.outcome_label) + row("Material", r.material ?? "—") +
      row("O2", pct(r.o2_mole_fraction)) + row("Presión", `${fmtAuto(r.pressure_atm)} atm`) +
      (r.burn_time_s != null ? row("Tiempo de quemado", `${fmt(r.burn_time_s, 1)} s`) : "") +
      (r.flame_type ? row("Llama", r.flame_type === "inverse" ? "inversa" : "normal") : ""), ev.clientX, ev.clientY);
  });
  svg.addEventListener("mouseleave", hideTip);
  svg.addEventListener("click", (ev) => { const d = nearest(ev); if (d) selectRun(d.r); });
}

// ------------------------------------------------------------------ barras por experimento
function renderBars(runs) {
  const box = $("#chart-bars");
  box.innerHTML = "";
  if (!runs.length) { box.innerHTML = `<p class="empty">Sin ensayos para este filtro.</p>`; return; }

  const groups = new Map();
  for (const r of runs) {
    const key = `${r.acronym} · ${r.source_table}`;
    // "CFI-G_Normal Flames" -> "CFI-G · Normal Flames"; tablas de un solo nombre usan la sigla.
    const label = r.source_table.includes("_") ? r.source_table.replace(/_/g, " · ") : r.acronym;
    if (!groups.has(key)) groups.set(key, { key, label, runs: [] });
    groups.get(key).runs.push(r);
  }
  const rows = [...groups.values()].sort((a, b) => b.runs.length - a.runs.length);
  const max = Math.max(...rows.map((g) => g.runs.length));

  const W = Math.max(300, box.clientWidth), rowH = 30, labelW = W < 560 ? 150 : 210, m = { t: 4, r: 40, b: 24 };
  const H = m.t + rows.length * rowH + m.b;
  const iw = W - labelW - m.r;
  const xs = (v) => (v / max) * iw;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Barras apiladas: ensayos por resultado en cada experimento" }, box);

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
      const seg = g.runs.filter((r) => classOf(r) === c);
      if (!seg.length) continue;
      const w = Math.max(xs(seg.length) - 2, 1.5);   // 2 px de separación entre segmentos
      const rect = el("rect", { x: x0, y: yTop, width: w, height: bh, rx: 3, fill: c.hollow ? "var(--surface)" : c.color,
        stroke: c.hollow ? c.color : "none", "stroke-width": c.hollow ? 1.5 : 0 }, svg);
      const detail = Object.entries(seg.reduce((acc, r) => ((acc[r.outcome_label] = (acc[r.outcome_label] || 0) + 1), acc), {}));
      rect.addEventListener("mousemove", (ev) => showTip(`<b>${esc(g.label)}</b>` + row(c.label, seg.length) +
        detail.map(([k, n]) => row(`· ${k}`, n)).join(""), ev.clientX, ev.clientY));
      rect.addEventListener("mouseleave", hideTip);
      x0 += xs(seg.length);
    }
    el("text", { x: x0 + 6, y: yTop + bh / 2 + 4, class: "ref-label" }, svg).textContent = g.runs.length;
  });
  el("line", { class: "baseline", x1: labelW, x2: labelW, y1: m.t, y2: H - m.b }, svg);
}

// ------------------------------------------------------------------ detalle
async function selectRun(r) {
  state.selected = r.test_run_id;
  document.querySelectorAll(".dot.selected").forEach((n) => n.classList.remove("selected"));
  document.querySelector(`.dot[data-id="${r.test_run_id}"]`)?.classList.add("selected");
  $("#detail-sub").textContent = `${r.acronym} · ensayo ${r.test_code ?? r.source_row}`;
  const ms = await getMeasurements(r.test_run_id);
  const fields = [
    ["Resultado", r.outcome_label], ["Material", r.material], ["Fecha", r.test_date],
    ["Tipo de llama", r.flame_type && (r.flame_type === "inverse" ? "inversa" : "normal")],
    ["Dirección del flujo", r.flow_direction && (r.flow_direction === "opposed" ? "opuesto" : "concurrente")],
    ["Comentario", r.comment],
  ].filter(([, v]) => v != null && v !== "");
  const flags = (r.quality_flags || []).filter((f) => f !== "outcome_not_in_table");
  $("#detail").innerHTML =
    `<dl>${fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` +
    `<h3>Mediciones</h3><dl>${ms.map((m) => `<dt>${esc(m.variable_label)}</dt><dd>${
      m.value_num != null ? `${m.is_approx ? "≈ " : ""}${esc(fmtAuto(m.value_num))} ${esc(m.unit === "1" ? "" : m.unit)}` : esc(m.value_text)
    }</dd>`).join("")}</dl>` +
    (flags.length ? `<p class="flags">Correcciones aplicadas: ${esc(flags.join(", "))}</p>` : "") +
    `<p class="flags">Fuente: <a href="${esc(r.doi_url)}" target="_blank" rel="noopener">${esc(r.doi_url)}</a> · fila ${r.source_row} de “${esc(r.source_table)}”</p>`;
}

// ------------------------------------------------------------------ tabla de investigaciones
function renderInvestigations(invs) {
  const rows = [...invs].sort((a, b) => b.n_test_runs - a.n_test_runs || b.rag_value_rank - a.rag_value_rank);
  const period = (i) => (i.start_date || i.end_date ? `${i.start_date?.slice(0, 4) ?? "?"}–${i.end_date?.slice(0, 4) ?? "?"}` : "—");
  $("#inv-table").innerHTML = `
    <thead><tr><th>PSI</th><th>Sigla</th><th>Título</th><th>Familia</th><th>Período</th>
      <th class="num">Ensayos</th><th class="num">Docs</th><th>Valor ML</th><th>Valor RAG</th><th>DOI</th></tr></thead>
    <tbody>${rows.map((i) => `<tr>
      <td>${esc(i.accession)}</td><td>${esc(i.acronym ?? "—")}</td><td class="title">${esc(i.title)}</td>
      <td><span class="pill">${esc(GROUP_NAMES[i.family_group] ?? i.family_group)}</span></td>
      <td>${period(i)}</td>
      <td class="num">${i.n_test_runs || "—"}</td><td class="num">${i.n_rag_documents || "—"}</td>
      <td>${esc(i.ml_value)}</td><td>${esc(i.rag_value)}</td>
      <td><a href="${esc(i.doi_url)}" target="_blank" rel="noopener">${esc(i.doi)}</a></td></tr>`).join("")}</tbody>`;
}

// ------------------------------------------------------------------ filtros
function fillSelect(sel, items) {
  const keep = sel.value;
  sel.innerHTML = sel.options[0].outerHTML + items.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  sel.value = items.some(([v]) => v === keep) ? keep : "";
}

function refreshFilters() {
  const byGroup = state.runs.filter((r) => !state.group || r.family_group === state.group);
  const invs = [...new Map(byGroup.map((r) => [r.accession, `${r.acronym} (${r.accession})`])).entries()];
  fillSelect($("#f-inv"), invs.sort((a, b) => a[1].localeCompare(b[1])));
  state.inv = $("#f-inv").value;
  const byInv = byGroup.filter((r) => !state.inv || r.accession === state.inv);
  const mats = [...new Map(byInv.filter((r) => r.material_code).map((r) => [r.material_code, r.material])).entries()];
  fillSelect($("#f-mat"), mats.sort((a, b) => a[1].localeCompare(b[1])));
  state.mat = $("#f-mat").value;
}

function render() {
  const runs = filtered();
  $("#f-count").textContent = `${runs.length} de ${state.runs.length} ensayos`;
  renderMap(runs);
  renderBars(runs);
}

for (const [id, key] of [["#f-group", "group"], ["#f-inv", "inv"], ["#f-mat", "mat"]]) {
  $(id).addEventListener("change", (e) => { state[key] = e.target.value; refreshFilters(); render(); });
}

// ------------------------------------------------------------------ arranque
try {
  const [stats, invs, runs] = await Promise.all([getStats(), getInvestigations(), getTestRuns()]);
  Object.assign(state, { runs, invs });
  renderTiles(stats);
  $("#legend-map").innerHTML = CLASSES.map(legendItem).join("") +
    `<li><svg width="12" height="12"><path d="M1 6H11M6 1V11" stroke="var(--ref)" stroke-width="2"/></svg>Atmósfera de referencia</li>`;
  $("#legend-bars").innerHTML = CLASSES.map(legendItem).join("");
  renderInvestigations(invs);
  refreshFilters();
  render();
  let raf;
  new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(render); }).observe($("#chart-bars"));
} catch (err) {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">No se pudieron cargar los datos: ${esc(err.message)}</p>`);
  console.error(err);
}
