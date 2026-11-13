"""Transformación: data/staging/*.parquet -> tablas del esquema core (DataFrames).

Reglas de negocio de esta etapa:
  * Solo entran a fact_test_run las tablas listadas en mappings/source_columns.csv.
  * Cada valor se convierte a la unidad canónica de mappings/variables.csv.
  * El resultado (outcome) se deriva con reglas explícitas por investigación (ver OUTCOME_RULES).
  * Toda corrección manual sale de mappings/overrides.csv y queda en quality_flags.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import config

MAPPINGS = Path(__file__).resolve().parent / "mappings"

# ---------------------------------------------------------------- unidades
# (desde, hacia) -> (factor, offset):  valor_canónico = valor * factor + offset
CONVERSIONS = {
    ("mmHg", "atm"): (1 / 760, 0), ("bar", "atm"): (1 / 1.01325, 0), ("kPa", "atm"): (1 / 101.325, 0),
    ("%", "mol/mol"): (0.01, 0), ("°C", "K"): (1, 273.15),
    ("cm", "mm"): (10, 0), ("mm", "cm"): (0.1, 0), ("m", "cm"): (100, 0),
    ("min", "s"): (60, 0), ("ms", "s"): (0.001, 0), ("cm/s", "mm/s"): (10, 0),
}

# ---------------------------------------------------------------- resultados
OUTCOMES = [
    # code,             label,                                 self_extinguished
    ("extinction",      "Se apagó sola",                       True),
    ("no_propagation",  "No propagó",                          True),
    ("complete",        "Ardió hasta consumirse",              False),
    ("flow_terminated", "Seguía ardiendo al cortar el flujo",  False),
    ("partial",         "Propagación parcial",                 None),
    ("no_ignition",     "No encendió",                         None),
    ("flash",           "Destello sin llama sostenida",        None),
    ("disruption",      "Disrupción de la gota",               None),
    ("not_reported",    "Sin resultado en la tabla",           None),
]


def _outcome_flex(row: dict) -> str:
    return {"extinction": "extinction", "completion": "complete", "disruption": "disruption"}.get(
        (row.get("test_end") or "").lower(), "not_reported")


def _outcome_cfi_g(row: dict) -> str:
    ign = (row.get("hot_ignition") or "").strip().lower()
    term = (row.get("self_ext_or_flow_term") or "").strip().upper()
    if ign == "no":
        return "no_ignition"
    if ign == "flash":
        return "flash"
    if ign == "yes":
        return {"SE": "extinction", "FT": "flow_terminated"}.get(term, "not_reported")
    return "not_reported"


def _outcome_saffire_2(row: dict) -> str:
    """Según el largo quemado en µg (las muestras miden 29 cm)."""
    raw = (row.get("micro_g_burn_length") or "").lower()
    if raw.startswith("~ 0") or raw == "0":
        return "no_propagation"
    if raw.startswith("29"):
        return "complete"
    if raw:
        return "partial"
    return "not_reported"


OUTCOME_RULES = {
    "PSI-69::flex": _outcome_flex,
    "PSI-159::cfi_g_normal_flames": _outcome_cfi_g,
    "PSI-159::cfi_g_inverse_flames": _outcome_cfi_g,
    "PSI-99::saffire_2": _outcome_saffire_2,
    # SAFFIRE-I y III: el resultado no está en el CSV (está en los reportes) -> not_reported.
}

# ---------------------------------------------------------------- helpers
RE_LEADING_NUM = re.compile(r"^(?P<approx>~\s*)?(?P<num>\d+\.?\d*|\.\d+)\s*(?P<unit>[a-zA-Z/%°]+)?\s*(\(note \d\))?$", re.I)


def _convert(value: float | None, unit_from: str | None, unit_to: str) -> float | None:
    if value is None or pd.isna(value):
        return None
    if not unit_from or unit_from == unit_to:
        return float(value)
    if (unit_from, unit_to) not in CONVERSIONS:
        raise ValueError(f"Sin conversión {unit_from} -> {unit_to}")
    factor, offset = CONVERSIONS[(unit_from, unit_to)]
    return float(value) * factor + offset


def _test_date(table_key: str, raw: dict) -> date | None:
    if table_key == "PSI-69::flex":
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(raw.get("test_date") or "", fmt).date()
            except ValueError:
                continue
    if table_key.startswith("PSI-159::"):
        m = re.match(r"^(\d{2})(\d{3})", raw.get("test") or "")   # YYDDD
        if m:
            return date(2000 + int(m[1]), 1, 1) + timedelta(days=int(m[2]) - 1)
    return None


def _ids(values, start: int = 1) -> dict:
    return {v: i for i, v in enumerate(sorted(set(v for v in values if v is not None)), start=start)}


# ---------------------------------------------------------------- dimensiones
def build_dimensions(st: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    inv = st["investigations"]
    fam_map = pd.read_csv(MAPPINGS / "families.csv")
    missing = set(inv["family"].dropna()) - set(fam_map["family"])
    if missing:
        raise ValueError(f"Familias sin grupo en families.csv: {missing}")

    groups = fam_map.drop_duplicates("group_code")[["group_code", "group_name"]].reset_index(drop=True)
    groups.insert(0, "family_group_id", range(1, len(groups) + 1))
    families = fam_map.merge(groups, on=["group_code", "group_name"])
    families = families.sort_values("family").reset_index(drop=True)
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

    mats = pd.read_csv(MAPPINGS / "materials.csv", dtype=str)
    phase = {"liquid": "liquid", "gaseous": "gas", "solid": "solid"}
    classes = pd.DataFrame({"code": sorted(mats["fuel_class"].unique())})
    classes["phase"] = classes["code"].map(lambda c: phase[c.split("_")[0]])
    classes.insert(0, "fuel_class_id", range(1, len(classes) + 1))
    materials = mats.drop_duplicates("code")[["code", "name", "fuel_class"]].sort_values("code").reset_index(drop=True)
    materials.insert(0, "material_id", range(1, len(materials) + 1))
    materials["fuel_class_id"] = materials.pop("fuel_class").map(dict(zip(classes["code"], classes["fuel_class_id"])))

    var = pd.read_csv(MAPPINGS / "variables.csv", dtype=str)
    vgroups = pd.DataFrame({"code": sorted(var["group"].unique())})
    vgroups.insert(0, "variable_group_id", range(1, len(vgroups) + 1))
    units = pd.DataFrame({"code": sorted(var["unit"].unique())})
    units.insert(0, "unit_id", range(1, len(units) + 1))
    variables = pd.DataFrame({
        "variable_id": range(1, len(var) + 1), "code": var["code"], "label": var["label"],
        "variable_group_id": var["group"].map(dict(zip(vgroups["code"], vgroups["variable_group_id"]))),
        "unit_id": var["unit"].map(dict(zip(units["code"], units["unit_id"]))),
        "is_core": var["is_core"].str.lower().eq("true"), "description": var["description"],
    })

    outcomes = pd.DataFrame(OUTCOMES, columns=["code", "label", "self_extinguished"])
    outcomes.insert(0, "outcome_id", range(1, len(outcomes) + 1))
    outcomes["self_extinguished"] = outcomes["self_extinguished"].astype("boolean")

    mapping = pd.read_csv(MAPPINGS / "source_columns.csv", dtype=str)
    t = st["experimental_tables"].sort_values("table_id").reset_index(drop=True)
    source_tables = pd.DataFrame({
        "source_table_id": range(1, len(t) + 1), "table_key": t["table_id"],
        "investigation_id": t["accession"].map(acc_to_id), "table_name": t["table_name"],
        "file_name": t["file_name"], "encoding": t["encoding"], "n_data_rows": t["n_data_rows"],
        "is_mapped": t["table_id"].isin(set(mapping["table_id"])),
    })

    kinds = pd.DataFrame({"code": sorted(st["files"]["kind"].dropna().unique())})
    kinds.insert(0, "file_kind_id", range(1, len(kinds) + 1))

    st_ids = dict(zip(source_tables["table_key"], source_tables["source_table_id"]))
    var_ids = dict(zip(variables["code"], variables["variable_id"]))
    map_df = pd.DataFrame({
        "source_table_id": mapping["table_id"].map(st_ids), "column_name": mapping["column_name"],
        "role": mapping["role"], "variable_id": mapping["target"].where(mapping["role"] == "measure").map(var_ids).astype("Int64"),
        "source_unit": mapping["source_unit"], "notes": mapping["notes"],
    })
    if map_df["source_table_id"].isna().any():
        raise ValueError("source_columns.csv referencia tablas inexistentes")
    bad = mapping[(mapping["role"] == "measure") & ~mapping["target"].isin(var_ids)]
    if not bad.empty:
        raise ValueError(f"Variables desconocidas en source_columns.csv: {bad['target'].tolist()}")

    return {
        "dim_family_group": groups.rename(columns={"group_code": "code", "group_name": "name"}),
        "dim_family": families[["family_id", "family", "family_group_id"]].rename(columns={"family": "name"}),
        "dim_platform": platforms, "dim_investigation": d_inv,
        "dim_hardware": hardware, "bridge_investigation_hardware": bridge,
        "dim_fuel_class": classes, "dim_material": materials,
        "dim_variable_group": vgroups, "dim_unit": units, "dim_variable": variables,
        "dim_outcome": outcomes, "dim_source_table": source_tables, "dim_file_kind": kinds,
        "map_source_column": map_df,
    }


# ---------------------------------------------------------------- hechos
def build_facts(st: dict[str, pd.DataFrame], dims: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    mapping = pd.read_csv(MAPPINGS / "source_columns.csv", dtype=str, keep_default_na=False)
    overrides = pd.read_csv(MAPPINGS / "overrides.csv", dtype=str, keep_default_na=False)
    mats = pd.read_csv(MAPPINGS / "materials.csv", dtype=str, keep_default_na=False)
    variables = pd.read_csv(MAPPINGS / "variables.csv", dtype=str)
    canon_unit = dict(zip(variables["code"], variables["unit"]))
    core_cols = {"o2_mole_fraction": "o2_mole_fraction", "pressure": "pressure_atm",
                 "flow_velocity": "flow_velocity_cm_s", "burn_time": "burn_time_s"}

    st_ids = dict(zip(dims["dim_source_table"]["table_key"], dims["dim_source_table"]["source_table_id"]))
    inv_ids = dict(zip(dims["dim_source_table"]["table_key"], dims["dim_source_table"]["investigation_id"]))
    var_ids = dict(zip(dims["dim_variable"]["code"], dims["dim_variable"]["variable_id"]))
    out_ids = dict(zip(dims["dim_outcome"]["code"], dims["dim_outcome"]["outcome_id"]))
    mat_ids = dict(zip(dims["dim_material"]["code"], dims["dim_material"]["material_id"]))
    mat_by_raw = {r["raw_value"]: r for _, r in mats.iterrows()}

    values = st["experimental_values"]
    rows_meta = st["experimental_rows"]
    runs, measurements, issues = [], [], []
    test_run_id = measurement_id = 0

    for table_key, tmap in mapping.groupby("table_id", sort=True):
        roles = dict(zip(tmap["column_name"], tmap["role"]))
        targets = dict(zip(tmap["column_name"], tmap["target"]))
        src_units = dict(zip(tmap["column_name"], tmap["source_unit"]))
        tv = values[values["table_id"] == table_key]
        data_rows = rows_meta[(rows_meta["table_id"] == table_key) & (rows_meta["row_type"] != "note")]["row_idx"]
        t_over = overrides[overrides["table_id"] == table_key]
        prev_raw: dict = {}

        for row_idx in sorted(data_rows):
            cells = {r["column_name"]: r for r in tv[tv["row_idx"] == row_idx].to_dict("records")}
            raw = {c: r["value_raw"] for c, r in cells.items()}
            flags: list[str] = []

            # --- overrides manuales
            for _, o in t_over[t_over["row_idx"].astype(int) == row_idx].iterrows():
                col = o["column_name"]
                if o["action"] == "replace":
                    raw[col] = o["new_value"]
                    cells[col] = {"value_raw": o["new_value"], **_reparse(o["new_value"])}
                elif o["action"] == "fill_down":
                    raw[col] = prev_raw.get(col)
                    cells[col] = {"value_raw": raw[col], "kind": "text"}
                elif o["action"] == "null":
                    raw.pop(col, None)
                    cells.pop(col, None)
                flags.append(f"{o['action']}:{col}")
            prev_raw = raw

            # --- atributos del ensayo
            material_raw = next((raw[c] for c, r in roles.items() if r == "material" and raw.get(c)), None)
            mat = mat_by_raw.get(material_raw) if material_raw else None
            if material_raw and mat is None:
                issues.append({"source": table_key, "key": row_idx, "issue": "material sin mapear", "value": material_raw})
            attr = lambda role: next((raw[c] for c, r in roles.items() if r == role and raw.get(c)), None)  # noqa: E731
            flame_type = (attr("flame_type") or "").strip().lower() or None
            flow_dir = (attr("flow_direction") or "").strip().lower() or None
            rule = OUTCOME_RULES.get(table_key)
            outcome = rule(raw) if rule else "not_reported"
            test_dt = _test_date(table_key, raw)

            test_run_id += 1
            run = {
                "test_run_id": test_run_id, "investigation_id": inv_ids[table_key],
                "source_table_id": st_ids[table_key], "source_row": int(row_idx),
                "test_code": attr("test_code"),
                "test_date_key": int(test_dt.strftime("%Y%m%d")) if test_dt else None,
                "material_id": mat_ids[mat["code"]] if mat is not None else None,
                "outcome_id": out_ids[outcome], "flame_type": flame_type, "flow_direction": flow_dir,
                "fuel_dilution": float(mat["dilution_fraction"]) if mat is not None and mat["dilution_fraction"] else None,
                "o2_mole_fraction": None, "pressure_atm": None, "flow_velocity_cm_s": None, "burn_time_s": None,
                "cool_flame": raw.get(next((c for c, t in targets.items() if t == "cool_flame"), ""), None),
                "flame_color": (raw.get(next((c for c, t in targets.items() if t == "flame_color"), "")) or "").lower() or None,
                "comment": attr("comment"), "quality_flags": flags,
            }

            # --- medidas
            for col, role in roles.items():
                if role != "measure" or col not in cells:
                    continue
                c, var = cells[col], targets[col]
                unit_to = canon_unit[var]
                unit_from = c.get("unit") if isinstance(c.get("unit"), str) else (src_units[col] or None)
                kind, is_approx = c.get("kind"), bool(c.get("is_approx"))
                vnum = c.get("value_num")
                vmin, vmax = c.get("value_min"), c.get("value_max")
                vtext = None
                if kind == "text":
                    m = RE_LEADING_NUM.match(c["value_raw"])
                    if m:
                        vnum, is_approx = float(m["num"]), bool(m["approx"])
                        unit_from = m["unit"] or unit_from
                    vtext = c["value_raw"]
                if unit_from == "mol/mol" and unit_to == "mol/mol" and vnum is not None and vnum > 1:
                    unit_from = "%"
                try:
                    vnum, vmin, vmax = (_convert(x, unit_from, unit_to) for x in (vnum, vmin, vmax))
                except ValueError as e:
                    issues.append({"source": table_key, "key": row_idx, "issue": str(e), "value": col})
                    continue
                if vnum is None and vmin is not None and vmax is not None:
                    vnum = (vmin + vmax) / 2          # rango/lista: punto medio
                    flags.append(f"midpoint:{var}")
                measurement_id += 1
                measurements.append({
                    "measurement_id": measurement_id, "test_run_id": test_run_id, "variable_id": var_ids[var],
                    "value_num": vnum, "value_min": vmin, "value_max": vmax, "value_text": vtext,
                    "is_approx": is_approx, "source_column": col, "source_raw": c["value_raw"],
                })
                if var in core_cols and vnum is not None:
                    run[core_cols[var]] = round(vnum, 6)
            if outcome == "not_reported":
                flags.append("outcome_not_in_table")
            runs.append(run)

    fact_runs = pd.DataFrame(runs)
    for c in ("test_date_key", "material_id"):
        fact_runs[c] = fact_runs[c].astype("Int64")
    fact_meas = pd.DataFrame(measurements)

    dup = fact_meas.duplicated(["test_run_id", "variable_id"])
    if dup.any():
        raise ValueError(f"Variable repetida dentro de un ensayo: {fact_meas[dup].head().to_dict('records')}")

    dates = sorted({k for k in fact_runs["test_date_key"].dropna()})
    dim_date = pd.DataFrame({"date_key": dates})
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
            "fact_test_run": fact_runs, "fact_measurement": fact_meas, "fact_file": fact_file,
            "_issues": pd.DataFrame(issues)}


def _reparse(raw: str) -> dict:
    from .ingest_tables import norm_unit, parse_value
    v = parse_value(raw)
    v["unit"] = norm_unit(v.pop("value_unit"))
    v.pop("value_raw")
    return v


def build_core() -> dict[str, pd.DataFrame]:
    st = {p.stem: pd.read_parquet(p) for p in config.STAGING_DIR.glob("*.parquet") if not p.stem.startswith("_")}
    dims = build_dimensions(st)
    facts = build_facts(st, dims)
    return {**dims, **facts}
