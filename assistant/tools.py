"""Tools the assistant can call. Two interchangeable backends with the same methods:

* HttpTools  — MG's API endpoints (/predict, /coverage, /explain/{family}, /tests/search, /rag/search), with
               `Authorization: Bearer ASSISTANT_API_TOKEN`. This is the production path (criterion 5: one database).
* LocalTools — the same answers computed in-process from `flame.models` and `data/processed/*.parquet`.
               Used for development, tests, and while MG's /rag/search (M3) is not ready (rag_search -> unavailable).

Every call returns a ToolResult; tools never raise to the pipeline (a failed tool is reported, not hidden).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import pandas as pd

from flame import models as fm
from flame import analytics as fa
from flame import scenarios as fs
from flame.contracts import (Conditions, EffectsResponse, EvidenceItem, Prediction, RagSearchRequest, RagSearchResponse,
                             RiskRequest, RiskResponse, SweepRequest, SweepResponse, TestsSearchRequest, TestsSearchResponse)

from .config import get_settings


@dataclass
class ToolResult:
    name: str
    args: dict
    ok: bool
    data: Any = None
    error: str | None = None
    ms: int = 0
    meta: dict = field(default_factory=dict)


def _timed(name: str, args: dict, fn) -> ToolResult:
    t0 = time.perf_counter()
    try:
        data = fn()
        return ToolResult(name, args, True, data, ms=int((time.perf_counter() - t0) * 1000))
    except Exception as e:  # noqa: BLE001 — report any tool failure instead of crashing the answer
        return ToolResult(name, args, False, None, f"{type(e).__name__}: {e}", ms=int((time.perf_counter() - t0) * 1000))


# ------------------------------------------------------------------ local backend
LOCAL_SPECS = {   # mirrors MG's Backend/api/tabular.py FAMILIES (same filters, same outcome columns)
    'flex': dict(table='flex_tests', id='flex_id', psi='PSI-69', o2='o2_frac', fuel='fuel', diluent='diluent',
                 outcome='test_end', model_rows='in_main_model',
                 cols=['flex_no', 'fuel', 'support', 'p_level', 'pressure_kpa', 'o2_frac', 'co2_frac', 'he_frac', 'diluent',
                       'd0_mm', 'burn_time_s', 'test_end', 'in_main_model', 'source']),
    'cfig': dict(table='cfig_tests', id='test_id', psi='PSI-159', o2='o2_mid', fuel='fuel', flame_type='flame_type',
                 outcome='end', model_rows='ext_model',
                 cols=['flame_type', 'fuel', 'pressure_bar', 'o2_mid', 'o2_nominal', 'jet_fuel_frac', 'atm_fuel_frac',
                       'fuel_flow_mg_s', 'ignited', 'end', 'burn_time_s', 'ext_model', 'source']),
    'solids': dict(table='bass_tests', id='test_id', psi=None, o2='o2_pct', material='material', outcome='outcome_group',
                   cols=['experiment', 'material', 'material_raw', 'o2_pct', 'air_cm_s_first', 'outcome', 'outcome_group',
                         'hardware_issue', 'observation', 'source']),
}


def _local_tests_search(req: TestsSearchRequest) -> TestsSearchResponse:
    s = LOCAL_SPECS[req.family]
    d = fm.table(s['table']).copy()
    eq = lambda col, v: d[col].astype(str).str.lower() == str(v).lower()   # noqa: E731
    if req.fuel is not None:
        d = d[eq(s['fuel'], {'n_heptane': 'heptane', 'n_butane': 'butane'}.get(req.fuel, req.fuel))]
    if req.material is not None:
        d = d[eq(s['material'], req.material)]
    if req.flame_type is not None:
        d = d[d[s['flame_type']] == req.flame_type]
    if req.diluent is not None:
        d = d[d[s['diluent']] == req.diluent]
    o2 = d[s['o2']] / (100 if req.family == 'solids' else 1)
    if req.o2_min is not None:
        d = d[o2 >= req.o2_min]; o2 = o2[d.index]
    if req.o2_max is not None:
        d = d[o2 <= req.o2_max]
    if req.pressure_kpa_min is not None or req.pressure_kpa_max is not None:
        p = d['pressure_kpa'] if req.family == 'flex' else d['pressure_bar'] * 100
        if req.pressure_kpa_min is not None: d = d[p >= req.pressure_kpa_min]
        if req.pressure_kpa_max is not None: d = d[p[d.index] <= req.pressure_kpa_max]
    if req.outcome is not None:
        d = d[eq(s['outcome'], req.outcome)]
    if req.only_model_rows and s.get('model_rows'):
        d = d[d[s['model_rows']]]
    counts = []
    if req.group_by:
        counts = (d.groupby(list(req.group_by), dropna=False).size().reset_index(name='n')
                  .sort_values('n', ascending=False).to_dict('records'))
    rows = d.sort_values(s['id']).head(req.limit)
    out = rows[[s['id']] + [c for c in s['cols'] if c in rows.columns]].rename(columns={s['id']: 'test_id'})
    out.insert(1, 'psi_id', s['psi'] or rows['experiment'].map({'BASS-II': 'PSI-25', 'BASS': 'PSI-26'}))
    rec = out.astype(object).where(out.notna(), None).to_dict('records')
    return TestsSearchResponse(family=req.family, n_total=int(len(d)), counts=counts, rows=rec)


class LocalTools:
    mode = 'local'

    def predict(self, c: Conditions) -> ToolResult:
        return _timed('predict', c.model_dump(exclude_none=True), lambda: fm.predict(c))

    def coverage(self, c: Conditions, k: int = 5) -> ToolResult:
        return _timed('coverage', {**c.model_dump(exclude_none=True), 'k': k}, lambda: fm.coverage(c, k=k))

    def explain(self, family: str) -> ToolResult:
        return _timed('explain', {'family': family}, lambda: fm.explain(family))

    def tests_search(self, req: TestsSearchRequest) -> ToolResult:
        return _timed('tests_search', req.model_dump(exclude_none=True), lambda: _local_tests_search(req))

    def rag_search(self, req: RagSearchRequest) -> ToolResult:
        return ToolResult('rag_search', req.model_dump(), False, None, 'unavailable: document search runs on MG\'s API (M3)')

    # v3 (mentors): early-warning risk, scenarios, per-variable effects
    def risk(self, req: RiskRequest) -> ToolResult:
        return _timed('risk', req.model_dump(), lambda: fs.risk(req))

    def sweep(self, req: SweepRequest) -> ToolResult:
        return _timed('sweep', req.model_dump(exclude_none=True), lambda: fs.sweep(req))

    def effects(self, variable: str) -> ToolResult:
        return _timed('effects', {'variable': variable}, lambda: fs.effects(variable))

    # analytic tools over ALL the data (always computed in-process from flame + data/processed)
    def profile(self, args) -> ToolResult:
        var, fam = args
        return _timed('profile', {'variable': var, 'family': fam}, lambda: fa.profile(var, fam))

    def rank(self, args) -> ToolResult:
        target, fam, k = args
        return _timed('rank', {'target': target, 'family': fam, 'k': k}, lambda: fa.rank(target, fam, k))

    def gaps(self, fam) -> ToolResult:
        return _timed('gaps', {'family': fam}, lambda: fa.gaps(fam))


# ------------------------------------------------------------------ HTTP backend (MG's API)
class HttpTools:
    mode = 'http'

    # analytic tools are pure functions over the curated tables shipped with this service: computed in-process
    def profile(self, args) -> ToolResult:
        return LocalTools().profile(args)

    def rank(self, args) -> ToolResult:
        return LocalTools().rank(args)

    def gaps(self, fam) -> ToolResult:
        return LocalTools().gaps(fam)

    def __init__(self, base_url: str, token: str | None, timeout_s: float = 20.0, transport: httpx.BaseTransport | None = None):
        self.base = base_url.rstrip('/')
        self.headers = {'Accept': 'application/json', **({'Authorization': f'Bearer {token}'} if token else {})}
        self.timeout, self.transport = timeout_s, transport

    def _req(self, method: str, path: str, json: dict | None = None):
        with httpx.Client(timeout=self.timeout, transport=self.transport) as cl:
            r = cl.request(method, self.base + path, json=json, headers=self.headers)
            r.raise_for_status()
            return r.json()

    def predict(self, c: Conditions) -> ToolResult:
        body = c.model_dump(exclude_none=True)
        return _timed('predict', body, lambda: [Prediction(**p) for p in self._req('POST', '/predict', body)])

    def coverage(self, c: Conditions, k: int = 5) -> ToolResult:
        body = c.model_dump(exclude_none=True)
        return _timed('coverage', {**body, 'k': k}, lambda: [EvidenceItem(**e) for e in self._req('POST', '/coverage', body)][:k])

    def explain(self, family: str) -> ToolResult:
        return _timed('explain', {'family': family}, lambda: self._req('GET', f'/explain/{family}'))

    def tests_search(self, req: TestsSearchRequest) -> ToolResult:
        body = req.model_dump(exclude_none=True)
        return _timed('tests_search', body, lambda: TestsSearchResponse(**self._req('POST', '/tests/search', body)))

    def rag_search(self, req: RagSearchRequest) -> ToolResult:
        body = req.model_dump()
        return _timed('rag_search', body, lambda: RagSearchResponse(**self._req('POST', '/rag/search', body)))

    # v3 endpoints: if MG's API does not serve them yet (404) or fails, compute them in-process with the same
    # flame code (pure functions over models/ and data/processed/, shipped with this service).
    def _with_local(self, r: ToolResult, local) -> ToolResult:
        if r.ok:
            return r
        lr = local()
        lr.meta['fallback'] = f'local (api: {r.error})'
        return lr

    def risk(self, req: RiskRequest) -> ToolResult:
        body = req.model_dump()
        return self._with_local(_timed('risk', body, lambda: RiskResponse(**self._req('POST', '/risk', body))),
                                lambda: LocalTools().risk(req))

    def sweep(self, req: SweepRequest) -> ToolResult:
        body = req.model_dump(exclude_none=True)
        return self._with_local(_timed('sweep', body, lambda: SweepResponse(**self._req('POST', '/sweep', body))),
                                lambda: LocalTools().sweep(req))

    def effects(self, variable: str) -> ToolResult:
        return self._with_local(_timed('effects', {'variable': variable}, lambda: EffectsResponse(**self._req('GET', f'/effects/{variable}'))),
                                lambda: LocalTools().effects(variable))


def get_tools():
    s = get_settings()
    if s.tools_mode == 'http' and s.api_base_url:
        return HttpTools(s.api_base_url, s.assistant_api_token)
    return LocalTools()
