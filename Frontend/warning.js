// "If a flame starts here" panel (contracts v3-v5).
// Every NASA test in these models started with a flame lit ON PURPOSE (igniter). The question the models answer is:
// once there is a flame, does it keep burning or go out by itself, and how fast? The sliders stand for sensor
// readings (moved by hand in the demo). The panel shows, live:
//   1. the state at the current readings (POST /compare, its `change` side),
//   2. what the last change did ("before -> now", the readings before you touched a slider; no plan to set up),
//   3. the curve of the variable you just moved with the value where the flame starts to go out by itself (POST /sweep),
//   4. how long the flame lasts (POST /duration), and, folded, every kind of fire at these readings (POST /risk).
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

const SLIDERS = [
  { id: "o2",  field: "o2_frac",      min: 10, max: 40,  step: 1, value: 21,    unit: "%",   label: "Oxygen" },
  { id: "p",   field: "pressure_kpa", min: 50, max: 310, step: 1, value: 101.3, unit: "kPa", label: "Pressure" },
  { id: "co2", field: "co2_frac",     min: 0,  max: 50,  step: 1, value: 0,     unit: "%",   label: "CO₂ added" },
  { id: "he",  field: "he_frac",      min: 0,  max: 60,  step: 1, value: 0,     unit: "%",   label: "Helium added" },
];
const PRESETS = [
  { label: "ISS 21 % / 101 kPa", o2: 21, p: 101.3, co2: 0, he: 0 },
  { label: "Exploration 34 % / 70 kPa", o2: 34, p: 70.3, co2: 0, he: 0 },
  { label: "18 % O₂ + 30 % CO₂", o2: 18, p: 101.3, co2: 30, he: 0 },
];
const FIRES = [
  { key: "butane", label: "Butane gas flame", fuel: "butane", gas: true },
  { key: "propane", label: "Propane gas flame", fuel: "propane", gas: true },
  { key: "ethane", label: "Ethane gas flame", fuel: "ethane", gas: true },
  { key: "methanol", label: "Methanol droplet", fuel: "methanol" },
  { key: "heptane", label: "n-Heptane droplet", fuel: "heptane" },
];
const VARIABLES = [
  { key: "o2_frac", slider: "o2", label: "Oxygen", unit: "%", scale: 100 },
  { key: "pressure_kpa", slider: "p", label: "Pressure", unit: "kPa", scale: 1 },
  { key: "co2_frac", slider: "co2", label: "CO₂ added", unit: "%", scale: 100 },
  { key: "he_frac", slider: "he", label: "Helium added", unit: "%", scale: 100 },
];
const HORIZON_S = 30;   // gas flames: "still burning after 30 s" (the /risk default)

const val = (id) => Number($(`#ew-${id}`).value);
const fire = () => FIRES.find((f) => f.key === $("#ew-fire").value) ?? FIRES[0];
const readings = () => ({ o2: val("o2"), p: val("p"), co2: val("co2"), he: val("he") });
const atmosphere = (r) => ({ o2_frac: r.o2 / 100, pressure_kpa: r.p, co2_frac: r.co2 / 100, he_frac: r.he / 100 });
function conditions(r, f = fire()) {
  const a = atmosphere(r);
  return { fuel: f.fuel, o2_frac: a.o2_frac, pressure_kpa: a.pressure_kpa,
    ...(a.co2_frac ? { co2_frac: a.co2_frac } : {}), ...(a.he_frac ? { he_frac: a.he_frac } : {}),
    ...(f.gas ? { horizon_s: HORIZON_S } : {}) };
}
const describe = (r) => `O₂ ${r.o2} %, ${+r.p.toFixed(1)} kPa, CO₂ ${r.co2} %, He ${r.he} %`;
const same = (a, b) => SLIDERS.every((s) => Math.abs(a[s.id] - b[s.id]) < 1e-9);
const fmtS = (m) => (m != null ? `~${m} s` : "over 300 s");

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
  return `<div class="ew-bar" role="img" aria-label="${esc(what)} ${pct(v)}, 95 % interval ${pct(lo)} to ${pct(hi)}">
      <span class="ew-ci" style="left:${(lo * 100).toFixed(1)}%;width:${Math.max(0.8, (hi - lo) * 100).toFixed(1)}%"></span>
      <span class="ew-dot" style="left:${(v * 100).toFixed(1)}%"></span>
    </div>`;
}

