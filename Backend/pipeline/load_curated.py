"""Load LC's curated tables (repo-root data/processed/) into Postgres, AS IS (docs/PLAN_ASISTENTE.md, M2).

    mart.flex_tests             <- data/processed/flex_tests.parquet        (FLEX, PSI-69)
    mart.cfig_tests             <- data/processed/cfig_tests.parquet        (CFI-G, PSI-159)
    mart.bass_tests             <- data/processed/bass_tests.parquet        (BASS PSI-26 / BASS-II PSI-25)
    mart.bass_material_summary  <- data/processed/bass_material_summary.csv

The files already carry every data decision (DD-010 to DD-033): nothing is renamed, filtered or recomputed here.
Each table is dropped and recreated, so re-running after LC regenerates the files is safe.

Run (from Backend/, uses DATABASE_URL):  .venv/Scripts/python -m pipeline.load_curated
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from psycopg2 import sql

_HERE = Path(__file__).resolve()
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE.parents[2])]   # Backend/ (api) and repo root (flame) when run locally
from api.settings import get_settings  # noqa: E402
from flame.models import PROC  # noqa: E402  - the same directory the models read (repo root locally, /app in Docker)
TABLES = {
    "flex_tests": "flex_tests.parquet",
    "cfig_tests": "cfig_tests.parquet",
    "bass_tests": "bass_tests.parquet",
    "bass_material_summary": "bass_material_summary.csv",
}
PRIMARY_KEYS = {"flex_tests": "flex_id", "cfig_tests": "test_id", "bass_tests": "test_id"}


def _pg_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "bigint"
    if pd.api.types.is_float_dtype(s):
        return "double precision"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "timestamp"
    return "text"


def _py(v):
    if v is None or (not isinstance(v, (str, bytes)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v.item() if hasattr(v, "item") else v


def read(file_name: str) -> pd.DataFrame:
    path = PROC / file_name
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def load_table(cur, name: str, df: pd.DataFrame) -> None:
    ident = sql.Identifier("mart", name)
    cols = [sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(_pg_type(df[c]))) for c in df.columns]
    if pk := PRIMARY_KEYS.get(name):
        cols.append(sql.SQL("PRIMARY KEY ({})").format(sql.Identifier(pk)))
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(ident))
    cur.execute(sql.SQL("CREATE TABLE {} ({})").format(ident, sql.SQL(", ").join(cols)))
    insert = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
        ident, sql.SQL(", ").join(map(sql.Identifier, df.columns))).as_string(cur)
    rows = [tuple(_py(v) for v in r) for r in df.itertuples(index=False, name=None)]
    psycopg2.extras.execute_values(cur, insert, rows, page_size=500)
    cur.execute(sql.SQL("COMMENT ON TABLE {} IS %s").format(ident),
                (f"LC curated table, loaded as is from data/processed/{TABLES[name]}",))


def main() -> None:
    url = get_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS mart")
        for name, file_name in TABLES.items():
            df = read(file_name)
            load_table(cur, name, df)
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("mart", name)))
            n = cur.fetchone()[0]
            assert n == len(df), f"{name}: loaded {n} rows, file has {len(df)}"
            print(f"mart.{name}: {n} rows, {len(df.columns)} columns")


if __name__ == "__main__":
    main()
