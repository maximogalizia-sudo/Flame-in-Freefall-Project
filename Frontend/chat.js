// Chat panel: conversation with history and, for each answer, the decision label, the written answer, prediction
// cards, the real NASA tests, citations and a "see how it was answered" panel (docs/PLAN_ASISTENTE.md, M5).
// Everything in the answer comes from the API (contract v2: flame/contracts.py `Answer`) and is escaped before it is
// put in the page.
import { ask, isOnline } from "./api.js";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v, d = 0) => `${(v * 100).toLocaleString("en-US", { maximumFractionDigits: d })} %`;
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

const EXAMPLES = [
  "Does a methanol droplet go out with 30 % CO2 and 18 % oxygen?",
  "How many Nomex tests were there, and in how many did the flame not sustain?",
  "Why did FLEX droplets break up?",
  "What is a cool flame?",
];

// decision -> label (text and shape, not only colour)
const DECISIONS = {
  answer:  { label: "Answer",             icon: "✓", hint: "The assistant answered from the NASA data and reports." },
  partial: { label: "Partial answer",     icon: "◐", hint: "Part of the question could not be answered from the data, or the assistant was not fully available." },
  abstain: { label: "Not enough data",    icon: "∅", hint: "The NASA data cannot answer the central question. The answer says why." },
};

const TARGETS = [
  [/^extinction_before_burnout$/, () => "Flame goes out before the droplet burns completely"],
  [/^droplet_breakup$/, () => "Droplet breaks up (possible experiment problem, not a flame property)"],
  [/^self_extinction_within_(.+)s$/, (m) => `Flame goes out by itself within ${m[1]} s`],
  [/^ignition$/, () => "Gas flame lights"],
  [/^no_model$/, () => "No predictive model for this material"],
];
const targetLabel = (t) => {
  for (const [re, f] of TARGETS) { const m = String(t).match(re); if (m) return f(m); }
  return String(t).replaceAll("_", " ");
};

