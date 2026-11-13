"""Helpers compartidos: normalización de texto, fechas y escritura."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏﻿�]")
_SPACES = re.compile(r"\s+")


def clean_cell(value) -> str | None:
    """Normaliza unicode, quita caracteres de control y espacios sobrantes. Vacío -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = unicodedata.normalize("NFKC", str(value))
    s = _CTRL.sub("", s)
    s = _SPACES.sub(" ", s).strip()
    return s or None


def snake(name: str) -> str:
    s = name.replace("µ", "micro").replace("%", "pct")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    return s or "col"


def parse_date(value: str | None, formats: tuple[str, ...]) -> str | None:
    """Devuelve ISO (YYYY-MM-DD) o None."""
    value = clean_cell(value)
    if not value:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def write_parquet(df: pd.DataFrame, name: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    df.to_parquet(path, index=False)
    return path
