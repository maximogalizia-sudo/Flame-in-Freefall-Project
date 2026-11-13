"""Paths and settings for the data pipeline.

Scope: CSV files only. The documents in `descargas/` (PDF, DOCX, PPTX) feed the
semantic-search service and go through a separate pipeline; they are not read here.
"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# --- Sources (L0: never modified) ---
SOURCE_ROOT = BACKEND / "psi-combustion"
CATALOG_DIR = SOURCE_ROOT / "catalog"
INVESTIGATIONS_CSV = CATALOG_DIR / "psi_combustion_catalog.csv"
FILES_INDEX_CSV = CATALOG_DIR / "psi_files_index_completo.csv.gz"
USEFUL_FILES_CSV = CATALOG_DIR / "psi_archivos_utiles.csv"
RAG_DOCS_CSV = CATALOG_DIR / "rag_documentos_pdf.csv"
TABLES_DIR = SOURCE_ROOT / "tablas_experimentales"

# --- Outputs ---
DATA_DIR = BACKEND / "data"
STAGING_DIR = DATA_DIR / "staging"           # L1: typed, lossless Parquet
STAGING_TABLES_DIR = STAGING_DIR / "tables"  # each table cleaned, wide CSV, for manual review
PROCESSED_DIR = DATA_DIR / "processed"       # L2: Parquet exchanged between the two ETLs (§7.4)

# PSI catalog date format (MM-DD-YYYY)
CATALOG_DATE_FORMATS = ("%m-%d-%Y",)