// ------------------------------------------------------------------ rendering one answer
function predictionCard(p) {
  const head = `<h4>${esc(targetLabel(p.target))}</h4>`;
  const foot = `<p class="muted">${esc(p.model_version)}</p>`;
  const notes = (items) => (items?.length ? items.map((n) => `<p class="note"><span aria-hidden="true">ⓘ</span> ${esc(n)}</p>`).join("") : "");
  const list = (items, cls = "") => (items?.length ? `<ul class="${cls}">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "");
  const closest = p.nearest_tests?.length ? `<p class="muted">Closest real tests: ${p.nearest_tests.map((t) => `<code>${esc(t)}</code>`).join(", ")}</p>` : "";

  if (p.probability == null || !p.in_domain) {
    return `<article class="pred pred-out">${head}
      <p class="pred-flag"><span aria-hidden="true">∅</span> Outside the NASA data: no estimate is given</p>
      ${list(p.out_of_domain, "reasons")}${notes(p.notes)}${closest}${foot}</article>`;
  }
  const lo = p.ci_low ?? p.probability, hi = p.ci_high ?? p.probability;
  const range = p.range_sensitivity?.length === 2
    ? `<p class="sens">Sensitivity: if every interrupted test (droplet break-up) were counted as "went out" or as "burned completely", the estimate would be <b>${pct(p.range_sensitivity[0])}–${pct(p.range_sensitivity[1])}</b>.</p>` : "";
  return `<article class="pred">${head}
    <p class="pred-value"><b>${pct(p.probability)}</b> <span class="muted">95 % interval ${pct(lo)}–${pct(hi)}</span></p>
    <div class="pred-bar" role="img" aria-label="Estimate ${pct(p.probability)}, 95 % interval ${pct(lo)} to ${pct(hi)}">
      <span class="pred-ci" style="left:${(lo * 100).toFixed(1)}%;width:${Math.max(0.8, (hi - lo) * 100).toFixed(1)}%"></span>
      <span class="pred-dot" style="left:${(p.probability * 100).toFixed(1)}%"></span>
    </div>
    ${range}
    ${p.assumptions?.length ? `<p class="muted">Assumed, because it was not given:</p>${list(p.assumptions, "assumed")}` : ""}
    ${notes(p.notes)}${closest}${foot}</article>`;
}

function evidenceList(items) {
  if (!items?.length) return "";
  const rows = items.map((e) => `<li><b>${esc(e.test_id)}</b> <span class="tag">${esc(e.outcome ?? "no outcome")}</span>
    <span class="muted">${esc(e.psi_id)} · ${esc(e.source)}</span></li>`);
  const shown = rows.slice(0, 5).join("");
  const more = rows.length > 5 ? `<details class="more"><summary>${rows.length - 5} more tests</summary><ul class="evidence">${rows.slice(5).join("")}</ul></details>` : "";
  return `<h3>Real NASA tests</h3><ul class="evidence">${shown}</ul>${more}`;
}

function citationList(items) {
  if (!items?.length) return "";
  return `<h3>From the NASA reports</h3><ul class="cites">${items.map((c) => {
    const url = safeUrl(c.url);
    const where = [c.title ?? c.doc_id, c.page_or_section].filter(Boolean).map(esc).join(", ");
    const link = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(c.psi_id ?? "source")} ↗</a>` : esc(c.psi_id ?? "");
    return `<li><q>${esc(c.snippet)}</q><span class="muted">${where} · ${link}</span></li>`;
  }).join("")}</ul>`;
}

function howAnswered(a) {
  const tools = a.tools_used?.length ? a.tools_used.map((t) => `<code>${esc(t)}</code>`).join(" ") : `<span class="muted">none</span>`;
  const problems = a.verification?.length;
  const verdict = problems
    ? `<ul class="reasons">${a.verification.map((v) => `<li>${esc(v)}</li>`).join("")}</ul>`
    : `<p>The verifier found no problems: every number in the answer is in a tool output.</p>`;
  const models = [...new Set((a.predictions ?? []).map((p) => p.model_version))];
  return `<details class="how"><summary>See how it was answered</summary><dl>
    <dt>Tools used</dt><dd>${tools}</dd>
    <dt>Verifier</dt><dd>${verdict}</dd>
    <dt>Question understood as</dt><dd>${esc(a.intent)}${a.unknown_terms?.length ? ` · not measured by NASA: ${esc(a.unknown_terms.join(", "))}` : ""}</dd>
    ${models.length ? `<dt>Models</dt><dd>${models.map((m) => `<code>${esc(m)}</code>`).join(" ")}</dd>` : ""}
    <dt>Trace</dt><dd>${a.trace_id ? `<code>${esc(a.trace_id)}</code>` : `<span class="muted">none</span>`}${a.assistant_version ? ` · ${esc(a.assistant_version)}` : ""}</dd>
  </dl></details>`;
}

export function renderAnswer(a) {
  const d = DECISIONS[a.decision] ?? DECISIONS.answer;
  return `<div class="decision decision-${esc(a.decision)}" title="${esc(d.hint)}"><span aria-hidden="true">${d.icon}</span> ${d.label}</div>
    <div class="answer-text">${esc(a.text).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>").replace(/^/, "<p>").concat("</p>")}</div>
    ${(a.predictions ?? []).map(predictionCard).join("")}
    ${evidenceList(a.evidence)}${citationList(a.citations)}${howAnswered(a)}`;
}

// ------------------------------------------------------------------ the panel
export function setupChat() {
  const form = $("#chat-form"), input = $("#chat-input"), btn = $("#chat-btn"), log = $("#chat-log");
  const examples = $("#chat-examples"), clear = $("#chat-clear");
  if (!isOnline()) {
    input.disabled = btn.disabled = true;
    $("#chat-sub").textContent = "Needs the backend API. This page is running offline on static data.";
    return;
  }
  const history = [];                       // [{role, text}] sent with each question

  examples.innerHTML = `<span class="muted">Try:</span> ${EXAMPLES.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join("")}`;
  examples.addEventListener("click", (e) => {
    const q = e.target.closest(".chip")?.textContent;
    if (q) { input.value = q; form.requestSubmit(); }
  });
  clear.addEventListener("click", () => { history.length = 0; log.innerHTML = ""; clear.hidden = true; examples.hidden = false; input.focus(); });

  const addTurn = (cls, html) => {
    const div = document.createElement("div");
    div.className = `turn ${cls}`;
    div.innerHTML = html;
    log.appendChild(div);
    div.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return div;
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (text.length < 3) { input.focus(); return; }
    input.value = "";
    btn.disabled = true;
    examples.hidden = true;
    clear.hidden = false;
    addTurn("turn-user", `<p>${esc(text)}</p>`);
    const pending = addTurn("turn-bot", `<p class="muted thinking">Reading the data and the reports… <span id="chat-wait"></span></p>`);
    const t0 = Date.now(), timer = setInterval(() => {
      const w = pending.querySelector("#chat-wait");
      if (w) w.textContent = `${Math.round((Date.now() - t0) / 1000)} s (can take up to 90 s)`;
    }, 1000);
    try {
      const a = await ask(text, history);
      pending.innerHTML = renderAnswer(a);
      history.push({ role: "user", text }, { role: "assistant", text: a.text.slice(0, 2000) });
    } catch (err) {
      const slow = err.name === "TimeoutError" || err.name === "AbortError";
      pending.innerHTML = `<div class="decision decision-error"><span aria-hidden="true">!</span> No answer</div>
        <p>${slow ? "The assistant took too long to answer." : "Could not get an answer."} <span class="muted">${esc(err.message)}</span></p>`;
    } finally {
      clearInterval(timer);
      btn.disabled = false;
      input.focus();
    }
  });
}
