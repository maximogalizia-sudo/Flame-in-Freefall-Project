"""Ingestion of the catalog CSVs: investigations and the PSI file index."""
from __future__ import annotations

import re

import pandas as pd

from . import config
from .utils import clean_cell, parse_date

LEVELS = {"no": 0, "baja": 1, "media": 2, "media-alta": 3, "alta": 4}


def _read(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=object, keep_default_na=False, encoding="utf-8")
    return df.map(clean_cell).astype(object)


def _split_list(value: str | None, sep: str = ",") -> list[str]:
    """Split lists like "A (X), B (Y)" while respecting parentheses."""
    value = clean_cell(value)
    if not value:
        return []
    parts, depth, buf = [], 0, ""
    for ch in value:
        depth += ch == "("
        depth -= ch == ")"
        if ch == sep and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    parts.append(buf.strip())
    return [p for p in parts if p]


def _parse_formats(value: str | None) -> dict[str, int]:
    """".zip:1184, .txt:993" -> {".zip": 1184, ".txt": 993}"""
    out = {}
    for part in _split_list(value):
        m = re.match(r"(.+?):(\d+)$", part)
        if m:
            out[m.group(1).strip()] = int(m.group(2))
    return out


def ingest_investigations() -> dict[str, pd.DataFrame]:
    raw = _read(config.INVESTIGATIONS_CSV)
    issues: list[dict] = []

    inv = pd.DataFrame({
        "accession": raw["accession"],
        "accession_num": raw["accession"].str.extract(r"PSI-(\d+)")[0].astype("Int64"),
        "acronym": raw["acronym"],
        "title": raw["title"],
        "family": raw["family"],
        "platform": raw["platform"],
        "start_date": raw["start"].map(lambda v: parse_date(v, config.CATALOG_DATE_FORMATS)),
        "end_date": raw["end"].map(lambda v: parse_date(v, config.CATALOG_DATE_FORMATS)),
        "doi": raw["doi_url"].str.replace(r"^https?://doi\.org/", "", regex=True),
        "doi_url": raw["doi_url"],
        "license": raw["license"].replace({"(sin declarar)": None}),
        "page_url": raw["page_url"],
        "n_files": pd.to_numeric(raw["n_files"]).astype("Int64"),
        "total_gb": pd.to_numeric(raw["total_gb"]),
        "n_documents": pd.to_numeric(raw["n_documents"]).astype("Int64"),
        "key_variables": raw["key_variables"],
        "outcome": raw["outcome"],
        "ml_value": raw["ml_value"],
        "ml_value_rank": raw["ml_value"].map(LEVELS).astype("Int64"),
        "rag_value": raw["rag_value"],
        "rag_value_rank": raw["rag_value"].map(LEVELS).astype("Int64"),
        "notes": raw["notes"],
    })

    for col_raw, col in (("start", "start_date"), ("end", "end_date")):
        bad = raw[col_raw].notna() & inv[col].isna()
        for acc, val in zip(raw.loc[bad, "accession"], raw.loc[bad, col_raw]):
            issues.append({"source": "investigations", "key": acc, "issue": f"unparseable date in {col_raw}", "value": val})
    for col in ("ml_value", "rag_value"):
        bad = inv[col].notna() & inv[f"{col}_rank"].isna()
        for acc, val in zip(inv.loc[bad, "accession"], inv.loc[bad, col]):
            issues.append({"source": "investigations", "key": acc, "issue": f"unknown level in {col}", "value": val})
    if inv["accession"].duplicated().any():
        issues.append({"source": "investigations", "key": None, "issue": "duplicated accession", "value": None})

    hardware = [
        {"accession": acc, "hardware": hw}
        for acc, val in zip(raw["accession"], raw["hardware"])
        for hw in _split_list(val)
    ]
    formats = [
        {"accession": acc, "ext": ext, "n_files": n}
        for acc, val in zip(raw["accession"], raw["formats_top"])
        for ext, n in _parse_formats(val).items()
    ]

    return {
        "investigations": inv,
        "investigation_hardware": pd.DataFrame(hardware),
        "investigation_formats": pd.DataFrame(formats),
        "_issues": pd.DataFrame(issues),
    }


def ingest_files() -> dict[str, pd.DataFrame]:
    """Full PSI file index, flagged with the curated lists (useful / document search)."""
    files = _read(config.FILES_INDEX_CSV)
    useful = _read(config.USEFUL_FILES_CSV)
    rag = _read(config.RAG_DOCS_CSV)
    issues: list[dict] = []

    files["bytes"] = pd.to_numeric(files["bytes"], errors="coerce").astype("Int64")
    files["restricted"] = files["restricted"].str.lower().eq("true")
    files["ext"] = files["ext"].str.lower()

    folders = files["file"].isna() | files["s3_key"].str.endswith("/")
    if folders.any():
        issues.append({"source": "files", "key": None, "issue": "folder entries (no file name) dropped", "value": int(folders.sum())})
        files = files[~folders]

    dup = files["s3_key"].duplicated(keep="first")
    if dup.any():
        issues.append({"source": "files", "key": None, "issue": "duplicated s3_key (first kept)", "value": int(dup.sum())})
        files = files[~dup]

    files["is_useful"] = files["s3_key"].isin(set(useful["s3_key"]))
    files["is_rag_document"] = files["s3_key"].isin(set(rag["s3_key"]))
    dup_map = dict(zip(rag["s3_key"], rag["duplicate_of_other_investigation"].str.lower().eq("true")))
    files["duplicate_of_other_investigation"] = files["s3_key"].map(dup_map).astype("boolean")

    for name, sub in (("psi_archivos_utiles", useful), ("rag_documentos_pdf", rag)):
        missing = set(sub["s3_key"]) - set(files["s3_key"])
        if missing:
            issues.append({"source": name, "key": None, "issue": "s3_key missing from the full index", "value": len(missing)})

    files = files.drop(columns=["acronym"])  # resolved through the investigation dimension
    files = files.rename(columns={"file": "file_name"})
    return {"files": files.reset_index(drop=True), "_issues": pd.DataFrame(issues)}
