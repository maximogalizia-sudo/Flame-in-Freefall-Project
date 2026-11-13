-- =====================================================================
-- Flame in Freefall · mart schema (what the API serves)
-- Flat, star-like views over core. Each view backs one API endpoint.
-- =====================================================================
DROP SCHEMA IF EXISTS mart CASCADE;
CREATE SCHEMA mart;

-- GET /investigations
CREATE VIEW mart.v_investigations AS
SELECT i.investigation_id,
       i.accession                   AS psi_id,
       i.acronym,
       i.title,
       f.name                        AS family,
       fg.code                       AS family_group,
       fg.name                       AS family_group_name,
       p.name                        AS platform,
       i.start_date,
       i.end_date,
       i.doi,
       i.doi_url,
       i.license,
       i.page_url,
       i.ml_value_rank,
       i.rag_value_rank,
       COALESCE(h.hardware, '{}')    AS hardware,
       COALESCE(t.n_tests, 0)        AS n_tests,
       COALESCE(t.n_with_outcome, 0) AS n_tests_with_outcome,
       COALESCE(lg.n_log_entries, 0) AS n_log_entries,
       COALESCE(fl.n_files, 0)       AS n_files,
       COALESCE(fl.n_documents, 0)   AS n_documents,
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
    SELECT count(*) AS n_tests,
           count(*) FILTER (WHERE o.code NOT IN ('not_reported', 'not_applicable')) AS n_with_outcome
    FROM core.fact_test_run r JOIN core.dim_outcome o USING (outcome_id)
    WHERE r.investigation_id = i.investigation_id AND r.row_type = 'test'
) t ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS n_log_entries FROM core.fact_log_entry e WHERE e.investigation_id = i.investigation_id
) lg ON true
LEFT JOIN LATERAL (
    SELECT count(*)                                   AS n_files,
           count(*) FILTER (WHERE ff.is_rag_document) AS n_documents,
           round(sum(ff.bytes) / 1e9, 2)              AS gb
    FROM core.fact_file ff WHERE ff.investigation_id = i.investigation_id
) fl ON true;

-- GET /tests
CREATE VIEW mart.v_tests AS
SELECT r.test_id,
       i.accession            AS psi_id,
       i.acronym,
       fg.code                AS family_group,
       st.table_name          AS source_table,
       st.file_name           AS source_file,
       r.source_row,
       st.owner,
       r.row_type,
       r.is_valid,
       r.invalid_reason,
       r.test_code,
       d.full_date            AS test_date,
       m.code                 AS material_code,
       m.name                 AS material,
       r.material_raw,
       r.sample_geometry,
       fc.code                AS fuel_class,
       fc.phase               AS fuel_phase,
       o.code                 AS outcome,
       o.label                AS outcome_label,
       o.self_extinguished,
       r.outcome_method,
       r.outcome_status,
       r.flame_type,
       r.flow_direction,
       r.fuel_dilution,
       r.o2_frac,
       r.pressure_kpa,
       r.flow_cm_s,
       r.burn_time_s,
       r.cool_flame,
       r.comment,
       r.attributes,
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

-- GET /tests/{test_id}/measurements
CREATE VIEW mart.v_measurements AS
SELECT r.test_id,
       v.code                   AS variable,
       v.label                  AS variable_label,
       vg.code                  AS variable_group,
       u.code                   AS unit,
       v.measured_or_calculated,
       ms.value_num,
       ms.value_min,
       ms.value_max,
       ms.value_text,
       ms.is_approx,
       ms.flag,
       ms.censored,
       ms.censor_limit,
       ms.source_column,
       ms.source_raw
FROM core.fact_measurement ms
JOIN core.fact_test_run r       USING (test_run_id)
JOIN core.dim_variable v        USING (variable_id)
JOIN core.dim_variable_group vg USING (variable_group_id)
JOIN core.dim_unit u            USING (unit_id);

-- GET /tests/{test_id}/steps
CREATE VIEW mart.v_test_steps AS
SELECT r.test_id, s.step_order, s.flow_cm_s, s.note, s.raw
FROM core.test_step s JOIN core.fact_test_run r USING (test_run_id);

-- GET /log-entries  (operations days, design matrices, sessions)
CREATE VIEW mart.v_log_entries AS
SELECT e.log_entry_id,
       i.accession        AS psi_id,
       i.acronym,
       fg.code            AS family_group,
       st.table_name      AS source_table,
       e.source_row,
       e.row_type,
       e.entry_type,
       d.full_date        AS entry_date,
       e.label,
       m.name             AS material,
       e.material_raw,
       e.fuel_dilution,
       e.n_flames_ignited,
       e.attributes,
       e.comment,
       e.quality_flags,
       i.doi_url
