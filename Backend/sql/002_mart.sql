-- =====================================================================
-- Flame in Freefall · esquema mart (lo que consume el tablero)
-- Vistas planas tipo estrella sobre core. Cada vista = un endpoint de la API.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS mart;

-- GET /api/investigations
CREATE OR REPLACE VIEW mart.v_investigations AS
SELECT i.investigation_id,
       i.accession,
       i.acronym,
       i.title,
       f.name           AS family,
       fg.code          AS family_group,
       fg.name          AS family_group_name,
       p.name           AS platform,
       i.start_date,
       i.end_date,
       i.doi,
       i.doi_url,
       i.license,
       i.page_url,
       i.ml_value,
       i.ml_value_rank,
       i.rag_value,
       i.rag_value_rank,
       i.key_variables,
       i.outcome_summary,
       COALESCE(h.hardware, '{}')    AS hardware,
       COALESCE(t.n_test_runs, 0)    AS n_test_runs,
       COALESCE(fl.n_files, 0)       AS n_files,
       COALESCE(fl.n_rag_docs, 0)    AS n_rag_documents,
       COALESCE(fl.gb, 0)            AS total_gb
FROM core.dim_investigation i
LEFT JOIN core.dim_family f        USING (family_id)
LEFT JOIN core.dim_family_group fg USING (family_group_id)
LEFT JOIN core.dim_platform p      USING (platform_id)
LEFT JOIN LATERAL (
    SELECT array_agg(hw.name ORDER BY hw.name) AS hardware
    FROM core.bridge_investigation_hardware b
    JOIN core.dim_hardware hw USING (hardware_id)
    WHERE b.investigation_id = i.investigation_id
) h ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS n_test_runs FROM core.fact_test_run r WHERE r.investigation_id = i.investigation_id
) t ON true
LEFT JOIN LATERAL (
    SELECT count(*)                                   AS n_files,
           count(*) FILTER (WHERE ff.is_rag_document) AS n_rag_docs,
           round(sum(ff.bytes) / 1e9, 2)              AS gb
    FROM core.fact_file ff WHERE ff.investigation_id = i.investigation_id
) fl ON true;

-- GET /api/test-runs
CREATE OR REPLACE VIEW mart.v_test_runs AS
SELECT r.test_run_id,
       i.accession,
       i.acronym,
       fg.code                AS family_group,
       st.table_name          AS source_table,
       r.source_row,
       r.test_code,
       d.full_date            AS test_date,
       m.code                 AS material_code,
       m.name                 AS material,
       fc.code                AS fuel_class,
       fc.phase               AS fuel_phase,
       o.code                 AS outcome,
       o.label                AS outcome_label,
       o.self_extinguished,
       r.flame_type,
       r.flow_direction,
       r.fuel_dilution,
       r.o2_mole_fraction,
       r.pressure_atm,
       r.flow_velocity_cm_s,
       r.burn_time_s,
       r.cool_flame,
       r.comment,
       r.quality_flags,
       i.doi_url
FROM core.fact_test_run r
JOIN core.dim_investigation i      USING (investigation_id)
JOIN core.dim_source_table st      USING (source_table_id)
JOIN core.dim_outcome o            USING (outcome_id)
LEFT JOIN core.dim_family f        ON f.family_id = i.family_id
LEFT JOIN core.dim_family_group fg ON fg.family_group_id = f.family_group_id
LEFT JOIN core.dim_material m      USING (material_id)
LEFT JOIN core.dim_fuel_class fc   USING (fuel_class_id)
LEFT JOIN core.dim_date d          ON d.date_key = r.test_date_key;

-- GET /api/test-runs/:id/measurements
CREATE OR REPLACE VIEW mart.v_measurements AS
SELECT ms.test_run_id,
       v.code       AS variable,
       v.label      AS variable_label,
       vg.code      AS variable_group,
       u.code       AS unit,
       ms.value_num,
       ms.value_min,
       ms.value_max,
       ms.value_text,
       ms.is_approx,
       ms.source_column,
       ms.source_raw
FROM core.fact_measurement ms
JOIN core.dim_variable v        USING (variable_id)
JOIN core.dim_variable_group vg USING (variable_group_id)
JOIN core.dim_unit u            USING (unit_id);

-- GET /api/outcomes/summary  (resultado por investigación y material)
CREATE OR REPLACE VIEW mart.v_outcome_summary AS
SELECT i.accession,
       i.acronym,
       m.name   AS material,
       o.code   AS outcome,
       o.label  AS outcome_label,
       o.self_extinguished,
       count(*) AS n_test_runs
FROM core.fact_test_run r
JOIN core.dim_investigation i USING (investigation_id)
JOIN core.dim_outcome o       USING (outcome_id)
LEFT JOIN core.dim_material m USING (material_id)
GROUP BY 1, 2, 3, 4, 5, 6;

-- GET /api/envelope  (rango ensayado por grupo de familia: la base de la alarma de "zona sin datos")
CREATE OR REPLACE VIEW mart.v_tested_envelope AS
SELECT fg.code AS family_group,
       fg.name AS family_group_name,
       count(*)                     AS n_test_runs,
       min(r.o2_mole_fraction)      AS o2_min,
       max(r.o2_mole_fraction)      AS o2_max,
       min(r.pressure_atm)          AS pressure_min_atm,
       max(r.pressure_atm)          AS pressure_max_atm,
       min(r.flow_velocity_cm_s)    AS flow_min_cm_s,
       max(r.flow_velocity_cm_s)    AS flow_max_cm_s
FROM core.fact_test_run r
JOIN core.dim_investigation i      USING (investigation_id)
JOIN core.dim_family f             USING (family_id)
JOIN core.dim_family_group fg      USING (family_group_id)
GROUP BY 1, 2;

-- GET /api/files/summary
CREATE OR REPLACE VIEW mart.v_file_summary AS
SELECT i.accession,
       k.code                                   AS file_kind,
       count(*)                                 AS n_files,
       round(sum(ff.bytes) / 1e9, 3)            AS gb,
       count(*) FILTER (WHERE ff.is_useful)     AS n_useful
FROM core.fact_file ff
JOIN core.dim_investigation i USING (investigation_id)
JOIN core.dim_file_kind k     USING (file_kind_id)
GROUP BY 1, 2;

-- GET /api/stats
CREATE OR REPLACE VIEW mart.v_stats AS
SELECT (SELECT count(*) FROM core.dim_investigation)                      AS n_investigations,
       (SELECT count(*) FROM core.fact_test_run)                          AS n_test_runs,
       (SELECT count(*) FROM core.fact_measurement)                       AS n_measurements,
       (SELECT count(*) FROM core.dim_source_table WHERE is_mapped)       AS n_tables_mapped,
       (SELECT count(*) FROM core.dim_source_table)                       AS n_tables_total,
       (SELECT count(*) FROM core.fact_file)                              AS n_files,
       (SELECT count(*) FROM core.fact_file WHERE is_rag_document)        AS n_rag_documents,
       (SELECT round(sum(bytes) / 1e12, 2) FROM core.fact_file)           AS total_tb;
