// Data layer of the dashboard. The frontend only talks to the backend API.
//
// API base URL, in order of precedence:
//   1. ?api=https://... in the page URL (handy for testing a deploy)
//   2. <meta name="flame-api" content="https://..."> in index.html (set at deploy time)
// With no API configured, the page runs in offline mode on static JSON exported from
// the same mart views (Backend/tools/pglite_check.mjs), so the shapes are identical.
const fromQuery = new URLSearchParams(location.search).get("api");
const fromMeta = document.querySelector('meta[name="flame-api"]')?.content;
export const API_BASE = (fromQuery || fromMeta || "").replace(/\/$/, "") || null;

const cache = new Map();

async function getJSON(url, options) {
  const key = options ? null : url;
  if (key && cache.has(key)) return cache.get(key);
  const p = fetch(url, options).then(async (r) => {
    if (!r.ok) throw new Error(`${r.status} on ${url}`);
    return r.json();
  });
  if (key) cache.set(key, p);
  return p;
}

const api = (path) => getJSON(`${API_BASE}${path}`);
const offline = (file) => getJSON(`./data/${file}`);

export const isOnline = () => Boolean(API_BASE);

export const getStats = async () => (API_BASE ? api("/stats") : (await offline("stats.json"))[0]);
export const getInvestigations = () => (API_BASE ? api("/investigations") : offline("investigations.json"));
export const getTests = () => (API_BASE ? api("/tests") : offline("tests.json"));

export async function getMeasurements(testId) {
  if (API_BASE) return api(`/tests/${encodeURIComponent(testId)}/measurements`);
  return (await offline("measurements.json")).filter((m) => m.test_id === testId);
}

export async function getSteps(testId) {
  if (API_BASE) return api(`/tests/${encodeURIComponent(testId)}/steps`);
  return (await offline("test_steps.json")).filter((s) => s.test_id === testId);
}

// Early warning (contract v3): POST /risk for one atmosphere, POST /sweep to vary one input. Fast (no LLM).
const postJSON = (path, body, signal) => getJSON(`${API_BASE}${path}`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});
export const getRisk = (atmosphere, signal) => postJSON("/risk", atmosphere, signal);
export const getSweep = (req, signal) => postJSON("/sweep", req, signal);

// POST /ask. `history` = previous turns [{role: "user"|"assistant", text}] (the API accepts up to 10).
// The assistant service can take up to 90 s, so the browser waits a little longer than the API does.
export async function ask(text, history = [], lang) {
  if (!API_BASE) throw new Error("The question box needs the backend API (offline mode).");
  return getJSON(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, history: history.slice(-10), ...(lang ? { lang } : {}) }),
    signal: AbortSignal.timeout(100_000),
  });
}
