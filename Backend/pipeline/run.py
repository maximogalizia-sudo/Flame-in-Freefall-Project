"""Ejecuta la ingesta completa: CSV crudos -> parquet en data/staging/.

Uso (desde Backend/):
    .venv/Scripts/python -m pipeline.run
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import pandas as pd

from . import config
from .ingest_catalog import ingest_files, ingest_investigations
from .ingest_tables import export_wide, ingest_tables
from .utils import write_parquet


def check_integrity(out: dict[str, pd.DataFrame]) -> list[dict]:
    """Chequeos de claves foráneas entre los datasets de staging."""
    issues = []
    known = set(out["investigations"]["accession"])
    for name in ("investigation_hardware", "investigation_formats", "files", "experimental_tables"):
        orphan = set(out[name]["accession"]) - known
        if orphan:
            issues.append({"source": name, "key": None, "issue": "accession sin investigación", "value": sorted(orphan)})
    tables = set(out["experimental_tables"]["table_id"])
    for name in ("experimental_columns", "experimental_rows", "experimental_values"):
        orphan = set(out[name]["table_id"]) - tables
        if orphan:
            issues.append({"source": name, "key": None, "issue": "table_id huérfano", "value": sorted(orphan)})
    no_table = set(out["files"].query("kind == 'experimental_table'")["file_name"]) - set(out["experimental_tables"]["file_name"])
    if no_table:
        issues.append({"source": "experimental_tables", "key": None, "issue": "tabla en el índice pero no en disco", "value": sorted(no_table)})
    return issues


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    out: dict[str, pd.DataFrame] = {}
    issues: list[pd.DataFrame] = []
    for step in (ingest_investigations, ingest_files, ingest_tables):
        result = step()
        issues.append(result.pop("_issues"))
        out.update(result)

    integrity = check_integrity(out)
    issues.append(pd.DataFrame(integrity))
    all_issues = pd.concat([i for i in issues if not i.empty], ignore_index=True) if any(not i.empty for i in issues) \
        else pd.DataFrame(columns=["source", "key", "issue", "value"])
    all_issues["value"] = all_issues["value"].astype(str)
    all_issues["key"] = all_issues["key"].astype(str)

    for name, df in out.items():
        write_parquet(df, name, config.STAGING_DIR)
    write_parquet(all_issues, "_ingest_issues", config.STAGING_DIR)
    export_wide(out["experimental_values"], out["experimental_columns"], config.STAGING_TABLES_DIR)

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datasets": {name: int(len(df)) for name, df in out.items()},
        "value_kinds": out["experimental_values"]["kind"].value_counts().to_dict(),
        "row_types": out["experimental_rows"]["row_type"].value_counts().to_dict(),
        "n_issues": int(len(all_issues)),
        "integrity_ok": not integrity,
    }
    (config.STAGING_DIR / "_ingest_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not all_issues.empty:
        print("\nIssues:\n" + all_issues.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
