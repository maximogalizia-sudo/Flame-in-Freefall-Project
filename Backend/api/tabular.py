"""Tabular tool for the assistant: POST /tests/search over LC's curated tables (docs/PLAN_ASISTENTE.md, M2).

Safety: the request is a typed `TestsSearchRequest`. Every column that can appear in a query comes from the
per-family whitelist below and is composed with psycopg2.sql.Identifier; every value is a bound parameter.
No text from the request is ever pasted into SQL.

A filter that does not exist for the family (e.g. `diluent` for solids) is rejected with a clear message,
so the assistant learns it instead of silently getting an unfiltered answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from psycopg2 import sql

from . import db
from .contracts import TestsSearchRequest, TestsSearchResponse


class SearchError(ValueError):
    """The request is valid JSON but asks for something this family does not have."""


@dataclass(frozen=True)
class FamilySpec:
    table: str
    id_col: str
    psi_id: sql.Composable                 # SQL expression for the PSI accession of each row
    o2: sql.Composable | None              # SQL expression of the O2 fraction (0-1) used by o2_min / o2_max
    pressure_kpa: sql.Composable | None
    fuel: str | None = None
    material: str | None = None
    flame_type: str | None = None
    diluent: str | None = None
    outcome: str = "outcome"
    model_rows: str | None = None
    group_by: frozenset[str] = field(default_factory=frozenset)
    row_cols: tuple[str, ...] = ()
    extra_rows: tuple[tuple[str, sql.Composable], ...] = ()   # (output name, SQL expression)


I = sql.Identifier
FAMILIES: dict[str, FamilySpec] = {
    "flex": FamilySpec(
        table="flex_tests", id_col="flex_id", psi_id=sql.SQL("'PSI-69'"),
        o2=I("o2_frac"), pressure_kpa=I("pressure_kpa"),
        fuel="fuel", diluent="diluent", outcome="test_end", model_rows="in_main_model",
        group_by=frozenset({"fuel", "support", "p_level", "diluent", "test_end", "in_main_model", "extinct",
                            "o2_frac", "co2_frac", "he_frac"}),
        row_cols=("flex_no", "fuel", "support", "p_level", "pressure_kpa", "o2_frac", "co2_frac", "he_frac",
                  "diluent", "d0_mm", "burn_time_s", "test_end", "in_main_model", "test_date", "source"),
    ),
    "cfig": FamilySpec(
        table="cfig_tests", id_col="test_id", psi_id=sql.SQL("'PSI-159'"),
        o2=I("o2_mid"), pressure_kpa=sql.SQL("({} * 100.0)").format(I("pressure_bar")),
        fuel="fuel", flame_type="flame_type", outcome="end", model_rows="ext_model",
        group_by=frozenset({"fuel", "flame_type", "end", "ignited", "ignition", "pressure_bar", "o2_nominal",
                            "ext_model", "self_ext"}),
        row_cols=("flame_type", "fuel", "pressure_bar", "o2_mid", "o2_nominal", "jet_fuel_frac", "atm_fuel_frac",
                  "fuel_flow_mg_s", "ignited", "end", "burn_time_s", "ext_model", "source"),
        extra_rows=(("pressure_kpa", sql.SQL("round(({} * 100.0)::numeric, 1)").format(I("pressure_bar"))),),
    ),
    "solids": FamilySpec(
        table="bass_tests", id_col="test_id",
        psi_id=sql.SQL("CASE WHEN {} = 'BASS-II' THEN 'PSI-25' ELSE 'PSI-26' END").format(I("experiment")),
        o2=sql.SQL("({} / 100.0)").format(I("o2_pct")), pressure_kpa=None,
        material="material", outcome="outcome_group",
        group_by=frozenset({"experiment", "material", "outcome_group", "outcome", "investigator", "hardware_issue"}),
        row_cols=("experiment", "material", "material_raw", "o2_pct", "air_cm_s_first", "outcome", "outcome_group",
                  "hardware_issue", "observation", "source"),
    ),
}
FUEL_ALIASES = {"n_heptane": "heptane", "n-heptane": "heptane", "n_butane": "butane", "n-butane": "butane"}


def _eq_ci(col: str, value: str) -> tuple[sql.Composable, list]:
    return sql.SQL("lower({}) = lower(%s)").format(I(col)), [value]


def build_where(spec: FamilySpec, req: TestsSearchRequest) -> tuple[sql.Composable, list]:
    family = req.family
    clauses: list[sql.Composable] = []
    params: list = []

    def unsupported(name: str) -> None:
        raise SearchError(f"filter '{name}' does not apply to family '{family}'")

    def add(clause: sql.Composable, values: list) -> None:
        clauses.append(clause)
        params.extend(values)

    if req.fuel is not None:
        if not spec.fuel:
            unsupported("fuel")
        add(*_eq_ci(spec.fuel, FUEL_ALIASES.get(req.fuel.lower(), req.fuel)))
    if req.material is not None:
        if not spec.material:
            unsupported("material")
        add(*_eq_ci(spec.material, req.material))
    if req.flame_type is not None:
        if not spec.flame_type:
            unsupported("flame_type")
        add(sql.SQL("{} = %s").format(I(spec.flame_type)), [req.flame_type])
    if req.diluent is not None:
        if not spec.diluent:
            unsupported("diluent")
        add(sql.SQL("{} = %s").format(I(spec.diluent)), [req.diluent])
    if req.o2_min is not None:
        add(sql.SQL("{} >= %s").format(spec.o2), [req.o2_min])
    if req.o2_max is not None:
        add(sql.SQL("{} <= %s").format(spec.o2), [req.o2_max])
    for bound, op in ((req.pressure_kpa_min, ">="), (req.pressure_kpa_max, "<=")):
        if bound is None:
            continue
        if spec.pressure_kpa is None:
            unsupported("pressure_kpa_min/max (no pressure column for solids; BASS ran at cabin pressure)")
        add(sql.SQL("{} " + op + " %s").format(spec.pressure_kpa), [bound])
    if req.outcome is not None:
        add(*_eq_ci(spec.outcome, req.outcome))
    if req.only_model_rows:
        if not spec.model_rows:
            unsupported("only_model_rows")
        add(sql.SQL("{}").format(I(spec.model_rows)), [])
    where = sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("TRUE")
    return where, params


def search(req: TestsSearchRequest) -> TestsSearchResponse:
    spec = FAMILIES[req.family]
    bad = [g for g in req.group_by if g not in spec.group_by]
    if bad:
        raise SearchError(f"group_by {bad} not allowed for '{req.family}'; allowed: {sorted(spec.group_by)}")
    table = I("mart", spec.table)
    where, params = build_where(spec, req)

    n_total = db.query_one(sql.SQL("SELECT count(*) AS n FROM {} WHERE {}").format(table, where), tuple(params))["n"]

    counts: list[dict] = []
    if req.group_by:
        groups = sql.SQL(", ").join(map(I, req.group_by))
        counts = db.query(sql.SQL("SELECT {g}, count(*) AS n FROM {t} WHERE {w} GROUP BY {g} ORDER BY n DESC, {g}")
                          .format(g=groups, t=table, w=where), tuple(params))

    select = [sql.SQL("{} AS test_id").format(I(spec.id_col)), sql.SQL("{} AS psi_id").format(spec.psi_id)]
    select += [I(c) for c in spec.row_cols]
    select += [sql.SQL("{} AS {}").format(expr, I(name)) for name, expr in spec.extra_rows]
    rows = db.query(sql.SQL("SELECT {s} FROM {t} WHERE {w} ORDER BY {id} LIMIT %s")
                    .format(s=sql.SQL(", ").join(select), t=table, w=where, id=I(spec.id_col)),
                    tuple(params) + (req.limit,))
    return TestsSearchResponse(family=req.family, n_total=n_total, counts=counts, rows=rows)