FROM core.fact_log_entry e
JOIN core.dim_investigation i      USING (investigation_id)
JOIN core.dim_source_table st      USING (source_table_id)
LEFT JOIN core.dim_family f        ON f.family_id = i.family_id
LEFT JOIN core.dim_family_group fg ON fg.family_group_id = f.family_group_id
LEFT JOIN core.dim_material m      USING (material_id)
LEFT JOIN core.dim_date d          ON d.date_key = e.entry_date_key;

-- GET /envelope  (tested range per family group: basis of the "no data here" warning).
-- Only valid tests count.
CREATE VIEW mart.v_tested_envelope AS
SELECT fg.code                   AS family_group,
       fg.name                   AS family_group_name,
       count(*)                  AS n_tests,
       min(r.o2_frac)            AS o2_frac_min,
       max(r.o2_frac)            AS o2_frac_max,
       min(r.pressure_kpa)       AS pressure_kpa_min,
       max(r.pressure_kpa)       AS pressure_kpa_max,
       min(r.flow_cm_s)          AS flow_cm_s_min,
       max(r.flow_cm_s)          AS flow_cm_s_max
FROM core.fact_test_run r
JOIN core.dim_investigation i USING (investigation_id)
JOIN core.dim_family f        USING (family_id)
JOIN core.dim_family_group fg USING (family_group_id)
WHERE r.is_valid AND r.row_type = 'test'
GROUP BY 1, 2;

-- GET /outcomes/summary
CREATE VIEW mart.v_outcome_summary AS
SELECT i.accession AS psi_id, i.acronym, m.name AS material,
       o.code AS outcome, o.label AS outcome_label, o.self_extinguished,
       r.outcome_status, count(*) AS n_tests
FROM core.fact_test_run r
JOIN core.dim_investigation i USING (investigation_id)
JOIN core.dim_outcome o       USING (outcome_id)
LEFT JOIN core.dim_material m USING (material_id)
WHERE r.row_type = 'test'
GROUP BY 1, 2, 3, 4, 5, 6, 7;

-- GET /files/summary
CREATE VIEW mart.v_file_summary AS
SELECT i.accession AS psi_id, k.code AS file_kind, count(*) AS n_files,
       round(sum(ff.bytes) / 1e9, 3) AS gb, count(*) FILTER (WHERE ff.is_useful) AS n_useful
FROM core.fact_file ff
JOIN core.dim_investigation i USING (investigation_id)
JOIN core.dim_file_kind k     USING (file_kind_id)
GROUP BY 1, 2;

-- GET /data-dictionary  (R17)
CREATE VIEW mart.v_data_dictionary AS
SELECT v.code AS variable, v.label, vg.code AS variable_group, u.code AS unit,
       v.measured_or_calculated, v.is_core, v.description,
       (SELECT string_agg(DISTINCT st.table_key || '.' || mc.column_name, ', ' ORDER BY st.table_key || '.' || mc.column_name)
        FROM core.map_source_column mc JOIN core.dim_source_table st USING (source_table_id)
        WHERE mc.variable_id = v.variable_id) AS source_columns
FROM core.dim_variable v
JOIN core.dim_variable_group vg USING (variable_group_id)
JOIN core.dim_unit u            USING (unit_id);

-- GET /stats
CREATE VIEW mart.v_stats AS
SELECT (SELECT count(*) FROM core.dim_investigation)                                AS n_investigations,
       (SELECT count(*) FROM core.fact_test_run WHERE row_type = 'test')            AS n_tests,
       (SELECT count(*) FROM core.fact_test_run r JOIN core.dim_outcome o USING (outcome_id)
         WHERE r.row_type = 'test' AND o.code NOT IN ('not_reported', 'not_applicable')) AS n_tests_with_outcome,
       (SELECT count(*) FROM core.fact_test_run WHERE NOT is_valid)                 AS n_tests_invalid,
       (SELECT count(*) FROM core.fact_measurement)                                 AS n_measurements,
       (SELECT count(*) FROM core.fact_log_entry)                                   AS n_log_entries,
       (SELECT count(*) FROM core.dim_source_table WHERE is_mapped)                 AS n_tables_mapped,
       (SELECT count(*) FROM core.dim_source_table)                                  AS n_tables_total,
       (SELECT count(*) FROM core.data_decision WHERE status = 'proposed')          AS n_decisions_pending,
       (SELECT count(*) FROM core.fact_file)                                        AS n_files,
       (SELECT count(*) FROM core.fact_file WHERE is_rag_document)                  AS n_documents,
       (SELECT round(sum(bytes) / 1e12, 2) FROM core.fact_file)                     AS total_tb;
