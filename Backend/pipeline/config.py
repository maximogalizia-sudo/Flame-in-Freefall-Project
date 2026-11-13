"""Rutas y parámetros del pipeline de ingesta.

Alcance: SOLO los CSV. Los documentos de `descargas/` (PDF, DOCX, PPTX) son
insumo del LLM/RAG y van por otro pipeline; acá no se leen.
"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# --- Fuentes (capa raw: nunca se modifican) ---
SOURCE_ROOT = BACKEND / "psi-combustion"
CATALOG_DIR = SOURCE_ROOT / "catalog"
INVESTIGATIONS_CSV = CATALOG_DIR / "psi_combustion_catalog.csv"
FILES_INDEX_CSV = CATALOG_DIR / "psi_files_index_completo.csv.gz"
USEFUL_FILES_CSV = CATALOG_DIR / "psi_archivos_utiles.csv"
RAG_DOCS_CSV = CATALOG_DIR / "rag_documentos_pdf.csv"
TABLES_DIR = SOURCE_ROOT / "tablas_experimentales"

# --- Salidas ---
DATA_DIR = BACKEND / "data"
STAGING_DIR = DATA_DIR / "staging"           # parquet limpios, listos para modelar y cargar
STAGING_TABLES_DIR = STAGING_DIR / "tablas"  # cada tabla experimental limpia en CSV ancho (para revisar a ojo)

# Formato de fechas del catálogo PSI (MM-DD-YYYY)
CATALOG_DATE_FORMATS = ("%m-%d-%Y",)
