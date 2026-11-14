// Early-warning panel (M8, contract v3): sliders for the atmosphere -> POST /risk -> one traffic light per scenario,
// plus a "what if" curve from POST /sweep. Everything shown comes from the API (LC's flame/scenarios.py).
// Gray is its own state: NASA never tested it, so it is unknown, never "safe".
import { getRisk, getSweep, isOnline } from "./api.js";

const SVG = "http://www.w3.org/2000/svg";
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => `${Math.round(v * 100)} %`;

// status -> label and icon (never colour alone)
export const LEVELS = {
  red:    { label: "Tends to keep burning",           icon: "▲" },
  yellow: { label: "Mixed: could go either way",      icon: "◆" },
  green:  { label: "Tends to go out by itself",       icon: "●" },
  gray:   { label: "NASA never tested this: unknown", icon: "?" },
};

const SLIDERS = [
  // id, field, min, max, step, default, unit -> value shown and sent
  { id: "o2",  field: "o2_frac",      min: 10, max: 40,  step: 1, value: 21,    unit: "%",   label: "Oxygen" },
  { id: "p",   field: "pressure_kpa", min: 50, max: 310, step: 1, value: 101.3, unit: "kPa", label: "Pressure" },
  { id: "co2", field: "co2_frac",     min: 0,  max: 50,  step: 1, value: 0,     unit: "%",   label: "CO₂ added" },
  { id: "he",  field: "he_frac",      min: 0,  max: 60,  step: 1, value: 0,     unit: "%",   label: "Helium added" },
];
const PRESETS = [
  { label: "ISS 21 % / 101 kPa", o2: 21, p: 101.3, co2: 0, he: 0 },
  { label: "Exploration 34 % / 70 kPa", o2: 34, p: 70.3, co2: 0, he: 0 },
];

const SCENARIOS = [
  { key: "methanol", label: "Methanol droplet", fuel: "methanol" },
  { key: "heptane", label: "n-Heptane droplet", fuel: "heptane" },
  { key: "ethane", label: "Ethane gas flame", fuel: "ethane", gas: true },
  { key: "propane", label: "Propane gas flame", fuel: "propane", gas: true },
  { key: "butane", label: "Butane gas flame", fuel: "butane", gas: true },
];
const VARIABLES = [
  { key: "o2_frac", label: "Oxygen", unit: "%", scale: 100 },
  { key: "pressure_kpa", label: "Pressure", unit: "kPa", scale: 1 },
  { key: "co2_frac", label: "CO₂ added", unit: "%", scale: 100 },
  { key: "he_frac", label: "Helium added", unit: "%", scale: 100 },
];
const HORIZON_S = 30;   // gas flames: "goes out by itself within 30 s" (the /risk default)

const val = (id) => Number($(`#ew-${id}`).value);
function atmosphere() {
  return { o2_frac: val("o2") / 100, pressure_kpa: val("p"), co2_frac: val("co2") / 100, he_frac: val("he") / 100 };
}

// ------------------------------------------------------------------ traffic light
function badge(level) {
  const l = LEVELS[level] ?? LEVELS.gray;
  return `<span class="ew-chip ew-${esc(level)}"><span aria-hidden="true">${l.icon}</span> ${esc(l.label)}</span>`;
}

function itemRow(i) {
  const range = i.persistence != null
    ? `<div class="ew-bar" role="img" aria-label="Chance it keeps burning ${pct(i.persistence)}, 95 % interval ${pct(i.ci_low)} to ${pct(i.ci_high)}">
         <span class="ew-ci" style="left:${(i.ci_low * 100).toFixed(1)}%;width:${Math.max(0.8, (i.ci_high - i.ci_low) * 100).toFixed(1)}%"></span>
         <span class="ew-dot" style="left:${(i.persistence * 100).toFixed(1)}%"></span>
       </div>
       <span class="ew-num">${pct(i.persistence)} <span class="muted">(${pct(i.ci_low)}–${pct(i.ci_high)})</span></span>`
    : `<span class="ew-num muted">${i.level === "gray" ? "no estimate" : "evidence only, no model"}</span>`;
  return `<li class="ew-item">
    <div class="ew-item-head"><b>${esc(i.scenario)}</b>${badge(i.level)}</div>
    <div class="ew-item-val">${range}</div>
    <p class="ew-reason">${esc(i.reasons?.[0] ?? "")}</p>
  </li>`;
}

