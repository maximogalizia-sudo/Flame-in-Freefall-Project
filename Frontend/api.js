// Capa de datos del tablero.
//
// Hoy lee JSON estáticos (Frontend/data/*.json) exportados desde las vistas mart.*
// por Backend/tools/pglite_check.mjs. Cuando exista la API, se cambia API_BASE
// (ej. "https://api.tu-dominio.com") y cada función pasa a pegarle al endpoint
// equivalente: la forma de los datos es la misma porque sale de las mismas vistas.
//
//   función            vista SQL                 endpoint futuro
//   getStats           mart.v_stats              GET /api/stats
//   getInvestigations  mart.v_investigations     GET /api/investigations
//   getTestRuns        mart.v_test_runs          GET /api/test-runs
//   getMeasurements    mart.v_measurements       GET /api/test-runs/:id/measurements
//   getEnvelope        mart.v_tested_envelope    GET /api/envelope
const API_BASE = null;

const cache = new Map();

async function getJSON(staticFile, endpoint) {
  const url = API_BASE ? `${API_BASE}${endpoint}` : `./data/${staticFile}`;
  if (!cache.has(url)) {
    cache.set(url, fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${r.status} al pedir ${url}`);
      return r.json();
    }));
  }
  return cache.get(url);
}

export const getStats = async () => (await getJSON("stats.json", "/api/stats"))[0];
export const getInvestigations = () => getJSON("investigations.json", "/api/investigations");
export const getTestRuns = () => getJSON("test_runs.json", "/api/test-runs");
export const getEnvelope = () => getJSON("envelope.json", "/api/envelope");

export async function getMeasurements(testRunId) {
  if (API_BASE) return getJSON(null, `/api/test-runs/${testRunId}/measurements`);
  const all = await getJSON("measurements.json");
  return all.filter((m) => m.test_run_id === testRunId);
}
