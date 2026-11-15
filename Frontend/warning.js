// Experiment planner + early warning (contracts v3-v5).
// A researcher picks one fire and the PLANNED atmosphere. The sliders stand for the sensor readings of the experiment:
// before changing a parameter (or when a reading drifts), the panel shows what the models expect with the change
// compared with the plan (POST /compare), how long the flame lasts (POST /duration), the "what if" curve of one
// variable (POST /sweep) and, folded, every kind of fire at the current atmosphere (POST /risk).
// Everything shown comes from the API (LC's flame/scenarios.py). Gray = NASA never tested it: unknown, never "safe".
import { getCompare, getDuration, getRisk, getSweep, isOnline } from "./api.js";

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
const VERDICTS = {
  worse: { label: "Riskier than planned", icon: "▲", cls: "ew-red" },
  better: { label: "Safer than planned", icon: "▼", cls: "ew-green" },
  "about the same": { label: "About the same as planned", icon: "=", cls: "ew-plain" },
  unknown: { label: "Unknown: outside the NASA data", icon: "?", cls: "ew-gray" },
};

const SLIDERS = [
  // id, field, min, max, step, default, unit -> value shown and sent
  { id: "o2",  field: "o2_frac",      min: 10, max: 40,  step: 1, value: 21,    unit: "%",   label: "Oxygen", scale: 100 },
  { id: "p",   field: "pressure_kpa", min: 50, max: 310, step: 1, value: 101.3, unit: "kPa", label: "Pressure", scale: 1 },
  { id: "co2", field: "co2_frac",     min: 0,  max: 50,  step: 1, value: 0,     unit: "%",   label: "CO₂ added", scale: 100 },
  { id: "he",  field: "he_frac",      min: 0,  max: 60,  step: 1, value: 0,     unit: "%",   label: "Helium added", scale: 100 },
];
const PRESETS = [
  { label: "ISS 21 % / 101 kPa", o2: 21, p: 101.3, co2: 0, he: 0 },
  { label: "Exploration 34 % / 70 kPa", o2: 34, p: 70.3, co2: 0, he: 0 },
  { label: "CO₂ suppression 18 % / 30 % CO₂", o2: 18, p: 101.3, co2: 30, he: 0 },
];
const FIRES = [
  { key: "butane", label: "Butane gas flame", fuel: "butane", gas: true },
  { key: "propane", label: "Propane gas flame", fuel: "propane", gas: true },
  { key: "ethane", label: "Ethane gas flame", fuel: "ethane", gas: true },
  { key: "methanol", label: "Methanol droplet", fuel: "methanol" },
  { key: "heptane", label: "n-Heptane droplet", fuel: "heptane" },
];
const VARIABLES = [
  { key: "o2_frac", label: "Oxygen", unit: "%", scale: 100 },
  { key: "pressure_kpa", label: "Pressure", unit: "kPa", scale: 1 },
  { key: "co2_frac", label: "CO₂ added", unit: "%", scale: 100 },
  { key: "he_frac", label: "Helium added", unit: "%", scale: 100 },
];
const HORIZON_S = 30;   // gas flames: "still burning after 30 s" (the /risk default)

const val = (id) => Number($(`#ew-${id}`).value);
const fire = () => FIRES.find((f) => f.key === $("#ew-fire").value) ?? FIRES[0];
function readings() {
  return { o2: val("o2"), p: val("p"), co2: val("co2"), he: val("he") };
}
function atmosphere(r = readings()) {
  return { o2_frac: r.o2 / 100, pressure_kpa: r.p, co2_frac: r.co2 / 100, he_frac: r.he / 100 };
}
function conditions(r, f = fire()) {
  const a = atmosphere(r);
  return { fuel: f.fuel, o2_frac: a.o2_frac, pressure_kpa: a.pressure_kpa,
    ...(a.co2_frac ? { co2_frac: a.co2_frac } : {}), ...(a.he_frac ? { he_frac: a.he_frac } : {}),
    ...(f.gas ? { horizon_s: HORIZON_S } : {}) };
}
const describe = (r) => `O₂ ${r.o2} %, ${+r.p.toFixed(1)} kPa, CO₂ ${r.co2} %, He ${r.he} %`;
const sameReadings = (a, b) => SLIDERS.every((s) => Math.abs(a[s.id] - b[s.id]) < 1e-9);