function renderRisk(r) {
  const l = LEVELS[r.level] ?? LEVELS.gray;
  $("#ew-result").innerHTML = `
    <div class="ew-overall ew-${esc(r.level)}" role="status">
      <span class="ew-light" aria-hidden="true">${l.icon}</span>
      <div><p class="ew-level">${esc(l.label)}</p><p class="ew-headline">${esc(r.headline)}</p></div>
    </div>
    <p class="ew-disclaimer"><span aria-hidden="true">ⓘ</span> ${esc(r.disclaimer)}</p>
    <p class="ew-scale muted">Bars: chance the flame keeps burning (dot) and its 95 % interval.</p>
    <ul class="ew-items">${r.items.map(itemRow).join("")}</ul>
    <p class="muted ew-model">${esc(r.model_version)}</p>`;
}

// ------------------------------------------------------------------ "what if" curve (POST /sweep)
function el(name, attrs = {}, parent) {
  const n = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function renderSweep(s, variable, current) {
  const box = $("#ew-sweep-chart");
  const v = VARIABLES.find((x) => x.key === variable);
  const pts = s.points.map((p) => ({ x: p.x * v.scale, in: p.probability != null,
    y: p.probability == null ? null : 1 - p.probability,
    lo: p.ci_high == null ? null : 1 - p.ci_high, hi: p.ci_low == null ? null : 1 - p.ci_low }));
  box.innerHTML = "";
  if (!pts.length) { box.innerHTML = `<p class="muted">No model for this scenario.</p>`; return; }
  const W = Math.max(320, box.clientWidth || 640), H = 240, m = { l: 44, r: 12, t: 12, b: 36 };
  const xs = pts.map((p) => p.x), x0 = Math.min(...xs), x1 = Math.max(...xs);
  const X = (x) => m.l + ((x - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const Y = (y) => m.t + (1 - y) * (H - m.t - m.b);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img",
    "aria-label": `Chance the flame keeps burning as ${v.label.toLowerCase()} changes` }, box);
  const pat = el("pattern", { id: "ew-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    el("defs", {}, svg));
  el("rect", { width: 6, height: 6, fill: "var(--page)" }, pat);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--grid)", "stroke-width": 3 }, pat);

  for (const y of [0, 0.25, 0.5, 0.75, 1]) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(y), y2: Y(y), class: y === 0 ? "baseline" : "gridline" }, svg);
    el("text", { x: m.l - 6, y: Y(y) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = `${y * 100} %`;
  }
  const step = (xs[1] ?? xs[0]) - xs[0] || 1;
  // untested stretches: hatched band + "no data"
  let run = null;
  const runs = [];
  pts.forEach((p, k) => {
    if (!p.in) { run ??= { a: p.x, b: p.x }; run.b = p.x; }
    if ((p.in || k === pts.length - 1) && run) { runs.push(run); run = null; }
  });
  for (const r of runs) {
    const a = Math.max(m.l, X(r.a - step / 2)), b = Math.min(W - m.r, X(r.b + step / 2));
    el("rect", { x: a, y: m.t, width: Math.max(1, b - a), height: H - m.t - m.b, class: "ew-nodata" }, svg);
    if (b - a > 44) el("text", { x: (a + b) / 2, y: m.t + 16, "text-anchor": "middle", class: "tick" }, svg).textContent = "no data";
  }
  // tested stretches: CI band + line, broken at the gaps
  const segs = [];
  let cur = [];
  for (const p of pts) { if (p.in) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } }
  if (cur.length) segs.push(cur);
  for (const seg of segs) {
    const band = seg.map((p) => `${X(p.x)},${Y(p.hi)}`).concat([...seg].reverse().map((p) => `${X(p.x)},${Y(p.lo)}`));
    el("polygon", { points: band.join(" "), class: "ew-band" }, svg);
    if (seg.length > 1) el("polyline", { points: seg.map((p) => `${X(p.x)},${Y(p.y)}`).join(" "), class: "ew-line" }, svg);
    else el("circle", { cx: X(seg[0].x), cy: Y(seg[0].y), r: 4, class: "ew-pt" }, svg);
  }
  // x ticks (about 6)
  const every = Math.max(1, Math.round(pts.length / 6));
  pts.forEach((p, k) => {
    if (k % every) return;
    el("text", { x: X(p.x), y: H - m.b + 16, "text-anchor": "middle", class: "tick" }, svg).textContent = `${+p.x.toFixed(1)}`;
  });
  el("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle", class: "tick" }, svg).textContent = `${v.label} (${v.unit})`;
  // current atmosphere
  if (current >= x0 && current <= x1) {
    el("line", { x1: X(current), x2: X(current), y1: m.t, y2: H - m.b, class: "ew-now" }, svg);
    const right = X(current) > W - m.r - 110;          // near the right edge: put the label on the left of the line
    el("text", { x: X(current) + (right ? -4 : 4), y: H - m.b - 6, "text-anchor": right ? "end" : "start", class: "tick" }, svg)
      .textContent = "your atmosphere";
  }
  // hover: one hit column per point
  const tip = $("#tooltip");
  const colW = (W - m.l - m.r) / pts.length;
  const hover = el("circle", { r: 5, class: "ew-pt", visibility: "hidden" }, svg);
  pts.forEach((p) => {
    const hit = el("rect", { x: X(p.x) - colW / 2, y: m.t, width: colW, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("pointermove", (e) => {
      if (p.in) { hover.setAttribute("cx", X(p.x)); hover.setAttribute("cy", Y(p.y)); hover.setAttribute("visibility", "visible"); }
      else hover.setAttribute("visibility", "hidden");
      tip.innerHTML = `<b>${esc(v.label)} ${+p.x.toFixed(1)} ${esc(v.unit)}</b><br>` + (p.in
        ? `Keeps burning: ${pct(p.y)} (95 % interval ${pct(p.lo)}–${pct(p.hi)})`
        : "NASA never tested this combination: no estimate");
      tip.hidden = false;
      tip.style.left = `${Math.min(e.clientX + 14, innerWidth - 240)}px`;
      tip.style.top = `${e.clientY + 14}px`;
    });
    hit.addEventListener("pointerleave", () => { tip.hidden = true; hover.setAttribute("visibility", "hidden"); });
  });
  const tested = s.tested_range ? `NASA tested ${v.label.toLowerCase()} ${+(s.tested_range[0] * v.scale).toFixed(1)}–${+(s.tested_range[1] * v.scale).toFixed(1)} ${v.unit} for this case.` : "";
  $("#ew-sweep-note").textContent = [tested, ...(s.notes ?? []).slice(0, 2)].filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ wiring
export function setupWarning() {
  const panel = $("#ew");
  if (!isOnline()) {
    $("#ew-result").innerHTML = `<p class="muted">The early-warning indicator needs the backend API. This page is running offline on static data.</p>`;
    panel.querySelectorAll("input, button, select").forEach((n) => { n.disabled = true; });
    return;
  }

  $("#ew-controls").innerHTML = SLIDERS.map((s) => `
    <label class="ew-slider" for="ew-${s.id}">
      <span class="ew-slider-head"><span>${s.label}</span><output id="ew-${s.id}-out"></output></span>
      <input type="range" id="ew-${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}">
    </label>`).join("");
  $("#ew-presets").innerHTML = PRESETS.map((p, k) => `<button type="button" class="chip" data-k="${k}">${esc(p.label)}</button>`).join("");
  $("#ew-scn").innerHTML = SCENARIOS.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join("");
  $("#ew-var").innerHTML = VARIABLES.map((v) => `<option value="${v.key}">${esc(v.label)}</option>`).join("");

  const showValues = () => SLIDERS.forEach((s) => { $(`#ew-${s.id}-out`).textContent = `${+val(s.id).toFixed(1)} ${s.unit}`; });

  let riskCtl, sweepCtl, timer;
  async function refreshRisk() {
    riskCtl?.abort(); riskCtl = new AbortController();
    try { renderRisk(await getRisk(atmosphere(), riskCtl.signal)); }
    catch (e) { if (e.name !== "AbortError") $("#ew-result").innerHTML = `<p class="muted">Could not get the indicator: ${esc(e.message)}</p>`; }
  }
  async function refreshSweep() {
    const scn = SCENARIOS.find((s) => s.key === $("#ew-scn").value), variable = $("#ew-var").value;
    const a = atmosphere();
    const conditions = { fuel: scn.fuel, o2_frac: a.o2_frac, pressure_kpa: a.pressure_kpa,
      ...(a.co2_frac ? { co2_frac: a.co2_frac } : {}), ...(a.he_frac ? { he_frac: a.he_frac } : {}),
      ...(scn.gas ? { horizon_s: HORIZON_S } : {}) };
    const v = VARIABLES.find((x) => x.key === variable);
    sweepCtl?.abort(); sweepCtl = new AbortController();
    try { renderSweep(await getSweep({ conditions, variable }, sweepCtl.signal), variable, a[variable] * v.scale); }
    catch (e) { if (e.name !== "AbortError") $("#ew-sweep-chart").innerHTML = `<p class="muted">Could not get the scenario: ${esc(e.message)}</p>`; }
  }
  const refresh = () => { showValues(); clearTimeout(timer); timer = setTimeout(() => { refreshRisk(); refreshSweep(); }, 150); };

  $("#ew-controls").addEventListener("input", refresh);
  $("#ew-presets").addEventListener("click", (e) => {
    const p = PRESETS[e.target.closest(".chip")?.dataset.k];
    if (!p) return;
    SLIDERS.forEach((s) => { $(`#ew-${s.id}`).value = p[s.id]; });
    refresh();
  });
  $("#ew-scn").addEventListener("change", refreshSweep);
  $("#ew-var").addEventListener("change", refreshSweep);
  addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(refreshSweep, 200); });
  refresh();
}