// ------------------------------------------------------------------ 1-2. current state + last change (POST /compare)
function renderState(c, before, now, f) {
  const s = c.change, l = LEVELS[s.level] ?? LEVELS.gray;
  const what = f.gas ? `Chance it is still burning after ${HORIZON_S} s` : "Chance the droplet burns completely before the flame goes out";
  const body = s.in_domain
    ? `<p class="ew-big">${f.gas ? "If a flame starts here, it" : "If the droplet is lit here, the flame"} <b>${s.level === "red" ? "tends to keep burning" : s.level === "green" ? "tends to go out by itself" : "could go either way"}</b>${f.gas ? `: it goes out by itself in <b>${fmtS(s.median_s)}</b> <span class="muted">(median)</span>` : ""}.</p>
       <div class="ew-item-val">${bar(s.persistence, s.ci_low, s.ci_high, what)}
         <span class="ew-num"><b>${pct(s.persistence)}</b> <span class="muted">(${pct(s.ci_low)}–${pct(s.ci_high)})</span></span></div>
       <p class="ew-meaning muted">${esc(what)} (dot) and its 95 % interval.</p>`
    : `<p class="ew-big">NASA never tested this combination: <b>no estimate</b> (unknown, not safe).</p>
       <p class="ew-reason">${esc(s.reasons?.[0] ?? "")}</p>`;
  let change = "";
  if (!same(before, now)) {
    const p = c.plan;
    const arrow = (a, b) => `${a} → <b>${b}</b>`;
    const parts = [];
    if (p.persistence != null && s.persistence != null) parts.push(arrow(pct(p.persistence), pct(s.persistence)));
    if (p.level !== s.level) parts.push(arrow(LEVELS[p.level]?.label ?? p.level, l.label));
    if (f.gas && p.in_domain && s.in_domain && p.median_s !== s.median_s) parts.push(`goes out in ${arrow(fmtS(p.median_s), fmtS(s.median_s))}`);
    const moved = c.changes.map((r) => r.label).join(", ");
    change = `<div class="ew-last">
        <p><span class="muted">Last change</span> ${esc(moved)}: ${parts.join(" · ") || (c.verdict === "unknown" ? "now outside the NASA data" : "about the same")}</p>
        ${c.changes.length > 1 ? `<ul class="ew-changes">${c.changes.map((r) => `<li class="ew-ch-${esc(r.direction.replaceAll(" ", "-"))}">${esc(r.text)}</li>`).join("")}</ul>` : ""}
        <button type="button" class="chip ew-discuss" id="ew-discuss">Ask the assistant why ↓</button>
      </div>`;
  }
  $("#ew-result").innerHTML = `
    <div class="ew-overall ew-${esc(s.level)}" role="status">
      <span class="ew-light" aria-hidden="true">${l.icon}</span>
      <div><p class="ew-level">${esc(l.label)}</p><p class="ew-headline">${esc(f.label)} · ${esc(describe(now))}</p></div>
    </div>
    ${body}${change}
    <p class="ew-disclaimer"><span aria-hidden="true">ⓘ</span> ${esc(c.disclaimer)} Sensor readings here are simulated: you move them.</p>`;
  $("#ew-discuss")?.addEventListener("click", () => dispatchEvent(new CustomEvent("flame:ask", { detail: discussQuestion(before, now, f) })));
}

// The question sent to the chat: "change <variable> from <before> to <now>" is read by the assistant as a change
// (it calls the same /compare tool and explains the result with the NASA reports).
const ASK_WORDS = { o2: ["oxygen", "%"], p: ["pressure", " kPa"], co2: ["CO2", "%"], he: ["helium", "%"] };
function discussQuestion(before, now, f) {
  const moved = SLIDERS.filter((s) => Math.abs(now[s.id] - before[s.id]) > 1e-9).map((s) => s.id);
  const [first, ...rest] = moved;
  const [word, unit] = ASK_WORDS[first];
  const ctx = SLIDERS.filter((s) => s.id !== first && (s.id === "o2" || s.id === "p" || now[s.id] > 0))
    .map((s) => `${+now[s.id].toFixed(1)}${ASK_WORDS[s.id][1]} ${ASK_WORDS[s.id][0]}`).join(", ");
  const also = rest.length ? ` (I also changed ${rest.map((id) => ASK_WORDS[id][0]).join(" and ")})` : "";
  return `What happens if I change ${word} from ${+before[first].toFixed(1)}${unit} to ${+now[first].toFixed(1)}${unit} in a ${f.label.toLowerCase()} at ${ctx}${also}? Why?`;
}

