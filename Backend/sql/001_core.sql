-- =====================================================================
-- Flame in Freefall · esquema core
-- Constelación de hechos (fact_test_run, fact_measurement, fact_file)
-- con dimensiones en copo de nieve donde hay jerarquías reales.
-- Idempotente: se puede correr varias veces.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS core;
SET search_path = core, public;

-- ---------------------------------------------------------------- dimensiones: investigación
CREATE TABLE IF NOT EXISTS dim_family_group (
    family_group_id smallint PRIMARY KEY,
    code            text NOT NULL UNIQUE,
    name            text NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_family (
    family_id       smallint PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    family_group_id smallint NOT NULL REFERENCES dim_family_group
);

CREATE TABLE IF NOT EXISTS dim_platform (
    platform_id smallint PRIMARY KEY,
    name        text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_investigation (
    investigation_id int PRIMARY KEY,               -- = número de PSI (PSI-69 -> 69)
    accession        text NOT NULL UNIQUE,
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

CREATE TABLE IF NOT EXISTS dim_hardware (
    hardware_id smallint PRIMARY KEY,
    name        text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS bridge_investigation_hardware (
    investigation_id int      NOT NULL REFERENCES dim_investigation ON DELETE CASCADE,
    hardware_id      smallint NOT NULL REFERENCES dim_hardware,
    PRIMARY KEY (investigation_id, hardware_id)
);

-- ---------------------------------------------------------------- dimensiones: material / combustible
CREATE TABLE IF NOT EXISTS dim_fuel_class (
    fuel_class_id smallint PRIMARY KEY,
    code          text NOT NULL UNIQUE,
    phase         text NOT NULL CHECK (phase IN ('gas', 'liquid', 'solid'))
);

CREATE TABLE IF NOT EXISTS dim_material (
    material_id   smallint PRIMARY KEY,
    code          text NOT NULL UNIQUE,
    name          text NOT NULL,
    fuel_class_id smallint NOT NULL REFERENCES dim_fuel_class
);

-- ---------------------------------------------------------------- dimensiones: variable
CREATE TABLE IF NOT EXISTS dim_variable_group (
    variable_group_id smallint PRIMARY KEY,
    code              text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_unit (
    unit_id smallint PRIMARY KEY,
    code    text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_variable (
    variable_id       smallint PRIMARY KEY,
    code              text NOT NULL UNIQUE,
    label             text NOT NULL,
    variable_group_id smallint NOT NULL REFERENCES dim_variable_group,
    unit_id           smallint NOT NULL REFERENCES dim_unit,     -- unidad canónica
    is_core           boolean NOT NULL DEFAULT false,            -- también vive como columna en fact_test_run
    description       text
);

-- ---------------------------------------------------------------- dimensiones: resultado, fecha, linaje
CREATE TABLE IF NOT EXISTS dim_outcome (
    outcome_id        smallint PRIMARY KEY,
    code              text NOT NULL UNIQUE,
    label             text NOT NULL,
    self_extinguished boolean          -- true = la llama se apagó sola; NULL = no aplica / no se sabe
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key  int PRIMARY KEY,         -- YYYYMMDD
    full_date date NOT NULL UNIQUE,
    year      smallint NOT NULL,
    quarter   smallint NOT NULL,
    month     smallint NOT NULL,
    day       smallint NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_source_table (
    source_table_id  smallint PRIMARY KEY,
    table_key        text NOT NULL UNIQUE,       -- ej. 'PSI-69::flex'
    investigation_id int NOT NULL REFERENCES dim_investigation,
    table_name       text NOT NULL,
    file_name        text NOT NULL,
    encoding         text,
    n_data_rows      int,
    is_mapped        boolean NOT NULL DEFAULT false   -- ¿sus filas entran en fact_test_run?
);

CREATE TABLE IF NOT EXISTS dim_file_kind (
    file_kind_id smallint PRIMARY KEY,
    code         text NOT NULL UNIQUE
);

-- ---------------------------------------------------------------- mapeo (metadatos versionados)
CREATE TABLE IF NOT EXISTS map_source_column (
    source_table_id smallint NOT NULL REFERENCES dim_source_table,
    column_name     text NOT NULL,
    role            text NOT NULL,
    variable_id     smallint REFERENCES dim_variable,
    source_unit     text,
    notes           text,
    PRIMARY KEY (source_table_id, column_name)
);

-- ---------------------------------------------------------------- hechos
-- Grano: 1 ensayo (una fila de una tabla experimental mapeada).
CREATE TABLE IF NOT EXISTS fact_test_run (
    test_run_id        int PRIMARY KEY,
    investigation_id   int      NOT NULL REFERENCES dim_investigation,
    source_table_id    smallint NOT NULL REFERENCES dim_source_table,
    source_row         int      NOT NULL,
    test_code          text,
    test_date_key      int      REFERENCES dim_date,
    material_id        smallint REFERENCES dim_material,
    outcome_id         smallint NOT NULL REFERENCES dim_outcome,
    flame_type         text CHECK (flame_type IN ('normal', 'inverse')),
    flow_direction     text CHECK (flow_direction IN ('concurrent', 'opposed')),
    fuel_dilution      numeric(6, 4),      -- fracción de combustible en la botella (ej. 30 % butano)
    o2_mole_fraction   numeric(8, 5),
    pressure_atm       numeric(8, 4),
    flow_velocity_cm_s numeric(8, 3),
    burn_time_s        numeric(10, 2),
    cool_flame         text,
    flame_color        text,
    comment            text,
    quality_flags      text[] NOT NULL DEFAULT '{}',
    UNIQUE (source_table_id, source_row)
);
CREATE INDEX IF NOT EXISTS ix_test_run_inv      ON fact_test_run (investigation_id);
CREATE INDEX IF NOT EXISTS ix_test_run_outcome  ON fact_test_run (outcome_id);
CREATE INDEX IF NOT EXISTS ix_test_run_material ON fact_test_run (material_id);

-- Grano: 1 variable de 1 ensayo, en la unidad canónica de dim_variable.
CREATE TABLE IF NOT EXISTS fact_measurement (
    measurement_id bigint PRIMARY KEY,
    test_run_id    int      NOT NULL REFERENCES fact_test_run ON DELETE CASCADE,
    variable_id    smallint NOT NULL REFERENCES dim_variable,
    value_num      double precision,     -- número, o punto medio si es rango/lista
    value_min      double precision,
    value_max      double precision,
    value_text     text,                 -- valor original cuando no es numérico ("Insignificant")
    is_approx      boolean NOT NULL DEFAULT false,
    source_column  text NOT NULL,
    source_raw     text,
    UNIQUE (test_run_id, variable_id)
);
CREATE INDEX IF NOT EXISTS ix_measurement_var ON fact_measurement (variable_id);

-- Grano: 1 archivo publicado en PSI.
CREATE TABLE IF NOT EXISTS fact_file (
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
CREATE INDEX IF NOT EXISTS ix_file_inv ON fact_file (investigation_id);
