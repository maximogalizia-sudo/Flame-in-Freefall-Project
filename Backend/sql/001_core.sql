-- =====================================================================
-- Flame in Freefall · core schema (L2, curated)
-- Fact constellation (fact_test_run, fact_measurement, test_step,
-- fact_log_entry, fact_file) with snowflaked dimensions where real
-- hierarchies exist.
--
-- core is fully derived from L0 (the raw PSI files), so it is rebuilt on
-- every load. Nothing outside core may reference it (the RAG service has
-- its own database and links by accession / doi only).
-- =====================================================================
DROP SCHEMA IF EXISTS core CASCADE;
CREATE SCHEMA core;
SET search_path = core, public;

-- ---------------------------------------------------------------- dimensions: investigation
CREATE TABLE dim_family_group (
    family_group_id smallint PRIMARY KEY,
    code            text NOT NULL UNIQUE,
    name            text NOT NULL
);

CREATE TABLE dim_family (
    family_id       smallint PRIMARY KEY,
    name            text NOT NULL,
    name_source     text NOT NULL UNIQUE,      -- family as written in the PSI catalog
    family_group_id smallint NOT NULL REFERENCES dim_family_group
);

CREATE TABLE dim_platform (
    platform_id smallint PRIMARY KEY,
    name        text NOT NULL UNIQUE
);

CREATE TABLE dim_investigation (
    investigation_id int PRIMARY KEY,               -- PSI number (PSI-69 -> 69)
    accession        text NOT NULL UNIQUE,          -- psi_id
    acronym          text,
    title            text NOT NULL,
    family_id        smallint REFERENCES dim_family,
    platform_id      smallint REFERENCES dim_platform,
    start_date       date,
    end_date         date,
    doi              text,
    doi_url          text,
    license          text,
    page_url         text,
    n_files          int,
    total_gb         numeric(12, 2),
    n_documents      int,
    key_variables    text,
    outcome_summary  text,
    ml_value         text,
    ml_value_rank    smallint CHECK (ml_value_rank BETWEEN 0 AND 4),
    rag_value        text,
    rag_value_rank   smallint CHECK (rag_value_rank BETWEEN 0 AND 4),
    notes            text
);

CREATE TABLE dim_hardware (
    hardware_id smallint PRIMARY KEY,
    name        text NOT NULL UNIQUE
);

CREATE TABLE bridge_investigation_hardware (
    investigation_id int      NOT NULL REFERENCES dim_investigation ON DELETE CASCADE,
    hardware_id      smallint NOT NULL REFERENCES dim_hardware,
    PRIMARY KEY (investigation_id, hardware_id)
);

-- ---------------------------------------------------------------- dimensions: material / fuel
CREATE TABLE dim_fuel_class (
    fuel_class_id smallint PRIMARY KEY,
    code          text NOT NULL UNIQUE,
    phase         text NOT NULL CHECK (phase IN ('gas', 'liquid', 'solid'))
);

CREATE TABLE dim_material (
    material_id   smallint PRIMARY KEY,
    code          text NOT NULL UNIQUE,
    name          text NOT NULL,
    fuel_class_id smallint NOT NULL REFERENCES dim_fuel_class
);

-- ---------------------------------------------------------------- dimensions: variable
CREATE TABLE dim_variable_group (
    variable_group_id smallint PRIMARY KEY,
    code              text NOT NULL UNIQUE
);

CREATE TABLE dim_unit (
    unit_id smallint PRIMARY KEY,
    code    text NOT NULL UNIQUE
);

CREATE TABLE dim_variable (
    variable_id            smallint PRIMARY KEY,
    code                   text NOT NULL UNIQUE,    -- canonical name with unit suffix (R5)
    label                  text NOT NULL,
    variable_group_id      smallint NOT NULL REFERENCES dim_variable_group,
    unit_id                smallint NOT NULL REFERENCES dim_unit,
    is_core                boolean NOT NULL DEFAULT false,   -- also a column of fact_test_run
    measured_or_calculated text NOT NULL CHECK (measured_or_calculated IN ('measured', 'calculated', 'derived')),  -- R13
    description            text
);