function el(name, attrs = {}, parent) {
  const n = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
function badge(level) {
  const l = LEVELS[level] ?? LEVELS.gray;
  return `<span class="ew-chip ew-${esc(level)}"><span aria-hidden="true">${l.icon}</span> ${esc(l.label)}</span>`;
}
function bar(v, lo, hi, what) {
  if (v == null) return `<span class="ew-num muted">no estimate</span>`;
  return `<div class="ew-bar" role="img" aria-label="${esc(what)} ${pct(v)}, 95 % interval ${pct(lo)} to ${pct(hi)}">
      <span class="ew-ci" style="left:${(lo * 100).toFixed(1)}%;width:${Math.max(0.8, (hi - lo) * 100).toFixed(1)}%"></span>
      <span class="ew-dot" style="left:${(v * 100).toFixed(1)}%"></span>
    </div>
    <span class="ew-num">${pct(v)} <span class="muted">(${pct(lo)}–${pct(hi)})</span></span>`;
}

// ------------------------------------------------------------------ plan vs change (POST /compare)
function sideCard(title, s, gas) {
  const med = gas && s.in_domain ? `<p class="ew-med">Goes out by itself: <b>${s.median_s != null ? `~${s.median_s} s` : "over 300 s"}</b> <span class="muted">(median)</span></p>` : "";
  return `<div class="ew-side-card">
    <p class="ew-side-title">${esc(title)}</p>
    ${badge(s.level)}
    <div class="ew-item-val">${bar(s.persistence, s.ci_low, s.ci_high, "Chance it keeps burning")}</div>
    ${med}
    ${s.in_domain ? "" : `<p class="ew-reason">${esc(s.reasons?.[0] ?? "")}</p>`}
  </div>`;
}

function renderCompare(c, plan, now, f) {
  const changed = !sameReadings(plan, now);
  const v = VERDICTS[c.verdict] ?? VERDICTS.unknown;
  const head = changed
    ? `<div class="ew-overall ${v.cls}" role="status">
         <span class="ew-light" aria-hidden="true">${v.icon}</span>
         <div><p class="ew-level">${esc(v.label)}</p><p class="ew-headline">${esc(c.headline)}</p></div>
       </div>`
    : `<div class="ew-overall ew-${esc(c.plan.level)}" role="status">
         <span class="ew-light" aria-hidden="true">${(LEVELS[c.plan.level] ?? LEVELS.gray).icon}</span>
         <div><p class="ew-level">Planned experiment: ${esc((LEVELS[c.plan.level] ?? LEVELS.gray).label.toLowerCase())}</p>
         <p class="ew-headline">Move a reading to see what the models expect with that change, before you make it.</p></div>
       </div>`;
  const rows = c.changes?.length
    ? `<ul class="ew-changes">${c.changes.map((r) => `<li class="ew-ch-${esc(r.direction.replaceAll(" ", "-"))}">${esc(r.text)}</li>`).join("")}</ul>` : "";
  const notes = (c.notes ?? []).map((n) => `<p class="note"><span aria-hidden="true">ⓘ</span> ${esc(n)}</p>`).join("");
  $("#ew-result").innerHTML = `${head}
    <p class="ew-meaning muted">Bars: ${esc(c.meaning)} (dot) and its 95 % interval.</p>
    <div class="ew-sides">${sideCard(`Plan · ${describe(plan)}`, c.plan, f.gas)}${changed ? sideCard(`With the change · ${describe(now)}`, c.change, f.gas) : ""}</div>
    ${rows}${notes}
    ${changed ? `<button type="button" class="chip ew-discuss" id="ew-discuss">Discuss this change with the assistant ↓</button>` : ""}
    <p class="ew-disclaimer"><span aria-hidden="true">ⓘ</span> ${esc(c.disclaimer)} Sensor readings here are simulated: you move them.</p>`;
  $("#ew-discuss")?.addEventListener("click", () => dispatchEvent(new CustomEvent("flame:ask", { detail: discussQuestion(plan, now, f) })));
}

// The question sent to the chat: "change <variable> from <plan> to <now>" is what the assistant reads as a planned value
// and a change (it then calls the same /compare tool and explains the result with the NASA reports).
const ASK_WORDS = { o2: ["oxygen", "%"], p: ["pressure", " kPa"], co2: ["CO2", "%"], he: ["helium", "%"] };
function discussQuestion(plan, now, f) {
  const moved = SLIDERS.filter((s) => Math.abs(now[s.id] - plan[s.id]) > 1e-9).map((s) => s.id);
  const [first, ...rest] = moved;
  const [word, unit] = ASK_WORDS[first];
  const ctx = SLIDERS.filter((s) => s.id !== first && (s.id === "o2" || s.id === "p" || now[s.id] > 0))
    .map((s) => `${+now[s.id].toFixed(1)}${ASK_WORDS[s.id][1]} ${ASK_WORDS[s.id][0]}`).join(", ");
  const also = rest.length ? ` (I also changed ${rest.map((id) => ASK_WORDS[id][0]).join(" and ")})` : "";
  return `What happens if I change ${word} from ${+plan[first].toFixed(1)}${unit} to ${+now[first].toFixed(1)}${unit} in a ${f.label.toLowerCase()} at ${ctx}${also}? Why?`;
}

// ------------------------------------------------------------------ duration (POST /duration)
function renderDuration(dp, dn, changed, f) {
  const box = $("#ew-dur-chart"), note = $("#ew-dur-note");
  box.innerHTML = ""; note.textContent = "";
  const d = changed ? dn : dp;
  if (!f.gas) {
    const rows = (d.observed ?? []).map((o) => `<tr><td><code>${esc(o.test_id)}</code></td><td>${o.o2_pct} %</td><td>${o.co2_pct} %</td><td>${o.he_pct} %</td>
      <td><b>${o.burn_time_s.toFixed(1)} s</b></td><td>${esc(o.ended)}</td></tr>`).join("");
    box.innerHTML = rows
      ? `<div class="table-wrap"><table class="ew-obs"><thead><tr><th>Closest NASA test</th><th>O₂</th><th>CO₂</th><th>He</th><th>Burned for</th><th>How it ended</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="muted">No NASA droplet test close to these conditions.</p>`;
    note.textContent = (d.notes ?? []).join(" ");
    return;
  }
  const curves = [{ d: dp, cls: "ew-line-plan", name: "plan" }, ...(changed ? [{ d: dn, cls: "ew-line", name: "with the change" }] : [])]
    .filter((c) => c.d.curve?.length);
  if (!curves.length) {
    box.innerHTML = `<p class="muted">No time curve: ${esc((d.reasons ?? []).join("; ") || "NASA did not test this combination")}.</p>`;
    note.textContent = (d.observed ?? []).filter((o) => o.burn_time_s != null)
      .map((o) => `${o.test_id}: ${o.burn_time_s} s (${o.ended})`).join(" · ");
    return;
  }
  const W = Math.max(320, box.clientWidth || 640), H = 220, m = { l: 44, r: 12, t: 12, b: 36 };
  const T = Math.max(...curves.flatMap((c) => c.d.curve.map((p) => p.t_s)));
  const X = (t) => m.l + (t / T) * (W - m.l - m.r), Y = (y) => m.t + (1 - y) * (H - m.t - m.b);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img",
    "aria-label": "Share of such flames still burning against time" }, box);
  for (const y of [0, 0.5, 1]) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(y), y2: Y(y), class: y === 0 ? "baseline" : "gridline" }, svg);
    el("text", { x: m.l - 6, y: Y(y) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = `${y * 100} %`;
  }
  for (let t = 0; t <= T; t += 60) el("text", { x: X(t), y: H - m.b + 16, "text-anchor": "middle", class: "tick" }, svg).textContent = `${t}`;
  el("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle", class: "tick" }, svg).textContent = "Seconds after ignition";
  for (const c of curves) {
    const pts = c.d.curve;
    if (c.cls === "ew-line") {
      const band = pts.map((p) => `${X(p.t_s)},${Y(p.ci_high)}`).concat([...pts].reverse().map((p) => `${X(p.t_s)},${Y(p.ci_low)}`));
      el("polygon", { points: band.join(" "), class: "ew-band" }, svg);
    }
    el("polyline", { points: pts.map((p) => `${X(p.t_s)},${Y(p.still_burning)}`).join(" "), class: c.cls }, svg);
    if (c.d.median_s != null) {
      el("line", { x1: X(c.d.median_s), x2: X(c.d.median_s), y1: Y(0.5) - 6, y2: Y(0.5) + 6, class: c.cls }, svg);
      el("text", { x: X(c.d.median_s) + 4, y: Y(0.5) - 8, class: "tick" }, svg).textContent = `${c.name}: ~${c.d.median_s} s`;
    }
  }
  // real NASA flames nearby: their burn times as ticks on the time axis
  for (const o of (d.observed ?? []).filter((o) => o.burn_time_s != null && o.burn_time_s <= T)) {
    el("line", { x1: X(o.burn_time_s), x2: X(o.burn_time_s), y1: H - m.b - 8, y2: H - m.b, class: "ew-obs-tick" }, svg)
      .appendChild(el("title")).textContent = `${o.test_id}: ${o.burn_time_s} s (${o.ended})`;
  }
  note.textContent = `Line: share of such flames still burning (model)${changed ? "; dashed = plan" : ""}. Ticks on the axis: burn times of the closest real NASA flames. ` + (d.notes ?? []).join(" ");
}

