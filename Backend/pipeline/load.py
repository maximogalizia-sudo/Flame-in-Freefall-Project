"""Carga: tablas core -> data/core/seed.sql y (opcional) a Postgres.

Uso (desde Backend/):
    .venv/Scripts/python -m pipeline.load                 # solo genera data/core/seed.sql
    .venv/Scripts/python -m pipeline.load --apply         # además aplica sql/*.sql + seed en DATABASE_URL

DATABASE_URL se lee del entorno o de ../.env (postgresql://user:pass@host:5432/db).
La recarga es completa (DELETE + INSERT en una transacción). Se usa DELETE y no
TRUNCATE ... CASCADE a propósito: si el esquema rag ya tiene filas que apuntan a
core, la carga falla en vez de borrar el RAG en cascada.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import config
from .transform import build_core

SQL_DIR = config.BACKEND / "sql"
CORE_OUT = config.DATA_DIR / "core"

# Orden de carga (respeta las claves foráneas). El borrado va en orden inverso.
LOAD_ORDER = [
    "dim_family_group", "dim_family", "dim_platform", "dim_investigation",
    "dim_hardware", "bridge_investigation_hardware",
    "dim_fuel_class", "dim_material", "dim_variable_group", "dim_unit", "dim_variable",
    "dim_outcome", "dim_date", "dim_source_table", "dim_file_kind", "map_source_column",
    "fact_test_run", "fact_measurement", "fact_file",
]
BATCH = 500


def sql_literal(v) -> str:
    if v is None or v is pd.NA or v is pd.NaT:
        return "NULL"
    if isinstance(v, float) and math.isnan(v):
        return "NULL"
    if isinstance(v, (bool,)) or type(v).__name__ == "bool_":
        return "true" if v else "false"
    if isinstance(v, (int, float)) or type(v).__module__ == "numpy":
        return repr(v.item() if hasattr(v, "item") else v)
    if isinstance(v, (date, datetime)):
        return f"'{v.isoformat()}'"
    if isinstance(v, (list, tuple)) or type(v).__name__ == "ndarray":
        return "ARRAY[" + ",".join(sql_literal(x) for x in v) + "]::text[]" if len(v) else "'{}'::text[]"
    return "'" + str(v).replace("'", "''") + "'"


def to_sql(tables: dict[str, pd.DataFrame]) -> str:
    out = ["-- Generado por pipeline.load. No editar a mano.", "BEGIN;", "SET search_path = core, public;"]
    out += [f"DELETE FROM {t};" for t in reversed(LOAD_ORDER)]
    for name in LOAD_ORDER:
        df = tables[name].astype(object).where(tables[name].notna(), None)
        cols = ", ".join(df.columns)
        records = df.to_dict("records")
        for i in range(0, len(records), BATCH):
            chunk = records[i:i + BATCH]
            rows = ",\n  ".join("(" + ", ".join(sql_literal(r[c]) for c in df.columns) + ")" for r in chunk)
            out.append(f"INSERT INTO {name} ({cols}) VALUES\n  {rows};")
    out.append("COMMIT;")
    return "\n".join(out) + "\n"


def _database_url() -> str | None:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = config.BACKEND.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def apply(seed_sql: str) -> None:
    import psycopg2  # solo hace falta para --apply

    url = _database_url()
    if not url:
        raise SystemExit("Falta DATABASE_URL (variable de entorno o .env en la raíz del repo).")
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        for f in sorted(SQL_DIR.glob("*.sql")):
            print(f"aplicando {f.name}")
            cur.execute(f.read_text(encoding="utf-8"))
        conn.commit()
        print("cargando seed")
        conn.autocommit = True   # el seed maneja su propia transacción
        cur.execute(seed_sql)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="aplicar DDL + seed en DATABASE_URL")
    a = ap.parse_args()

    tables = build_core()
    issues = tables.pop("_issues")
    CORE_OUT.mkdir(parents=True, exist_ok=True)
    seed = to_sql(tables)
    (CORE_OUT / "seed.sql").write_text(seed, encoding="utf-8", newline="\n")

    runs = tables["fact_test_run"].merge(tables["dim_outcome"], on="outcome_id")
    report = {
        "rows": {k: int(len(v)) for k, v in tables.items()},
        "outcomes": runs["code"].value_counts().to_dict(),
        "seed_mb": round(len(seed.encode()) / 1e6, 2),
        "issues": issues.to_dict("records"),
    }
    (CORE_OUT / "_load_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if a.apply:
        apply(seed)
        print("listo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