-- ---------------------------------------------------------------- dimensions: outcome, date, lineage
CREATE TABLE dim_outcome (
    outcome_id        smallint PRIMARY KEY,
    code              text NOT NULL UNIQUE,
    label             text NOT NULL,
    self_extinguished boolean          -- true = the flame went out on its own; NULL = n/a or unknown
);

CREATE TABLE dim_date (
    date_key  int PRIMARY KEY,         -- YYYYMMDD
    full_date date NOT NULL UNIQUE,
    year      smallint NOT NULL,
    quarter   smallint NOT NULL,
    month     smallint NOT NULL,
    day       smallint NOT NULL
);

CREATE TABLE dim_source_table (
    source_table_id  smallint PRIMARY KEY,
    table_key        text NOT NULL UNIQUE,       -- e.g. 'PSI-69::flex'
    investigation_id int NOT NULL REFERENCES dim_investigation,
    table_name       text NOT NULL,
    file_name        text NOT NULL,
    encoding         text,
    n_data_rows      int,
    owner            text CHECK (owner IN ('LC', 'MG')),   -- who owns the ETL of this table (§9)
    target_fact      text CHECK (target_fact IN ('test_run', 'log_entry')),
    outcome_method   text,
    is_mapped        boolean NOT NULL DEFAULT false,
    notes            text
);

CREATE TABLE dim_file_kind (
    file_kind_id smallint PRIMARY KEY,
    code         text NOT NULL UNIQUE
);

-- ---------------------------------------------------------------- mapping and decisions (versioned metadata)
CREATE TABLE map_source_column (
    source_table_id smallint NOT NULL REFERENCES dim_source_table,
    column_name     text NOT NULL,
    role            text NOT NULL,
    variable_id     smallint REFERENCES dim_variable,
    target          text,            -- attribute name when the column is not a measure
    source_unit     text,
    notes           text,
    PRIMARY KEY (source_table_id, column_name)
);

-- Data decisions (docs/DECISIONES_DATOS.md). Only 'approved' ones change values.
CREATE TABLE data_decision (
    decision_id text PRIMARY KEY,
    status      text NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    table_key   text,
    summary     text NOT NULL
);

-- ---------------------------------------------------------------- facts
-- Grain: one test (one row of a mapped test table).
CREATE TABLE fact_test_run (
    test_run_id      int PRIMARY KEY,
    test_id          text NOT NULL UNIQUE,     -- R9: '<psi_id>:<source id>'
    investigation_id int      NOT NULL REFERENCES dim_investigation,
    source_table_id  smallint NOT NULL REFERENCES dim_source_table,
    source_row       int      NOT NULL,
    row_type         text NOT NULL CHECK (row_type IN ('test', 'setup', 'calibration', 'cleaning', 'summary')),  -- R1
    is_valid         boolean NOT NULL DEFAULT true,
    invalid_reason   text,
    test_code        text,
    test_date_key    int      REFERENCES dim_date,
    material_id      smallint REFERENCES dim_material,
    material_raw     text,
    sample_geometry  text,                     -- rod / sphere / film / slab / candle / fabric
    outcome_id       smallint NOT NULL REFERENCES dim_outcome,
    outcome_method   text NOT NULL,            -- source_column / rule_two_columns / rule_operator_notes / ...
    outcome_status   text CHECK (outcome_status IN ('provisional', 'validated')),  -- validated by the model owner (LC)
    flame_type       text CHECK (flame_type IN ('normal', 'inverse')),
    flow_direction   text CHECK (flow_direction IN ('concurrent', 'opposed')),
    fuel_dilution    numeric(6, 4),            -- fuel fraction in the bottle (e.g. 30 % butane)
    o2_frac          numeric(8, 5),
    pressure_kpa     numeric(9, 3),
    flow_cm_s        numeric(8, 3),            -- only when the test had a single flow (R6)
    burn_time_s      numeric(10, 2),
    cool_flame       text,
    flame_color      text,
    comment          text,                     -- free text, verbatim (R7)
    attributes       jsonb,                    -- table-specific attributes (PI, fiber, restrictor...)
    quality_flags    text[] NOT NULL DEFAULT '{}',
    UNIQUE (source_table_id, source_row)
);
CREATE INDEX ix_test_run_inv      ON fact_test_run (investigation_id);
CREATE INDEX ix_test_run_outcome  ON fact_test_run (outcome_id);
CREATE INDEX ix_test_run_material ON fact_test_run (material_id);

