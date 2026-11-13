"""Transform: data/staging/*.parquet (L1) -> core tables (L2) as DataFrames.

Every judgment call lives in mappings/*.csv, not in code:
  tables.csv          table owner (LC/MG), target fact, date rule, outcome rule
  source_columns.csv  role of each column (and target variable for measures)
  variables.csv       shared variable catalog with canonical unit (R5) and measured/calculated (R13)
  materials.csv       ordered regex rules that normalize materials and fuels
  families.csv        PSI families grouped for the dashboard
  decisions.csv       data decisions (DD-Pxx) and their status: proposed / approved / rejected
  overrides.csv       per-cell actions (corrections gated by a decision, or R12 invalid marks)
  value_fixes.csv     per-column regex fixes, gated by a decision

Rules from docs/DOCUMENTO_TECNICO.md §6 that this module enforces:
  * nothing is deleted or imputed; corrections apply only when their decision is approved,
    otherwise the row is flagged `pending:<decision>` (R2, D8);
  * known errors are marked is_valid=False and the value is kept (R12);
  * special values carry flags (R4); flow sequences go to test_steps (R6);
  * stable test_id "<psi_id>:<source id>" (R9).

Column roles (combine with '+', e.g. "material+comment"):
  test_code, material, measure, sequence, attribute, comment, outcome,
  flame_type, flow_direction, log_label, n_flames, ignore
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import config
from .ingest_tables import norm_unit, parse_value
from .utils import snake

MAPPINGS = Path(__file__).resolve().parent / "mappings"

# ---------------------------------------------------------------- units
# (from, to) -> (factor, offset):  canonical = value * factor + offset
CONVERSIONS = {
    ("mmHg", "kPa"): (0.133322, 0), ("atm", "kPa"): (101.325, 0), ("bar", "kPa"): (100.0, 0),
    ("psi", "kPa"): (6.89476, 0), ("%", "mol/mol"): (0.01, 0), ("°C", "K"): (1, 273.15),
    ("cm", "mm"): (10, 0), ("m", "mm"): (1000, 0), ("µm", "mm"): (0.001, 0),
    ("min", "s"): (60, 0), ("ms", "s"): (0.001, 0), ("cm/s", "mm/s"): (10, 0),
}

# ---------------------------------------------------------------- outcomes
OUTCOMES = [
    # code,             label,                                     self_extinguished
    ("extinction",      "Self-extinguished",                       True),
    ("no_propagation",  "Did not spread",                          True),
    ("blowoff",         "Blown off by high flow",                  True),
    ("complete",        "Burned to completion",                    False),
    ("flow_terminated", "Still burning when the flow was cut",     False),
    ("partial",         "Partial spread",                          None),
    ("no_ignition",     "Did not ignite",                          None),
    ("flash",           "Flash, no sustained flame",               None),
    ("disruption",      "Droplet disruption",                      None),
    ("not_reported",    "No outcome in the table",                 None),
    ("not_applicable",  "Not applicable (not a flame test)",       None),
]


def _outcome_flex(raw: dict) -> str:
    return {"extinction": "extinction", "completion": "complete", "disruption": "disruption"}.get(
        (raw.get("test_end") or "").lower(), "not_reported")


def _outcome_cfi_g(raw: dict) -> str:
    ign = (raw.get("hot_ignition") or "").strip().lower()
    term = (raw.get("self_ext_or_flow_term") or "").strip().upper()
    if ign == "no":
        return "no_ignition"
    if ign == "flash":
        return "flash"
    if ign == "yes":
        return {"SE": "extinction", "FT": "flow_terminated"}.get(term, "not_reported")
    return "not_reported"


def _outcome_saffire_2(raw: dict) -> str:
    """From the burned length in microgravity (samples are 29 cm long)."""
    s = (raw.get("micro_g_burn_length") or "").lower()
    if s.startswith("~ 0") or s == "0":
        return "no_propagation"
    if s.startswith("29"):
        return "complete"
    return "partial" if s else "not_reported"


def _outcome_bass_ii(raw: dict) -> str:
    """Inferred from operator notes: the flow was lowered until the flame went out."""
    s = f"{raw.get('air_display') or ''} {raw.get('fan_display') or ''}".lower()
    if "no ign" in s or "would not stay lit" in s:
        return "no_ignition"
    if "blowoff" in s:
        return "blowoff"
    if re.search(r"\bext\b|extinguish|\d\s*ext\b|\bout\b", s):
        return "extinction"
    return "not_reported"


OUTCOME_RULES = {
    "flex": _outcome_flex, "cfi_g": _outcome_cfi_g, "saffire_2": _outcome_saffire_2,
    "bass_ii_notes": _outcome_bass_ii, "not_applicable": lambda raw: "not_applicable",
}


# ---------------------------------------------------------------- derived values (gated by a decision)
def _spice_burn_time(raw: dict) -> dict:
    """Burn time = flame-off minus ignition (GMT clock times of the same day)."""
    def parse(t):
        return datetime.strptime((t or "").replace(".", ":"), "%H:%M:%S")
    try:
        secs = (parse(raw.get("gmt_flame_off")) - parse(raw.get("gmt_ignition"))).total_seconds()
    except ValueError:
        return {}
    return {"burn_time_s": secs} if secs > 0 else {}


DERIVED = {"PSI-107::spice": ("DD-P04", _spice_burn_time)}

# Values that cannot be negative: a negative value is kept but flagged as a sensor error (R12).
NON_NEGATIVE = {"flow_cm_s", "pressure_kpa", "burn_time_s", "o2_frac", "fuel_mass_flow_mg_s",
                "fuel_mix_mass_flow_mg_s", "fuel_volume_flow_sccm", "o2_mass_flow_mg_s", "n2_mass_flow_mg_s"}
CORE_COLUMNS = {"o2_frac", "pressure_kpa", "flow_cm_s", "burn_time_s"}

# ---------------------------------------------------------------- dates
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_date(rule: str, raw: dict) -> date | None:
    if not rule:
        return None
    kind, _, cols = rule.partition(":")
    v = raw.get(cols) if cols and "/" not in cols else None
    try:
        if kind == "flex_date":
            s = raw.get("test_date") or ""
            for fmt in ("%m/%d/%Y", "%m/%d/%y"):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
        elif kind == "yyddd" and v:
            m = re.match(r"^(\d{2})(\d{3})", v.replace(" ", ""))
            if m and 1 <= int(m[2]) <= 366:
                return date(2000 + int(m[1]), 1, 1) + timedelta(days=int(m[2]) - 1)
        elif kind == "mdy" and v:
            return datetime.strptime(v, "%m/%d/%Y").date()
        elif kind == "dmy" and v:
            return datetime.strptime(v, "%d-%b-%y").date()
        elif kind == "date_prefix" and v:
            m = re.match(r"^(\d{4})-([A-Za-z]{3})-(\d{2})", v)
            if m:
                return date(int(m[1]), MONTHS[m[2].lower()], int(m[3]))
        elif kind == "gmt_year" and v:
            m = re.match(r"GMT\s*(\d+)\s*\((\d{4})\)", v)
            if m:
                return date(int(m[2]), 1, 1) + timedelta(days=int(m[1]) - 1)
        elif kind == "ymd_cols":
            y, mo, d = (raw.get(c) for c in cols.split("/"))
            mo = MONTHS.get((mo or "")[:3].lower()) if not (mo or "").isdigit() else int(mo)
            return date(int(float(y)), mo, int(float(d)))
    except (ValueError, TypeError, KeyError):
        return None
    return None


# ---------------------------------------------------------------- sequences (R6)
RE_PAREN = re.compile(r"\([^)]*\)")
RE_POT = re.compile(r"\bpots?\b\s*(?:[<>~]?\s*\.?\d+\.?\d*\s*)+", re.I)
RE_STEP = re.compile(r"(?P<lt><\s*)?(?P<num>\d+\.?\d*|\.\d+)")
EVENTS = [("no_ignition", r"no ign|would not stay lit"), ("blowoff", r"blowoff|blow off"),
          ("extinction", r"\bext\b|extinguish|\d\s*ext\b|\bout\b")]


def parse_sequence(raw: str | None) -> tuple[list[dict], str | None]:
    """'5 3 2 1 decr' -> steps [5, 3, 2, 1]; potentiometer readings and parentheses are left out.

    Returns (steps, event) where event is extinction / blowoff / no_ignition / None.
    """
    if not raw:
        return [], None
    low = raw.lower()
    event = next((e for e, rx in EVENTS if re.search(rx, low)), None)
    clean = RE_POT.sub(" ", RE_PAREN.sub(" ", low))
    steps = []
    for i, m in enumerate(RE_STEP.finditer(clean), start=1):
        lt = bool(m["lt"])
        steps.append({"step_order": i, "flow_cm_s": None if lt else float(m["num"]),
                      "note": f"<{m['num']}" if lt else None})
    return steps, event


# ---------------------------------------------------------------- materials
class MaterialMatcher:
    """Ordered regex rules: the first match wins."""

    GEOMETRY = [("rod", "rod"), ("spher", "sphere"), ("film|micron", "film"),
                ("slab|thick|sided", "slab"), ("candle", "candle"), ("sibal|nomex|fabric|lampwick", "fabric")]

    def __init__(self, rules: pd.DataFrame):
        self.rules = [(re.compile(r["pattern"], re.I), r) for _, r in rules.iterrows()]

    def match(self, raw: str | None) -> dict | None:
        if not raw:
            return None
        for rx, r in self.rules:
            if rx.search(raw):
                m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%", raw)
                dilution = float(m[1]) / 100 if m and r["fuel_class"].startswith("gaseous") else None
                geometry = None
                if r["fuel_class"].startswith("solid"):
                    geometry = next((g for p, g in self.GEOMETRY if re.search(p, raw, re.I)), None)
                return {"code": r["code"], "dilution": dilution if dilution != 1.0 else None, "geometry": geometry}
        return None


# ---------------------------------------------------------------- helpers
RE_LEADING_NUM = re.compile(r"^(?P<approx>~\s*)?(?P<num>\d+\.?\d*|\.\d+)\s*(?P<unit>[a-zA-Z/%°]+)?\s*(\(note \d\))?$", re.I)


def _convert(value, unit_from, unit_to):
    if value is None or pd.isna(value):
        return None
    if not unit_from or unit_from == unit_to:
        return float(value)
    if (unit_from, unit_to) not in CONVERSIONS:
        raise ValueError(f"No conversion {unit_from} -> {unit_to}")
    factor, offset = CONVERSIONS[(unit_from, unit_to)]
    return float(value) * factor + offset


def _attr_value(raw: str) -> object:
    """Attribute value for jsonb: number, range, list or text."""
    v = parse_value(raw)
    unit = norm_unit(v["value_unit"])
    if v["kind"] == "number":
        return v["value_num"] if not unit else {"value": v["value_num"], "unit": unit}
    if v["kind"] in ("range", "list"):
        out = {"min": v["value_min"], "max": v["value_max"], "raw": raw}
        if unit:
            out["unit"] = unit
        return out
    return raw


def _load_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(MAPPINGS / name, dtype=str, keep_default_na=False)


def approved_decisions() -> set[str]:
    d = _load_csv("decisions.csv")
    return set(d.loc[d["status"].str.lower() == "approved", "decision_id"])


# ---------------------------------------------------------------- dimensions
def build_dimensions(st: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    inv = st["investigations"]
    fam_map = _load_csv("families.csv")
    missing = set(inv["family"].dropna()) - set(fam_map["family"])
    if missing:
        raise ValueError(f"Families without a group in families.csv: {missing}")

    groups = fam_map.drop_duplicates("group_code")[["group_code", "group_name"]].reset_index(drop=True)
    groups.insert(0, "family_group_id", range(1, len(groups) + 1))
    families = fam_map.merge(groups, on=["group_code", "group_name"]).sort_values("family").reset_index(drop=True)
    families.insert(0, "family_id", range(1, len(families) + 1))

    platforms = pd.DataFrame({"name": sorted(inv["platform"].dropna().unique())})
    platforms.insert(0, "platform_id", range(1, len(platforms) + 1))

    d_inv = pd.DataFrame({
        "investigation_id": inv["accession_num"].astype(int),
        "accession": inv["accession"], "acronym": inv["acronym"], "title": inv["title"],
        "family_id": inv["family"].map(dict(zip(families["family"], families["family_id"]))).astype("Int64"),
        "platform_id": inv["platform"].map(dict(zip(platforms["name"], platforms["platform_id"]))).astype("Int64"),
        "start_date": inv["start_date"], "end_date": inv["end_date"],
        "doi": inv["doi"], "doi_url": inv["doi_url"], "license": inv["license"], "page_url": inv["page_url"],
        "n_files": inv["n_files"], "total_gb": inv["total_gb"], "n_documents": inv["n_documents"],
        "key_variables": inv["key_variables"], "outcome_summary": inv["outcome"],
        "ml_value": inv["ml_value"], "ml_value_rank": inv["ml_value_rank"],
        "rag_value": inv["rag_value"], "rag_value_rank": inv["rag_value_rank"], "notes": inv["notes"],
    })
    acc_to_id = dict(zip(d_inv["accession"], d_inv["investigation_id"]))

    hw = st["investigation_hardware"]
    hardware = pd.DataFrame({"name": sorted(hw["hardware"].unique())})
    hardware.insert(0, "hardware_id", range(1, len(hardware) + 1))
    bridge = pd.DataFrame({
        "investigation_id": hw["accession"].map(acc_to_id),
        "hardware_id": hw["hardware"].map(dict(zip(hardware["name"], hardware["hardware_id"]))),
    }).drop_duplicates()

    mats = _load_csv("materials.csv")
    phase = {"liquid": "liquid", "gaseous": "gas", "solid": "solid"}
    classes = pd.DataFrame({"code": sorted(mats["fuel_class"].unique())})
    classes["phase"] = classes["code"].map(lambda c: phase[c.split("_")[0]])
    classes.insert(0, "fuel_class_id", range(1, len(classes) + 1))
    materials = mats.drop_duplicates("code")[["code", "name", "fuel_class"]].sort_values("code").reset_index(drop=True)
    materials.insert(0, "material_id", range(1, len(materials) + 1))
    materials["fuel_class_id"] = materials.pop("fuel_class").map(dict(zip(classes["code"], classes["fuel_class_id"])))

    var = _load_csv("variables.csv")
    vgroups = pd.DataFrame({"code": sorted(var["group"].unique())})
    vgroups.insert(0, "variable_group_id", range(1, len(vgroups) + 1))
    units = pd.DataFrame({"code": sorted(var["unit"].unique())})
    units.insert(0, "unit_id", range(1, len(units) + 1))
    variables = pd.DataFrame({
        "variable_id": range(1, len(var) + 1), "code": var["code"], "label": var["label"],
        "variable_group_id": var["group"].map(dict(zip(vgroups["code"], vgroups["variable_group_id"]))),
        "unit_id": var["unit"].map(dict(zip(units["code"], units["unit_id"]))),
        "is_core": var["is_core"].str.lower().eq("true"),
        "measured_or_calculated": var["measured_or_calculated"], "description": var["description"],
    })

    outcomes = pd.DataFrame(OUTCOMES, columns=["code", "label", "self_extinguished"])
    outcomes.insert(0, "outcome_id", range(1, len(outcomes) + 1))
    outcomes["self_extinguished"] = outcomes["self_extinguished"].astype("boolean")

    tcfg = _load_csv("tables.csv").set_index("table_id")
    t = st["experimental_tables"].sort_values("table_id").reset_index(drop=True)
    source_tables = pd.DataFrame({
        "source_table_id": range(1, len(t) + 1), "table_key": t["table_id"],
        "investigation_id": t["accession"].map(acc_to_id), "table_name": t["table_name"],
        "file_name": t["file_name"], "encoding": t["encoding"], "n_data_rows": t["n_data_rows"],
        "owner": t["table_id"].map(lambda k: tcfg["owner"].get(k) or None),
        "target_fact": t["table_id"].map(lambda k: tcfg["fact"].get(k) or None),
        "outcome_method": t["table_id"].map(lambda k: tcfg["outcome_method"].get(k) or None),
        "notes": t["table_id"].map(lambda k: tcfg["notes"].get(k) or None),
    })
    source_tables["is_mapped"] = source_tables["target_fact"].notna()

    kinds = pd.DataFrame({"code": sorted(st["files"]["kind"].dropna().unique())})
    kinds.insert(0, "file_kind_id", range(1, len(kinds) + 1))

    mapping = _load_csv("source_columns.csv")
    st_ids = dict(zip(source_tables["table_key"], source_tables["source_table_id"]))
    var_ids = dict(zip(variables["code"], variables["variable_id"]))
    is_measure = mapping["role"].str.contains(r"\bmeasure\b")
    map_df = pd.DataFrame({
        "source_table_id": mapping["table_id"].map(st_ids), "column_name": mapping["column_name"],
        "role": mapping["role"],
        "variable_id": mapping["target"].where(is_measure).map(var_ids).astype("Int64"),
        "target": mapping["target"].where(~is_measure & (mapping["target"] != "")).replace("", None),
        "source_unit": mapping["source_unit"].replace("", None), "notes": mapping["notes"].replace("", None),
    })
    if map_df["source_table_id"].isna().any():
        raise ValueError("source_columns.csv references unknown tables")
    bad = mapping[is_measure & ~mapping["target"].isin(var_ids)]
    if not bad.empty:
        raise ValueError(f"Unknown variables in source_columns.csv: {bad['target'].tolist()}")

    decisions = _load_csv("decisions.csv")

    return {
        "dim_family_group": groups.rename(columns={"group_code": "code", "group_name": "name"}),
        "dim_family": families[["family_id", "family_en", "family", "family_group_id"]]
            .rename(columns={"family_en": "name", "family": "name_source"}),
        "dim_platform": platforms, "dim_investigation": d_inv,
        "dim_hardware": hardware, "bridge_investigation_hardware": bridge,
        "dim_fuel_class": classes, "dim_material": materials,
        "dim_variable_group": vgroups, "dim_unit": units, "dim_variable": variables,
        "dim_outcome": outcomes, "dim_source_table": source_tables, "dim_file_kind": kinds,
        "map_source_column": map_df,
        "data_decision": decisions.rename(columns={"table_id": "table_key"}),
    }


# ---------------------------------------------------------------- rows of one table
def _iter_rows(table_key, values, rows_meta, overrides, fixes, approved):
    """Yield (row_idx, row_type, cells, raw, flags, marks) applying approved fixes only.

    `marks` collects R12 invalid marks: {column: reason}.
    """
    tv = values[values["table_id"] == table_key]
    meta = rows_meta[(rows_meta["table_id"] == table_key) & (rows_meta["row_type"] != "note")]
    t_over = overrides[overrides["table_id"] == table_key]
    t_fix = fixes[fixes["table_id"] == table_key]
    prev_raw: dict = {}
    for row_idx, row_type in sorted(zip(meta["row_idx"], meta["row_type"])):
        cells = {r["column_name"]: dict(r) for r in tv[tv["row_idx"] == row_idx].to_dict("records")}
        flags: list[str] = []
        marks: dict[str, str] = {}
        for _, f in t_fix.iterrows():
            c = cells.get(f["column_name"])
            if c and re.search(f["pattern"], c["value_raw"]):
                if f["decision_id"] in approved:
                    new = re.sub(f["pattern"], f["replacement"], c["value_raw"]).strip()
                    cells[f["column_name"]] = {"value_raw": new, **_reparse(new)}
                    flags.append(f"fixed:{f['decision_id']}")
                else:
                    flags.append(f"pending:{f['decision_id']}")
        for _, o in t_over[t_over["row_idx"].astype(int) == row_idx].iterrows():
            col, action, dec = o["column_name"], o["action"], o["decision_id"]
            if action == "invalid":
                marks[col] = o["reason"]
            elif action == "row_type":
                row_type = o["new_value"]
            elif dec and dec not in approved:
                flags.append(f"pending:{dec}")
            elif action == "replace":
                cells[col] = {"value_raw": o["new_value"], **_reparse(o["new_value"])}
                flags.append(f"fixed:{dec}")
            elif action == "fill_down" and prev_raw.get(col):
                cells[col] = {"value_raw": prev_raw[col], "kind": "text"}
                flags.append(f"fixed:{dec}")
        raw = {c: v["value_raw"] for c, v in cells.items()}
        prev_raw = raw
        yield int(row_idx), row_type, cells, raw, sorted(set(flags)), marks


def _reparse(raw: str) -> dict:
    v = parse_value(raw)
    v["unit"] = norm_unit(v.pop("value_unit"))
    v.pop("value_raw")
    return v


def _roles(mapping: pd.DataFrame) -> dict[str, list[tuple[str, str, str, str]]]:
    """column -> [(role, modifier, target, source_unit)]"""
    out: dict[str, list] = {}
    for _, m in mapping.iterrows():
        for part in m["role"].split("+"):
            role, _, mod = part.partition(":")
            out.setdefault(m["column_name"], []).append((role, mod, m["target"], m["source_unit"]))
    return out


def _cols_with(roles, role):
    return [c for c, rs in roles.items() if any(r[0] == role for r in rs)]


def _measure(c: dict, src_unit: str, unit_to: str, issues, table_key, row_idx, col) -> dict | None:
    """Convert one cell to the canonical unit. Returns None if it cannot be converted."""
    unit_from = c.get("unit") if isinstance(c.get("unit"), str) else (src_unit or None)
    kind, is_approx = c.get("kind"), bool(c.get("is_approx"))
    vnum, vmin, vmax, vtext = c.get("value_num"), c.get("value_min"), c.get("value_max"), None
    flag, censored, limit = c.get("flag"), c.get("censored"), c.get("censor_limit")
    if flag == "missing":
        flag = None
    if kind == "text":
        vtext = c["value_raw"]
        m = RE_LEADING_NUM.match(c["value_raw"])
        if m:
            vnum, is_approx, unit_from = float(m["num"]), bool(m["approx"]), (m["unit"] or unit_from)
            flag = flag or ("approx" if is_approx else None)
    elif kind == "special":
        vtext = c["value_raw"]
    if unit_from not in (None, unit_to) and (unit_from, unit_to) not in CONVERSIONS:
        unit_from = src_unit or None   # stray unit in free text that does not apply: use the mapping's
    if unit_from == "mol/mol" and unit_to == "mol/mol" and vnum is not None and vnum > 1:
        unit_from = "%"
    try:
        vnum, vmin, vmax, limit = (_convert(x, unit_from, unit_to) for x in (vnum, vmin, vmax, limit))
    except ValueError as e:
        issues.append({"source": table_key, "key": row_idx, "issue": str(e), "value": col})
        return None
    midpoint = vnum is None and vmin is not None and vmax is not None
    if midpoint:
        vnum = (vmin + vmax) / 2
    return {"value_num": vnum, "value_min": vmin, "value_max": vmax, "value_text": vtext,
            "is_approx": is_approx, "flag": flag, "censored": censored, "censor_limit": limit,
            "midpoint": midpoint}


# ---------------------------------------------------------------- facts
def build_facts(st: dict[str, pd.DataFrame], dims: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    mapping = _load_csv("source_columns.csv")
    tcfg = _load_csv("tables.csv").set_index("table_id")
    overrides, fixes = _load_csv("overrides.csv"), _load_csv("value_fixes.csv")
    approved = approved_decisions()
    matcher = MaterialMatcher(_load_csv("materials.csv"))
    var_csv = _load_csv("variables.csv")
    canon_unit = dict(zip(var_csv["code"], var_csv["unit"]))

    st_ids = dict(zip(dims["dim_source_table"]["table_key"], dims["dim_source_table"]["source_table_id"]))
    inv_ids = dict(zip(dims["dim_source_table"]["table_key"], dims["dim_source_table"]["investigation_id"]))
    var_ids = dict(zip(dims["dim_variable"]["code"], dims["dim_variable"]["variable_id"]))
    out_ids = dict(zip(dims["dim_outcome"]["code"], dims["dim_outcome"]["outcome_id"]))
    mat_ids = dict(zip(dims["dim_material"]["code"], dims["dim_material"]["material_id"]))

    values, rows_meta = st["experimental_values"], st["experimental_rows"]
    runs, measurements, steps, logs, issues = [], [], [], [], []

    def add_measurement(test_run_id, var, col, raw, m):
        measurements.append({"measurement_id": len(measurements) + 1, "test_run_id": test_run_id,
                             "variable_id": var_ids[var], "source_column": col, "source_raw": raw, **m})

    for table_key, tmap in mapping.groupby("table_id", sort=True):
        cfg = tcfg.loc[table_key]
        accession = table_key.split("::")[0]
        roles = _roles(tmap)
        mat_cols, comment_cols = _cols_with(roles, "material"), _cols_with(roles, "comment")
        attr_cols = {c: next(r[2] for r in rs if r[0] == "attribute") for c, rs in roles.items()
                     if any(r[0] == "attribute" for r in rs)}
        first = lambda raw, role: next((raw[c] for c in _cols_with(roles, role) if raw.get(c)), None)  # noqa: E731

        for row_idx, row_type, cells, raw, flags, marks in _iter_rows(
                table_key, values, rows_meta, overrides, fixes, approved):
            material_raw = next((raw[c] for c in mat_cols if raw.get(c)), None) or (cfg["default_material"] or None)
            mat = matcher.match(material_raw)
            if material_raw and mat is None and not any(f.startswith("pending:") for f in flags) \
                    and row_type != "cleaning":
                issues.append({"source": table_key, "key": row_idx, "issue": "no material rule", "value": material_raw})
            comment = " | ".join(raw[c] for c in comment_cols if raw.get(c)) or None
            attributes = {name: _attr_value(raw[c]) for c, name in attr_cols.items() if raw.get(c)}
            test_dt = _parse_date(cfg["date_rule"], raw)
            if cfg["date_rule"] and test_dt is None:
                flags.append("date_unparsed")

            # ---------------------------------------- logs, matrices, sessions
            if cfg["fact"] == "log_entry":
                merge = row_type == "sparse" and logs and logs[-1]["_table"] == table_key
                if merge and table_key == "PSI-22::acme_e_field_flames" and "DD-P07" not in approved:
                    flags.append("pending:DD-P07")
                    merge = False
                if merge:
                    for k, v in attributes.items():          # continuation of the previous day
                        prev = logs[-1]["attributes"].setdefault(k, [])
                        logs[-1]["attributes"][k] = (prev if isinstance(prev, list) else [prev]) + [v]
                    logs[-1]["quality_flags"].append(f"merged_row:{row_idx}")
                    continue
                n_fl = first(raw, "n_flames")
                logs.append({
                    "_table": table_key,
                    "investigation_id": inv_ids[table_key], "source_table_id": st_ids[table_key],
                    "source_row": row_idx, "row_type": "continuation" if row_type == "sparse" else "log",
                    "entry_type": cfg["entry_type"],
                    "entry_date_key": int(test_dt.strftime("%Y%m%d")) if test_dt else None,
                    "label": first(raw, "log_label"),
                    "material_id": mat_ids[mat["code"]] if mat else None, "material_raw": material_raw,
                    "fuel_dilution": mat["dilution"] if mat else None,
                    "n_flames_ignited": int(float(n_fl)) if n_fl and re.fullmatch(r"\d+(\.0)?", n_fl) else None,
                    "attributes": attributes, "comment": comment, "quality_flags": flags,
                })
                continue

            # ---------------------------------------- tests
            rule = OUTCOME_RULES.get(cfg["outcome_rule"])
            outcome = rule(raw) if rule else "not_reported"
            if row_type == "cleaning":
                outcome = "not_applicable"
            method = cfg["outcome_method"] or "none"
            status = "provisional" if method not in ("none", "not_applicable") and outcome not in (
                "not_reported", "not_applicable") else None
            test_run_id = len(runs) + 1
            invalid = []
            run = {
                "test_run_id": test_run_id, "test_id": None, "investigation_id": inv_ids[table_key],
                "source_table_id": st_ids[table_key], "source_row": row_idx,
                "row_type": "test" if row_type in ("data", "sparse") else row_type,
                "test_code": first(raw, "test_code"),
                "test_date_key": int(test_dt.strftime("%Y%m%d")) if test_dt else None,
                "material_id": mat_ids[mat["code"]] if mat else None,
                "material_raw": material_raw, "sample_geometry": mat["geometry"] if mat else None,
                "outcome_id": out_ids[outcome], "outcome_method": method if outcome != "not_reported" else "none",
                "outcome_status": status,
                "flame_type": (first(raw, "flame_type") or "").strip().lower() or None,
                "flow_direction": (first(raw, "flow_direction") or "").strip().lower() or None,
                "fuel_dilution": mat["dilution"] if mat else None,
                "o2_frac": None, "pressure_kpa": None, "flow_cm_s": None, "burn_time_s": None,
                "cool_flame": attributes.pop("cool_flame", None),
                "flame_color": attributes.pop("flame_color", None),
                "comment": comment, "attributes": attributes, "quality_flags": flags,
            }
            if isinstance(run["flame_color"], str):
                run["flame_color"] = run["flame_color"].lower()

            seen_vars: set[str] = set()
            for col, rs in roles.items():           # CSV order = priority (pmean before pinit)
                for role, _mod, var, src_unit in rs:
                    if role == "sequence" and col in cells:
                        seq, event = parse_sequence(cells[col]["value_raw"])
                        for s in seq:
                            steps.append({"test_run_id": test_run_id, **s, "raw": cells[col]["value_raw"]})
                        if event:
                            run["attributes"]["sequence_event"] = event
                        flowed = [s["flow_cm_s"] for s in seq if s["flow_cm_s"] is not None]
                        if len(flowed) == 1:         # R6: a single flow goes to the test row
                            run["flow_cm_s"] = flowed[0]
                        continue
                    if role != "measure" or col not in cells or var in seen_vars:
                        continue
                    m = _measure(cells[col], src_unit, canon_unit[var], issues, table_key, row_idx, col)
                    if m is None:
                        continue
                    if m.pop("midpoint"):
                        flags.append(f"midpoint:{var}")
                    if m["value_num"] is None and m["value_text"] is None:
                        continue
                    if col in marks:
                        m["flag"] = "sensor_error"
                        invalid.append(marks[col])
                    if var in NON_NEGATIVE and m["value_num"] is not None and m["value_num"] < 0:
                        m["flag"] = "sensor_error"
                        invalid.append(f"negative {var} ({cells[col]['value_raw']}); see DD-P03")
                    seen_vars.add(var)
                    add_measurement(test_run_id, var, col, cells[col]["value_raw"], m)
                    if var in CORE_COLUMNS and m["value_num"] is not None:
                        run[var] = round(m["value_num"], 6)

            derived = DERIVED.get(table_key)
            if derived:
                decision, fn = derived
                vals = fn(raw)
                if vals and decision in approved:
                    for var, val in vals.items():
                        if var in seen_vars:
                            continue
                        add_measurement(test_run_id, var, "(derived)", None,
                                        {"value_num": val, "value_min": None, "value_max": None, "value_text": None,
                                         "is_approx": False, "flag": "derived", "censored": None, "censor_limit": None})
                        flags.append(f"derived:{decision}")
                        if var in CORE_COLUMNS:
                            run[var] = round(val, 6)
                elif vals:
                    flags.append(f"pending:{decision}")

            if outcome == "not_reported":
                flags.append("outcome_not_in_table")
            run["is_valid"] = not invalid
            run["invalid_reason"] = "; ".join(invalid) or None
            run["quality_flags"] = sorted(set(flags))
            run["_slug"] = f"{accession}:{snake(table_key.split('::')[1])}"
            runs.append(run)

    fact_runs = pd.DataFrame(runs)
    # R9: stable test_id = "<psi_id>:<source id>"; falls back to table + row when the id is missing or repeated.
    code = fact_runs["test_code"].fillna("").str.replace(r"\s+", "", regex=True)
    acc = fact_runs["_slug"].str.split(":").str[0]
    dup = (code == "") | pd.Series(list(zip(acc, code))).duplicated(keep=False).values
    fact_runs["test_id"] = (acc + ":" + code).where(~dup, fact_runs["_slug"] + ":r" + fact_runs["source_row"].astype(str))
    fact_runs = fact_runs.drop(columns="_slug")
    if fact_runs["test_id"].duplicated().any():
        raise ValueError("test_id is not unique")
    for c in ("test_date_key", "material_id"):
        fact_runs[c] = fact_runs[c].astype("Int64")
    fact_runs["attributes"] = fact_runs["attributes"].map(lambda d: json.dumps(d, ensure_ascii=False) if d else None)

    fact_meas = pd.DataFrame(measurements)
    dup = fact_meas.duplicated(["test_run_id", "variable_id"])
    if dup.any():
        raise ValueError(f"Variable repeated within a test: {fact_meas[dup].head().to_dict('records')}")

    test_steps = pd.DataFrame(steps, columns=["test_run_id", "step_order", "flow_cm_s", "note", "raw"])

    fact_logs = pd.DataFrame(logs).drop(columns=["_table"])
    fact_logs.insert(0, "log_entry_id", range(1, len(fact_logs) + 1))
    for c in ("entry_date_key", "material_id", "n_flames_ignited"):
        fact_logs[c] = fact_logs[c].astype("Int64")
    fact_logs["attributes"] = fact_logs["attributes"].map(lambda d: json.dumps(d, ensure_ascii=False) if d else None)

    keys = sorted(set(fact_runs["test_date_key"].dropna()) | set(fact_logs["entry_date_key"].dropna()))
    dim_date = pd.DataFrame({"date_key": [int(k) for k in keys]})
    dim_date["full_date"] = pd.to_datetime(dim_date["date_key"].astype(str), format="%Y%m%d").dt.date
    dim_date["year"] = dim_date["date_key"] // 10000
    dim_date["month"] = dim_date["date_key"] // 100 % 100
    dim_date["day"] = dim_date["date_key"] % 100
    dim_date["quarter"] = (dim_date["month"] - 1) // 3 + 1

    files = st["files"].reset_index(drop=True)
    kind_ids = dict(zip(dims["dim_file_kind"]["code"], dims["dim_file_kind"]["file_kind_id"]))
    acc_ids = dict(zip(dims["dim_investigation"]["accession"], dims["dim_investigation"]["investigation_id"]))
    fact_file = pd.DataFrame({
        "file_id": range(1, len(files) + 1), "investigation_id": files["accession"].map(acc_ids),
        "file_kind_id": files["kind"].map(kind_ids), "category": files["category"],
        "subcategory": files["subcategory"], "file_name": files["file_name"], "ext": files["ext"],
        "bytes": files["bytes"], "restricted": files["restricted"], "s3_key": files["s3_key"],
        "is_useful": files["is_useful"], "is_rag_document": files["is_rag_document"],
        "duplicate_of_other_investigation": files["duplicate_of_other_investigation"],
    })

    return {"dim_date": dim_date[["date_key", "full_date", "year", "quarter", "month", "day"]],
            "fact_test_run": fact_runs, "fact_measurement": fact_meas, "test_step": test_steps,
            "fact_log_entry": fact_logs, "fact_file": fact_file,
            "_issues": pd.DataFrame(issues, columns=["source", "key", "issue", "value"])}


def build_core() -> dict[str, pd.DataFrame]:
    st = {p.stem: pd.read_parquet(p) for p in config.STAGING_DIR.glob("*.parquet") if not p.stem.startswith("_")}
    dims = build_dimensions(st)
    facts = build_facts(st, dims)
    return {**dims, **facts}
