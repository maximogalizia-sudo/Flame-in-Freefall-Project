// Validates the schema on a real Postgres (PGlite = Postgres compiled to WASM, nothing to install)
// and exports the mart views as static JSON for the frontend's offline mode.
//
//   cd Backend/tools && npm install && npm run check
//
// Run first:  .venv/Scripts/python -m pipeline.load
// Also validates the RAG service schema (RAG/sql), which is NOT loaded into the backend database.
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite/vector";
import { readFileSync, readdirSync, mkdirSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const backend = join(here, "..");
const sqlDir = join(backend, "sql");
const ragSqlDir = join(backend, "..", "RAG", "sql");
const seedPath = join(backend, "data", "core", "seed.sql");
const outDir = join(backend, "..", "Frontend", "data");

// numeric (1700) and int8 (20) become numbers; date (1082) stays 'YYYY-MM-DD' (no time zone).
const db = new PGlite({ extensions: { vector }, parsers: { 1700: Number, 20: Number, 1082: (v) => v } });

const run = async (target, label, sql) => {
  try {
    await target.exec(sql);
    console.log(`ok   ${label}`);
  } catch (e) {
    console.error(`FAIL ${label}: ${e.message}${e.detail ? `\n     ${e.detail}` : ""}`);
    process.exit(1);
  }
};
const sqlFiles = (dir) => readdirSync(dir).filter((f) => f.endsWith(".sql")).sort();

const t0 = Date.now();
for (const f of sqlFiles(sqlDir)) await run(db, f, readFileSync(join(sqlDir, f), "utf8"));
await run(db, `seed.sql (${((Date.now() - t0) / 1000).toFixed(1)} s)`, readFileSync(seedPath, "utf8"));
// Everything is applied twice: DDL and seed must be idempotent.
for (const f of sqlFiles(sqlDir)) await run(db, `${f} (2nd run)`, readFileSync(join(sqlDir, f), "utf8"));
await run(db, "seed.sql (2nd run)", readFileSync(seedPath, "utf8"));

// The RAG schema lives in its own database (separate service): validated on another instance.
{
  const ragDb = new PGlite({ extensions: { vector } });
  for (const f of sqlFiles(ragSqlDir)) {
    const sql = readFileSync(join(ragSqlDir, f), "utf8");
    await run(ragDb, `RAG/${f}`, sql);
    await run(ragDb, `RAG/${f} (2nd run)`, sql);
  }
  await ragDb.close();
}

// ------------------------------------------------------------ checks (R1, R4, R5, R9, R12, R15)
const one = async (sql) => (await db.query(sql)).rows[0];
const checks = [
  ["24 investigations", "SELECT count(*) = 24 AS ok FROM core.dim_investigation"],
  ["1871 table rows in fact_test_run", "SELECT count(*) = 1871 AS ok FROM core.fact_test_run"],
  // 119 log rows + 11 E-FIELD continuation rows kept separate while DD-P07 is pending.
  ["130 log entries", "SELECT count(*) = 130 AS ok FROM core.fact_log_entry"],
  ["all 22 tables mapped and loaded", `SELECT bool_and(n > 0) AND count(*) = 22 AS ok FROM (
     SELECT st.source_table_id, (SELECT count(*) FROM core.fact_test_run r WHERE r.source_table_id = st.source_table_id)
            + (SELECT count(*) FROM core.fact_log_entry e WHERE e.source_table_id = st.source_table_id) AS n
     FROM core.dim_source_table st WHERE st.is_mapped) x`],
  ["R9 test_id unique and well-formed", "SELECT count(*) = count(DISTINCT test_id) AND bool_and(test_id ~ '^PSI-[0-9]+:') AS ok FROM core.fact_test_run"],
  ["R5 o2_frac within [0, 1]", "SELECT bool_and(o2_frac BETWEEN 0 AND 1) AS ok FROM core.fact_test_run WHERE o2_frac IS NOT NULL"],
  ["R5 pressure_kpa > 0 on valid tests", "SELECT bool_and(pressure_kpa > 0) AS ok FROM core.fact_test_run WHERE is_valid AND pressure_kpa IS NOT NULL"],
  ["R12 known errors marked, value kept", "SELECT count(*) = 1 AS ok FROM core.fact_test_run WHERE test_id = 'PSI-69:114' AND NOT is_valid AND pressure_kpa = 0"],
  ["R4 censored values carry a limit", "SELECT bool_and(censor_limit IS NOT NULL AND censored IS NOT NULL) AS ok FROM core.fact_measurement WHERE flag = 'censored'"],
  ["R6 flow sequences in test_step", "SELECT count(*) > 100 AS ok FROM core.test_step"],
  ["R13 Tad and Zst marked as calculated", "SELECT bool_and(measured_or_calculated = 'calculated') AS ok FROM core.dim_variable WHERE code IN ('tad_k', 'zst')"],
  ["pending decisions are not applied", "SELECT count(*) = 0 AS ok FROM core.fact_test_run WHERE EXISTS (SELECT 1 FROM unnest(quality_flags) f WHERE f LIKE 'fixed:%')"],
  ["FLEX: 186 extinctions", "SELECT count(*) = 186 AS ok FROM mart.v_tests WHERE psi_id = 'PSI-69' AND outcome = 'extinction'"],
  ["mart views respond", "SELECT (SELECT count(*) FROM mart.v_investigations) = 24 AND (SELECT count(*) FROM mart.v_tested_envelope) > 0 AS ok"],
];
let failed = 0;
for (const [name, sql] of checks) {
  const { ok } = await one(sql);
  console.log(`${ok ? "ok  " : "FAIL"} ${name}`);
  if (!ok) failed++;
}

// ------------------------------------------------------------ static export for the frontend (offline mode)
mkdirSync(outDir, { recursive: true });
const exportView = async (file, sql) => {
  const { rows } = await db.query(sql);
  writeFileSync(join(outDir, file), JSON.stringify(rows));
  console.log(`exp  ${file} (${rows.length} rows)`);
};
await exportView("stats.json", "SELECT * FROM mart.v_stats");
await exportView("investigations.json", "SELECT * FROM mart.v_investigations ORDER BY investigation_id");
await exportView("tests.json", "SELECT * FROM mart.v_tests WHERE row_type = 'test' ORDER BY test_id");
await exportView("measurements.json", "SELECT * FROM mart.v_measurements ORDER BY test_id, variable");
await exportView("test_steps.json", "SELECT * FROM mart.v_test_steps ORDER BY test_id, step_order");
await exportView("envelope.json", "SELECT * FROM mart.v_tested_envelope ORDER BY family_group");

await db.close();
process.exit(failed ? 1 : 0);