// ------------------------------------------------------------------ all fires at the current atmosphere (POST /risk)
function renderRisk(r) {
  const row = (i) => `<li class="ew-item">
      <div class="ew-item-head"><b>${esc(i.scenario)}</b>${badge(i.level)}</div>
      <div class="ew-item-val">${i.persistence != null ? bar(i.persistence, i.ci_low, i.ci_high, "Chance it keeps burning")
        : `<span class="ew-num muted">${i.level === "gray" ? "no estimate" : "evidence only, no model"}</span>`}</div>
      <p class="ew-reason">${esc(i.reasons?.[0] ?? "")}</p></li>`;
  $("#ew-all").innerHTML = `<p class="ew-headline">${esc(r.headline)}</p><ul class="ew-items">${r.items.map(row).join("")}</ul>
    <p class="muted ew-model">${esc(r.model_version)}</p>`;
  $("#ew-all-level").innerHTML = badge(r.level);
}

// ------------------------------------------------------------------ "what if" curve (POST /sweep)
function renderSweep(s, variable, planX, nowX) {
  const box = $("#ew-sweep-chart");
  const v = VARIABLES.find((x) => x.key === variable);
  const pts = s.points.map((p) => ({ x: p.x * v.scale, in: p.probability != null,
    y: p.probability == null ? null : 1 - p.probability,
    lo: p.ci_high == null ? null : 1 - p.ci_high, hi: p.ci_low == null ? null : 1 - p.ci_low }));
  box.innerHTML = "";
  if (!pts.length) { box.innerHTML = `<p class="muted">No model for this fire.</p>`; return; }
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
  const every = Math.max(1, Math.round(pts.length / 6));
  pts.forEach((p, k) => {
    if (k % every) return;
    el("text", { x: X(p.x), y: H - m.b + 16, "text-anchor": "middle", class: "tick" }, svg).textContent = `${+p.x.toFixed(1)}`;
  });
  el("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle", class: "tick" }, svg).textContent = `${v.label} (${v.unit})`;
  const mark = (xv, label, cls, row) => {
    if (xv < x0 || xv > x1) return;
    el("line", { x1: X(xv), x2: X(xv), y1: m.t, y2: H - m.b, class: cls }, svg);
    const right = X(xv) > W - m.r - 110;
    el("text", { x: X(xv) + (right ? -4 : 4), y: H - m.b - 6 - row * 14, "text-anchor": right ? "end" : "start", class: "tick" }, svg).textContent = label;
  };
  mark(planX, "plan", "ew-plan-mark", 0);
  if (Math.abs(nowX - planX) > 1e-9) mark(nowX, "with the change", "ew-now", 1);
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
  const tested = s.tested_range ? `NASA tested ${v.label.toLowerCase()} ${+(s.tested_range[0] * v.scale).toFixed(1)}–${+(s.tested_range[1] * v.scale).toFixed(1)} ${v.unit} for this fire.` : "";
  $("#ew-sweep-note").textContent = [tested, ...(s.notes ?? []).slice(0, 2)].filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ wiring
