// Valida el esquema en Postgres real (PGlite = Postgres compilado a WASM, sin instalar nada)
// y exporta las vistas mart a JSON para el mini front.
//
//   cd Backend/tools && npm install && npm run check
//
// Requiere haber corrido antes:  .venv/Scripts/python -m pipeline.load
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite/vector";
import { readFileSync, readdirSync, mkdirSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const backend = join(here, "..");
const sqlDir = join(backend, "sql");
const seedPath = join(backend, "data", "core", "seed.sql");
const outDir = join(backend, "..", "Frontend", "data");

// numeric (1700) e int8 (20) se pasan a number; date (1082) queda como 'YYYY-MM-DD' (sin zona horaria).
const db = new PGlite({ extensions: { vector }, parsers: { 1700: Number, 20: Number, 1082: (v) => v } });

const run = async (label, sql) => {
  try {
    await db.exec(sql);
    console.log(`ok  ${label}`);
  } catch (e) {
    console.error(`FAIL ${label}: ${e.message}${e.detail ? `\n     ${e.detail}` : ""}`);
    process.exit(1);
  }
};

const t0 = Date.now();
for (const f of readdirSync(sqlDir).filter((f) => f.endsWith(".sql")).sort()) {
  await run(f, readFileSync(join(sqlDir, f), "utf8"));
}
await run(`seed.sql (${((Date.now() - t0) / 1000).toFixed(1)} s)`, readFileSync(seedPath, "utf8"));
// Se re-aplica todo: DDL y seed tienen que ser idempotentes.
for (const f of readdirSync(sqlDir).filter((f) => f.endsWith(".sql")).sort()) {
  await run(`${f} (2da vez)`, readFileSync(join(sqlDir, f), "utf8"));
}
await run("seed.sql (2da vez)", readFileSync(seedPath, "utf8"));

// ------------------------------------------------------------ chequeos
const one = async (sql) => (await db.query(sql)).rows[0];
const checks = [
  ["24 investigaciones", "SELECT count(*) = 24 AS ok FROM core.dim_investigation"],
  ["620 ensayos", "SELECT count(*) = 620 AS ok FROM core.fact_test_run"],
  ["todo ensayo tiene medidas", "SELECT NOT EXISTS (SELECT 1 FROM core.fact_test_run r WHERE NOT EXISTS (SELECT 1 FROM core.fact_measurement m WHERE m.test_run_id = r.test_run_id)) AS ok"],
  ["O2 en [0,1]", "SELECT bool_and(o2_mole_fraction BETWEEN 0 AND 1) AS ok FROM core.fact_test_run WHERE o2_mole_fraction IS NOT NULL"],
  ["presión > 0", "SELECT bool_and(pressure_atm > 0) AS ok FROM core.fact_test_run WHERE pressure_atm IS NOT NULL"],
  ["FLEX: 186 extinciones", "SELECT count(*) = 186 AS ok FROM mart.v_test_runs WHERE accession = 'PSI-69' AND outcome = 'extinction'"],
  ["vistas mart responden", "SELECT (SELECT count(*) FROM mart.v_investigations) = 24 AND (SELECT count(*) FROM mart.v_tested_envelope) > 0 AS ok"],
];
let failed = 0;
for (const [name, sql] of checks) {
  const { ok } = await one(sql);
  console.log(`${ok ? "ok " : "FAIL"} ${name}`);
  if (!ok) failed++;
}

// ------------------------------------------------------------ export para el front
mkdirSync(outDir, { recursive: true });
const exportView = async (file, sql) => {
  const { rows } = await db.query(sql);
  writeFileSync(join(outDir, file), JSON.stringify(rows));
  console.log(`exp ${file} (${rows.length} filas)`);
};
await exportView("stats.json", "SELECT * FROM mart.v_stats");
await exportView("investigations.json", "SELECT * FROM mart.v_investigations ORDER BY investigation_id");
await exportView("test_runs.json", "SELECT * FROM mart.v_test_runs ORDER BY test_run_id");
await exportView("measurements.json", "SELECT * FROM mart.v_measurements ORDER BY test_run_id, variable");
await exportView("outcome_summary.json", "SELECT * FROM mart.v_outcome_summary ORDER BY accession, material, outcome");
await exportView("envelope.json", "SELECT * FROM mart.v_tested_envelope ORDER BY family_group");
await exportView("outcomes.json", "SELECT code, label, self_extinguished FROM core.dim_outcome ORDER BY outcome_id");
await exportView("file_summary.json", "SELECT * FROM mart.v_file_summary ORDER BY accession, file_kind");

await db.close();
process.exit(failed ? 1 : 0);
