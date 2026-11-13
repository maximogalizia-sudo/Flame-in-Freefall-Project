"""Ingestion of the 22 PSI experimental tables (L0 -> L1 staging).

The tables share no schema and are messy: units inside cells ("30 cm/s"), ranges
("1.5 - 5"), lists ("0.17; 0.21"), approximations ("~ 21.5"), special markers
("RAMP", "OR", "#VALUE!") and footnote rows mixed with data.

This stage does NOT harmonize variables across investigations and never deletes,
imputes or corrects (R1, R2). It types every cell and keeps it traceable to its
file, row and column:

  experimental_tables   one row per file
  experimental_columns  one row per column (raw name, clean name, unit, profile)
  experimental_rows     one row per CSV row (row_type: data / sparse / note)
  experimental_values   long format: one row per non-empty cell, parsed
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

from . import config
from .utils import clean_cell, snake

# Known header fixes per investigation: (regex on the raw header, replacement).
HEADER_FIXES: dict[str, list[tuple[str, str]]] = {
    # FLEX lost its subscripts/superscripts on export: O=O2, N=N2, CO=CO2, and the
    # burning rate (droplet K constant) is in mm2/s, not mm.
    "PSI-69": [
        (r"^O initial", "O2 initial"),
        (r"^N initial", "N2 initial"),
        (r"^CO initial", "CO2 initial"),
        (r"^Burning rate; mm$", "Burning rate; mm2/s"),
    ],
    # SAFFIRE-II: "µ" became "?", "1-g" starts with a digit, and a typo.
    "PSI-99": [(r"^\?-g", "micro-g"), (r"^1-g", "normal-g"), (r"^Samle", "Sample")],
}

UNITS = [
    "cm/s", "mm/s", "mg/s", "mg/min", "g/s", "mm2/s", "sccm", "slpm", "mmHg", "kPa", "atm", "bar", "psi",
    "cm", "mm", "µm", "um", "m", "sec", "s", "min", "ms", "W", "kW", "K", "°C", "C", "%", "kV", "V", "ppm",
]
UNIT_ALIASES = {
    "sec": "s", "secs": "s", "seconds": "s", "minutes": "min", "percent": "%", "um": "µm",
    "C": "°C", "mole fraction": "mol/mol",
}
KNOWN_UNITS = set(UNITS) | set(UNIT_ALIASES) | set(UNIT_ALIASES.values())

_UNIT_RE = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
RE_NUMBER = re.compile(rf"^(?P<num>{_NUM})\s*(?P<unit>{_UNIT_RE})?$")
RE_RANGE = re.compile(
    rf"^(?P<a>{_NUM})\s*(?P<ua>{_UNIT_RE})?(?P<sep>\s*(?:-|–|to)\s*)(?P<b>{_NUM})\s*(?P<ub>{_UNIT_RE})?$"
)
RE_APPROX = re.compile(r"^(?:~|≈|approx\.?|about|ca\.)\s*", re.I)
RE_CENSORED = re.compile(rf"^(?P<op>[<>]=?)\s*(?P<num>{_NUM})\s*(?P<unit>{_UNIT_RE})?$")
RE_HEADER_PAREN = re.compile(r"\(([^()]+)\)\s*$")
RE_HEADER_SEMI = re.compile(r";\s*([^;]+)$")

# R4: special values are not plain nulls. Each maps to a flag.
MISSING_TOKENS = {"", "-", "--", "–", "—", "n/a", "na", "none", "null", "?"}
EXCEL_ERRORS = {"#value!", "#n/a", "#div/0!", "#ref!", "#num!", "#name?", "#null!"}
RAMPED_TOKENS = {"ramp", "ramped"}
# "OR" in BASS-II CO columns = over range (> 1000 ppm, per datos/CSV.md §4.3).
CENSORED_TOKENS = {"or": (">", 1000.0)}


def norm_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    unit = re.sub(r"\s+", " ", unit).strip()
    return UNIT_ALIASES.get(unit, UNIT_ALIASES.get(unit.lower(), unit))


# ---------------------------------------------------------------- reading
def read_raw(path: Path) -> tuple[list[list[str]], str]:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        text, enc = data.decode("latin-1"), "latin-1"
    rows = list(csv.reader(io.StringIO(text)))
    return rows, ("utf-8" if enc == "utf-8-sig" else enc)


# ---------------------------------------------------------------- headers
def parse_header(raw: str, accession: str) -> tuple[str, str | None, str | None]:
    """(label, unit, note). 'mfuel (mg/s)' -> ('mfuel', 'mg/s', None); 'XO2 (init)' -> ('XO2 init', None, None)."""
    label = clean_cell(raw) or ""
    for pat, rep in HEADER_FIXES.get(accession, []):
        label = re.sub(pat, rep, label)
    label = label.rstrip("*").strip()
    unit = note = None

    m = RE_HEADER_PAREN.search(label)
    if m:
        inner = re.sub(r"\s+", " ", m.group(1)).strip()
        base = label[: m.start()].strip()
        if inner.lower().startswith("note") or re.fullmatch(r"[\d\s.,]+", inner):
            label, note = base, inner             # "(Note 1)", "(1000 250 50 10)"
        elif inner in KNOWN_UNITS or inner.lower() in KNOWN_UNITS:
            label, unit = base, inner
        else:
            label = f"{base} {inner}"              # "(init)", "(Fuel Type)" belong to the name
    else:
        m = RE_HEADER_SEMI.search(label)
        if m:
            unit = m.group(1).strip()
            label = label[: m.start()].strip()
    return label, norm_unit(unit), note


# ---------------------------------------------------------------- cells
def parse_value(raw: str | None) -> dict:
    """Type one cell. kind: number | range | list | text | special | null.

    `flag` follows R4: approx, censored, ramped, excel_error, encoding_loss, missing.
    """
    out = {"value_raw": raw, "kind": "null", "value_num": None, "value_min": None, "value_max": None,
           "value_unit": None, "is_approx": False, "flag": None, "censored": None, "censor_limit": None}
    if raw is not None and "�" in str(raw):
        out.update(kind="special", flag="encoding_loss")
        return out
    s = clean_cell(raw)
    if s is None or s.lower() in MISSING_TOKENS:
        out["flag"] = "missing"
        return out
    low = s.lower()
    if low in EXCEL_ERRORS:
        out.update(kind="special", flag="excel_error")
        return out
    if low in RAMPED_TOKENS:
        out.update(kind="special", flag="ramped")
        return out
    if low in CENSORED_TOKENS:
        op, limit = CENSORED_TOKENS[low]
        out.update(kind="special", flag="censored", censored=op, censor_limit=limit)
        return out
    m = RE_CENSORED.match(s)
    if m:
        out.update(kind="special", flag="censored", censored=m["op"][0], censor_limit=float(m["num"]),
                   value_unit=m["unit"])
        return out

    approx = bool(RE_APPROX.match(s))
    if approx:
        s = RE_APPROX.sub("", s)
        out["flag"] = "approx"

    m = RE_NUMBER.match(s)
    if m:
        out.update(kind="number", value_num=float(m["num"]), value_unit=m["unit"], is_approx=approx)
        return out

    m = RE_RANGE.match(s)
    # A dash with no spaces and no units ("2-1") is an ID, not a range. With spaces,
    # "to" or units ("30%-40%", "22.1 to 22.0") it is a range.
    if m and (m["sep"] != m["sep"].strip() or "to" in m["sep"] or m["ua"] or m["ub"]):
        a, b = float(m["a"]), float(m["b"])
        out.update(kind="range", value_min=min(a, b), value_max=max(a, b),
                   value_unit=m["ub"] or m["ua"], is_approx=approx)
        return out

    if ";" in s:
        items = [re.sub(r"^or\s+", "", p.strip()) for p in s.split(";")]
        parsed = [RE_NUMBER.match(p) for p in items if p]
        if parsed and all(parsed):
            nums = [float(p["num"]) for p in parsed]
            units = {p["unit"] for p in parsed if p["unit"]}
            out.update(kind="list", value_min=min(nums), value_max=max(nums),
                       value_unit=units.pop() if len(units) == 1 else None, is_approx=approx)
            return out

    out["kind"] = "text"
    return out


# ---------------------------------------------------------------- rows
def classify_row(cells: list[str | None], note_cols: set[int]) -> str:
    """data | sparse (continuation of the previous row) | note (legend/footnote) | empty."""
    filled = [(i, c) for i, c in enumerate(cells) if c]
    if not filled:
        return "empty"
    first = cells[0] or ""
    if re.match(r"^(note\b|\*)", first, re.I):
        return "note"
    if len(cells) >= 4 and len(filled) <= 2:
        long_free_text = any(len(c) > 25 and i not in note_cols for i, c in filled)
        if long_free_text and first and not RE_NUMBER.match(first):
            return "note"      # "F1C1/F1C2 | Pre-test flow visualization data for sample 1"
        return "sparse"        # "5 | Kapton: same conditions as 1-4", ",,,,100%,,"
    return "data"


# ---------------------------------------------------------------- table
def ingest_table(path: Path) -> dict[str, list[dict]]:
    m = re.match(r"(PSI-\d+)_Experimental table_(.+)\.csv$", path.name)
    if not m:
        raise ValueError(f"Unexpected table file name: {path.name}")
    accession, table_name = m.group(1), m.group(2).strip()
    table_id = f"{accession}::{snake(table_name)}"

    rows, encoding = read_raw(path)
    header, body = rows[0], rows[1:]
    n_cols = len(header)

    columns, seen = [], {}
    for i, raw in enumerate(header):
        label, unit, note = parse_header(raw, accession)
        name = snake(label) if label else f"col_{i}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}_{seen[name]}"
        columns.append({"table_id": table_id, "col_idx": i, "column_raw": raw, "column_label": label,
                        "column_name": name, "header_unit": unit, "header_note": note})

    note_cols = {c["col_idx"] for c in columns if re.search(r"note|comment", c["column_name"])}
    out_rows, values, issues = [], [], []
    for r_idx, raw_row in enumerate(body, start=1):
        if len(raw_row) > n_cols and any(clean_cell(c) for c in raw_row[n_cols:]):
            issues.append({"source": path.name, "key": r_idx, "issue": "row has more cells than the header",
                           "value": len(raw_row)})
        padded = (raw_row + [""] * n_cols)[:n_cols]
        cells = [clean_cell(c) for c in padded]
        row_type = classify_row(cells, note_cols)
        if row_type == "empty":
            continue
        out_rows.append({"table_id": table_id, "row_idx": r_idx, "row_type": row_type,
                         "note_text": " | ".join(c for c in cells if c) if row_type == "note" else None})
        if row_type == "note":
            continue
        for col, raw_val in zip(columns, padded):
            v = parse_value(raw_val)
            if v["kind"] == "null":
                continue
            v["unit"] = norm_unit(v.pop("value_unit")) or col["header_unit"]
            values.append({"table_id": table_id, "row_idx": r_idx, "column_name": col["column_name"], **v})

    table = {"table_id": table_id, "accession": accession, "table_name": table_name, "file_name": path.name,
             "encoding": encoding, "n_columns": n_cols,
             "n_data_rows": sum(r["row_type"] in ("data", "sparse") for r in out_rows),
             "n_note_rows": sum(r["row_type"] == "note" for r in out_rows)}
    return {"tables": [table], "columns": columns, "rows": out_rows, "values": values, "issues": issues}


def _profile_columns(columns: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """Dominant type of each column (helps decide measure vs attribute when modeling)."""
    kinds = ["number", "range", "list", "text", "special"]
    prof = values.groupby(["table_id", "column_name"])["kind"].value_counts().unstack(fill_value=0)
    prof = prof.reindex(columns=kinds, fill_value=0)
    prof["n_values"] = prof[kinds].sum(axis=1)
    prof["numeric_ratio"] = ((prof["number"] + prof["range"] + prof["list"]) /
                             (prof["n_values"] - prof["special"]).clip(lower=1)).round(3)
    prof["inferred_type"] = pd.cut(prof["numeric_ratio"], [-0.01, 0.2, 0.8, 1.0],
                                   labels=["categorical", "mixed", "numeric"]).astype(str)
    prof = prof.rename(columns={k: f"n_{k}" for k in kinds}).reset_index()
    prof.columns.name = None
    return columns.merge(prof, on=["table_id", "column_name"], how="left")


def ingest_tables() -> dict[str, pd.DataFrame]:
    acc = {"tables": [], "columns": [], "rows": [], "values": [], "issues": []}
    for path in sorted(config.TABLES_DIR.glob("*.csv")):
        part = ingest_table(path)
        for k in acc:
            acc[k].extend(part[k])

    values = pd.DataFrame(acc["values"])
    return {
        "experimental_tables": pd.DataFrame(acc["tables"]),
        "experimental_columns": _profile_columns(pd.DataFrame(acc["columns"]), values),
        "experimental_rows": pd.DataFrame(acc["rows"]),
        "experimental_values": values,
        "_issues": pd.DataFrame(acc["issues"]),
    }


def export_wide(values: pd.DataFrame, columns: pd.DataFrame, out_dir: Path) -> None:
    """Write each cleaned table in wide format (normalized raw value) for manual review."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for table_id, grp in values.groupby("table_id"):
        order = columns.loc[columns["table_id"] == table_id].sort_values("col_idx")["column_name"].tolist()
        wide = grp.pivot(index="row_idx", columns="column_name", values="value_raw").reindex(columns=order)
        wide.to_csv(out_dir / f"{table_id.replace('::', '__')}.csv", encoding="utf-8")