export function setupWarning() {
  const panel = $("#ew");
  if (!isOnline()) {
    $("#ew-result").innerHTML = `<p class="muted">The experiment planner needs the backend API. This page is running offline on static data.</p>`;
    panel.querySelectorAll("input, button, select").forEach((n) => { n.disabled = true; });
    return;
  }

  $("#ew-fire").innerHTML = FIRES.map((f) => `<option value="${f.key}">${esc(f.label)}</option>`).join("");
  $("#ew-controls").innerHTML = SLIDERS.map((s) => `
    <label class="ew-slider" for="ew-${s.id}">
      <span class="ew-slider-head"><span>${s.label} <span class="muted">· sensor</span></span><output id="ew-${s.id}-out"></output></span>
      <input type="range" id="ew-${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}">
      <span class="ew-plan-tick" id="ew-${s.id}-plan"></span>
    </label>`).join("");
  $("#ew-presets").innerHTML = PRESETS.map((p, k) => `<button type="button" class="chip" data-k="${k}">${esc(p.label)}</button>`).join("");
  $("#ew-var").innerHTML = VARIABLES.map((v) => `<option value="${v.key}">${esc(v.label)}</option>`).join("");

  let plan = readings();
  const showValues = () => {
    const now = readings();
    SLIDERS.forEach((s) => {
      $(`#ew-${s.id}-out`).textContent = `${+now[s.id].toFixed(1)} ${s.unit}`;
      const moved = Math.abs(now[s.id] - plan[s.id]) > 1e-9;
      $(`#ew-${s.id}-plan`).textContent = moved ? `plan: ${+plan[s.id].toFixed(1)} ${s.unit}` : "";
      $(`#ew-${s.id}`).closest(".ew-slider").classList.toggle("ew-moved", moved);
    });
    $("#ew-plan-desc").textContent = `Plan: ${fire().label}, ${describe(plan)}`;
    $("#ew-reset").disabled = sameReadings(plan, now);
    $("#ew-setplan").disabled = sameReadings(plan, now);
  };

  let ctl, timer;
  async function refresh() {
    ctl?.abort(); ctl = new AbortController();
    const signal = ctl.signal, f = fire(), now = readings(), changed = !sameReadings(plan, now);
    const pc = conditions(plan, f), nc = conditions(now, f);
    const variable = $("#ew-var").value, v = VARIABLES.find((x) => x.key === variable);
    const fail = (sel, what) => (e) => { if (e.name !== "AbortError") $(sel).innerHTML = `<p class="muted">Could not get ${what}: ${esc(e.message)}</p>`; };
    getCompare({ plan: pc, change: nc }, signal).then((c) => renderCompare(c, plan, now, f)).catch(fail("#ew-result", "the comparison"));
    Promise.all([getDuration(pc, signal), changed ? getDuration(nc, signal) : null])
      .then(([dp, dn]) => renderDuration(dp, dn, changed, f)).catch(fail("#ew-dur-chart", "the duration"));
    getSweep({ conditions: nc, variable }, signal)
      .then((s) => renderSweep(s, variable, atmosphere(plan)[variable] * v.scale, atmosphere(now)[variable] * v.scale))
      .catch(fail("#ew-sweep-chart", "the scenario"));
    getRisk(atmosphere(now), signal).then(renderRisk).catch(fail("#ew-all", "the indicator"));
  }
  const update = () => { showValues(); clearTimeout(timer); timer = setTimeout(refresh, 150); };
  const setReadings = (r) => SLIDERS.forEach((s) => { $(`#ew-${s.id}`).value = r[s.id]; });

  $("#ew-controls").addEventListener("input", update);
  $("#ew-fire").addEventListener("change", update);
  $("#ew-var").addEventListener("change", update);
  $("#ew-presets").addEventListener("click", (e) => {     // a preset is a new PLAN
    const p = PRESETS[e.target.closest(".chip")?.dataset.k];
    if (!p) return;
    setReadings(p); plan = readings(); update();
  });
  $("#ew-setplan").addEventListener("click", () => { plan = readings(); update(); });
  $("#ew-reset").addEventListener("click", () => { setReadings(plan); update(); });
  addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(refresh, 200); });
  update();
}