-- Grain: one variable of one test, in the canonical unit of dim_variable.
CREATE TABLE fact_measurement (
    measurement_id bigint PRIMARY KEY,
    test_run_id    int      NOT NULL REFERENCES fact_test_run ON DELETE CASCADE,
    variable_id    smallint NOT NULL REFERENCES dim_variable,
    value_num      double precision,     -- number, or midpoint of a range/list
    value_min      double precision,
    value_max      double precision,
    value_text     text,                 -- original value when it is not numeric ("Insignificant", "RAMP")
    is_approx      boolean NOT NULL DEFAULT false,
    flag           text CHECK (flag IN ('approx', 'censored', 'ramped', 'excel_error', 'encoding_loss',
                                        'sensor_error', 'derived',
                                        'corrected', 'imputed', 'unit_suspect')),   -- R4; the last three come from approved decisions
    censored       text CHECK (censored IN ('>', '<')),
    censor_limit   double precision,
    source_column  text NOT NULL,
    source_raw     text,                 -- R3
    UNIQUE (test_run_id, variable_id)
);
CREATE INDEX ix_measurement_var ON fact_measurement (variable_id);

-- Grain: one step of a condition that changed during a test (R6), e.g. "5 3 2 1 ext".
CREATE TABLE test_step (
    test_run_id int      NOT NULL REFERENCES fact_test_run ON DELETE CASCADE,
    step_order  smallint NOT NULL,
    flow_cm_s   numeric(8, 3),
    note        text,
    raw         text NOT NULL,
    PRIMARY KEY (test_run_id, step_order)
);

-- Grain: one row of a table that is NOT per test: operations day (ACME logs, SLICE),
-- design-matrix row (FLEX-2), sampling session (DAFT) or dataset index entry (PSI-142).
CREATE TABLE fact_log_entry (
    log_entry_id     int PRIMARY KEY,
    investigation_id int      NOT NULL REFERENCES dim_investigation,
    source_table_id  smallint NOT NULL REFERENCES dim_source_table,
    source_row       int      NOT NULL,
    row_type         text NOT NULL CHECK (row_type IN ('log', 'continuation', 'note')),
    parent_row       int,                      -- for row_type 'note': source_row of the day it belongs to (DD-007)
    entry_type       text NOT NULL CHECK (entry_type IN ('operation_day', 'test_matrix', 'sampling_session', 'dataset_index')),
    entry_date_key   int      REFERENCES dim_date,
    label            text,
    material_id      smallint REFERENCES dim_material,
    material_raw     text,
    fuel_dilution    numeric(6, 4),
    n_flames_ignited int,
    attributes       jsonb,
    comment          text,
    quality_flags    text[] NOT NULL DEFAULT '{}',
    UNIQUE (source_table_id, source_row)
);
CREATE INDEX ix_log_entry_inv ON fact_log_entry (investigation_id);

-- Grain: one file published in PSI.
CREATE TABLE fact_file (
    file_id                          int PRIMARY KEY,
    investigation_id                 int      NOT NULL REFERENCES dim_investigation,
    file_kind_id                     smallint NOT NULL REFERENCES dim_file_kind,
    category                         text,
    subcategory                      text,
    file_name                        text NOT NULL,
    ext                              text,
    bytes                            bigint,
    restricted                       boolean NOT NULL DEFAULT false,
    s3_key                           text NOT NULL UNIQUE,
    is_useful                        boolean NOT NULL DEFAULT false,
    is_rag_document                  boolean NOT NULL DEFAULT false,
    duplicate_of_other_investigation boolean
);
CREATE INDEX ix_file_inv ON fact_file (investigation_id);