// ------------------------------------------------------------------ 3. "what if" curve + where the flame starts to go out
function crossing(pts) {
  // first x where the chance of keeping burning crosses 50 % between two tested points (linear interpolation)
  for (let k = 1; k < pts.length; k++) {
    const a = pts[k - 1], b = pts[k];
    if (!a.in || !b.in || a.y === b.y) continue;
    if ((a.y - 0.5) * (b.y - 0.5) <= 0) return { x: a.x + ((0.5 - a.y) / (b.y - a.y)) * (b.x - a.x), downward: b.y < a.y };
  }
  return null;
}

function renderSweep(s, variable, beforeX, nowX) {
  const box = $("#ew-sweep-chart");
  const v = VARIABLES.find((x) => x.key === variable);
  const pts = s.points.map((p) => ({ x: p.x * v.scale, in: p.probability != null,
    y: p.probability == null ? null : 1 - p.probability,
    lo: p.ci_high == null ? null : 1 - p.ci_high, hi: p.ci_low == null ? null : 1 - p.ci_low }));
  box.innerHTML = "";
  const tested = pts.filter((p) => p.in);
  const fmt = (x) => `${+x.toFixed(1)} ${v.unit}`;
  if (!tested.length) {
    box.innerHTML = `<p class="muted">NASA never tested this fire anywhere along ${esc(v.label.toLowerCase())} with the other readings as they are: no curve.</p>`;
    $("#ew-threshold").innerHTML = "";
    $("#ew-sweep-note").textContent = "";
    return;
  }
  const cr = crossing(pts);
  const lo = Math.min(...tested.map((p) => p.x)), hi = Math.max(...tested.map((p) => p.x));
  const first = tested[0], last = tested[tested.length - 1];
  // the 50 % point can also fall inside a stretch NASA never tested: then say between which tested values it flips
  let gap = null;
  for (let k = 1; k < tested.length && !cr; k++) {
    if ((tested[k - 1].y >= 0.5) !== (tested[k].y >= 0.5)) { gap = [tested[k - 1], tested[k]]; break; }
  }
  const pre = "With the other readings as they are,";
  $("#ew-threshold").innerHTML = cr
    ? `${pre} the chance it keeps burning <b>${cr.downward ? "drops below" : "rises above"} 50 % at ~${esc(fmt(cr.x))}</b> of ${esc(v.label)} (the circle on the curve).`
    : gap
      ? `${pre} the chance it keeps burning goes from ${pct(gap[0].y)} at ${esc(fmt(gap[0].x))} to ${pct(gap[1].y)} at ${esc(fmt(gap[1].x))}: <b>it flips somewhere in between, but NASA never tested that stretch</b> (hatched), so the exact point is unknown.`
      : `${pre} the chance it keeps burning goes from ${pct(first.y)} (at ${esc(fmt(first.x))}) to ${pct(last.y)} (at ${esc(fmt(last.x))}): it <b>${first.y >= 0.5 ? "stays above" : "stays below"} 50 % everywhere NASA tested</b>, so ${esc(v.label)} alone does not flip it.`;

  const W = Math.max(320, box.clientWidth || 600), H = 220, m = { l: 44, r: 12, t: 12, b: 36 };
  const xs = pts.map((p) => p.x), x0 = Math.min(...xs), x1 = Math.max(...xs);
  const X = (x) => m.l + ((x - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const Y = (y) => m.t + (1 - y) * (H - m.t - m.b);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img",
    "aria-label": `Chance the flame keeps burning as ${v.label.toLowerCase()} changes` }, box);
  const pat = el("pattern", { id: "ew-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    el("defs", {}, svg));
  el("rect", { width: 6, height: 6, fill: "var(--page)" }, pat);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--grid)", "stroke-width": 3 }, pat);
  for (const y of [0, 0.5, 1]) {
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
  if (cr) el("circle", { cx: X(cr.x), cy: Y(0.5), r: 5, class: "ew-cross" }, svg);
  const every = Math.max(1, Math.round(pts.length / 6));
  pts.forEach((p, k) => {
    if (k % every) return;
    el("text", { x: X(p.x), y: H - m.b + 16, "text-anchor": "middle", class: "tick" }, svg).textContent = `${+p.x.toFixed(1)}`;
  });
  el("text", { x: (m.l + W - m.r) / 2, y: H - 4, "text-anchor": "middle", class: "tick" }, svg).textContent = `${v.label} (${v.unit})`;
  const mark = (xv, label, cls, row) => {
    if (xv < x0 || xv > x1) return;
    el("line", { x1: X(xv), x2: X(xv), y1: m.t, y2: H - m.b, class: cls }, svg);
    const right = X(xv) > W - m.r - 90;
    el("text", { x: X(xv) + (right ? -4 : 4), y: H - m.b - 6 - row * 14, "text-anchor": right ? "end" : "start", class: "tick" }, svg).textContent = label;
  };
  if (Math.abs(nowX - beforeX) > 1e-9) mark(beforeX, "before", "ew-plan-mark", 1);
  mark(nowX, "now", "ew-now", 0);
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
  $("#ew-sweep-note").textContent = `Line: chance the flame keeps burning (model) as ${v.label.toLowerCase()} changes, the other readings as they are now. Hatched = NASA never tested it: no estimate.`;
}

// ------------------------------------------------------------------ 4. duration (POST /duration)
function renderDuration(dNow, dBefore, f) {
  const box = $("#ew-dur-chart"), note = $("#ew-dur-note");
  box.innerHTML = ""; note.textContent = "";
  if (!f.gas) {
    const rows = (dNow.observed ?? []).map((o) => `<tr><td><code>${esc(o.test_id)}</code></td><td>${o.o2_pct} %</td><td>${o.co2_pct} %</td><td>${o.he_pct} %</td>
      <td><b>${o.burn_time_s.toFixed(1)} s</b></td><td>${esc(o.ended)}</td></tr>`).join("");
    box.innerHTML = rows
      ? `<div class="table-wrap"><table class="ew-obs"><thead><tr><th>Closest NASA test</th><th>O₂</th><th>CO₂</th><th>He</th><th>Burned for</th><th>How it ended</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="muted">No NASA droplet test close to these readings.</p>`;
    note.textContent = (dNow.notes ?? []).join(" ");
    return;
  }
  const curves = [...(dBefore ? [{ d: dBefore, cls: "ew-line-plan", name: "before" }] : []), { d: dNow, cls: "ew-line", name: "now" }]
    .filter((c) => c.d.curve?.length);
  if (!dNow.curve?.length) {
    box.innerHTML = `<p class="muted">No time curve at these readings: ${esc((dNow.reasons ?? []).join("; ") || "NASA did not test this combination")}.</p>`;
    note.textContent = (dNow.observed ?? []).filter((o) => o.burn_time_s != null)
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
  for (const o of (dNow.observed ?? []).filter((o) => o.burn_time_s != null && o.burn_time_s <= T)) {
    el("line", { x1: X(o.burn_time_s), x2: X(o.burn_time_s), y1: H - m.b - 8, y2: H - m.b, class: "ew-obs-tick" }, svg)
      .appendChild(el("title")).textContent = `${o.test_id}: ${o.burn_time_s} s (${o.ended})`;
  }
  note.textContent = `Line: share of such flames still burning (model)${curves.length > 1 ? "; dashed = before the last change" : ""}. Ticks on the axis: burn times of the closest real NASA flames. ` + (dNow.notes ?? []).join(" ");
}

// ------------------------------------------------------------------ every kind of fire at these readings (POST /risk)
function renderRisk(r) {
  const row = (i) => `<li class="ew-item">
      <div class="ew-item-head"><b>${esc(i.scenario)}</b>${badge(i.level)}</div>
      <div class="ew-item-val">${i.persistence != null ? `${bar(i.persistence, i.ci_low, i.ci_high, "Chance it keeps burning")}<span class="ew-num">${pct(i.persistence)} <span class="muted">(${pct(i.ci_low)}–${pct(i.ci_high)})</span></span>`
        : `<span class="ew-num muted">${i.level === "gray" ? "no estimate" : "evidence only, no model"}</span>`}</div>
      <p class="ew-reason">${esc(i.reasons?.[0] ?? "")}</p></li>`;
  $("#ew-all").innerHTML = `<p class="ew-headline">${esc(r.headline)}</p><ul class="ew-items">${r.items.map(row).join("")}</ul>
    <p class="muted ew-model">${esc(r.model_version)}</p>`;
  $("#ew-all-level").innerHTML = badge(r.level);
}

// ------------------------------------------------------------------ wiring
export function setupWarning() {
  const panel = $("#ew");
  if (!isOnline()) {
    $("#ew-result").innerHTML = `<p class="muted">This panel needs the backend API. This page is running offline on static data.</p>`;
    panel.querySelectorAll("input, button, select").forEach((n) => { n.disabled = true; });
    return;
  }

  $("#ew-fire").innerHTML = FIRES.map((f) => `<option value="${f.key}">${esc(f.label)}</option>`).join("");
  $("#ew-controls").innerHTML = SLIDERS.map((s) => `
    <label class="ew-slider" for="ew-${s.id}">
      <span class="ew-slider-head"><span>${s.label} <span class="muted">· sensor</span></span><output id="ew-${s.id}-out"></output></span>
      <input type="range" id="ew-${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}">
    </label>`).join("");
  $("#ew-presets").innerHTML = `<span class="muted">Start from:</span> ` +
    PRESETS.map((p, k) => `<button type="button" class="chip" data-k="${k}">${esc(p.label)}</button>`).join("");
  $("#ew-var").innerHTML = VARIABLES.map((v) => `<option value="${v.key}">${esc(v.label)}</option>`).join("");

  // "before" = the readings just before the user touched a slider (no plan to set up). A new touch after a pause
  // starts a new change; presets and a new fire start fresh.
  let before = readings(), lastTouch = 0;
  const touch = () => { if (Date.now() - lastTouch > 1500) before = readings(); lastTouch = Date.now(); };
  $("#ew-controls").addEventListener("pointerdown", touch);
  $("#ew-controls").addEventListener("keydown", touch);

  const showValues = () => SLIDERS.forEach((s) => {
    const now = val(s.id), moved = Math.abs(now - before[s.id]) > 1e-9;
    $(`#ew-${s.id}-out`).textContent = moved ? `${+before[s.id].toFixed(1)} → ${+now.toFixed(1)} ${s.unit}` : `${+now.toFixed(1)} ${s.unit}`;
    $(`#ew-${s.id}`).closest(".ew-slider").classList.toggle("ew-moved", moved);
  });

  let ctl, timer;
  async function refresh() {
    ctl?.abort(); ctl = new AbortController();
    const signal = ctl.signal, f = fire(), now = readings(), changed = !same(before, now), b = { ...before };
    const variable = $("#ew-var").value, v = VARIABLES.find((x) => x.key === variable);
    const fail = (sel, what) => (e) => { if (e.name !== "AbortError") $(sel).innerHTML = `<p class="muted">Could not get ${what}: ${esc(e.message)}</p>`; };
    getCompare({ plan: conditions(b, f), change: conditions(now, f) }, signal)
      .then((c) => renderState(c, b, now, f)).catch(fail("#ew-result", "the state"));
    getSweep({ conditions: conditions(now, f), variable }, signal)
      .then((s) => renderSweep(s, variable, atmosphere(b)[variable] * v.scale, atmosphere(now)[variable] * v.scale))
      .catch(fail("#ew-sweep-chart", "the curve"));
    Promise.all([getDuration(conditions(now, f), signal), changed && f.gas ? getDuration(conditions(b, f), signal) : null])
      .then(([dn, db]) => renderDuration(dn, db, f)).catch(fail("#ew-dur-chart", "the duration"));
    getRisk(atmosphere(now), signal).then(renderRisk).catch(fail("#ew-all", "the indicator"));
  }
  const update = () => { showValues(); clearTimeout(timer); timer = setTimeout(refresh, 150); };
  const fresh = () => { before = readings(); update(); };

  $("#ew-controls").addEventListener("input", (e) => {
    const v = VARIABLES.find((x) => `ew-${x.slider}` === e.target.id);
    if (v) $("#ew-var").value = v.key;               // the curve follows the variable you are moving
    update();
  });
  $("#ew-fire").addEventListener("change", fresh);
  $("#ew-var").addEventListener("change", update);
  $("#ew-presets").addEventListener("click", (e) => {
    const p = PRESETS[e.target.closest(".chip")?.dataset.k];
    if (!p) return;
    SLIDERS.forEach((s) => { $(`#ew-${s.id}`).value = p[s.id]; });
    fresh();
  });
  addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(refresh, 200); });
  update();
}
