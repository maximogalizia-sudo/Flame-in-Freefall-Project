"""Mentors' feedback (2026-11-14): early-warning risk indicator, hypothetical scenarios and per-variable explanations.

All three are built ONLY from the existing models (flame.models) and the curated data: no new modelling.
  risk(req)     -> traffic light per scenario (droplets, gas flames, solid materials) for one atmosphere.
  sweep(req)    -> "what if": vary one input and get the curve, with the points NASA never tested left empty.
  effects(var)  -> how one variable acts (direction, size with interval, interactions), where it was tested,
                   what was never tested and what cannot be separated.
Honesty: the risk indicator comes from microgravity laboratory experiments; it is NOT a certified fire alarm.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import (Conditions, EffectRow, EffectsResponse, RiskItem, RiskRequest, RiskResponse,
                        SweepPoint, SweepRequest, SweepResponse)
from .models import VERSION, model, predict, table

DISCLAIMER = ('Early-warning indicator built from NASA microgravity laboratory experiments (droplets, gas flames, small '
              'material samples). It shows tendencies and where data exist; it is not a certified fire alarm for a real cabin.')
ORDER = {'green': 0, 'yellow': 1, 'red': 2}


def _level(persistence: float, hi: float | None) -> str:
    if persistence > 0.5:
        return 'red'
    if persistence >= 0.2 or (hi is not None and hi >= 0.5):
        return 'yellow'
    return 'green'


def _model_item(name: str, family: str, c: Conditions) -> RiskItem:
    p = predict(c)[0]
    if p.probability is None:
        return RiskItem(scenario=name, family=family, level='gray', persistence=None, ci_low=None, ci_high=None,
                        in_domain=False, reasons=p.out_of_domain or ['not tested'], nearest_tests=p.nearest_tests[:3])
    pers, lo, hi = 1 - p.probability, 1 - p.ci_high, 1 - p.ci_low
    lvl = _level(pers, hi)
    what = 'keeps burning (does not go out by itself)' if family == 'cfig' else 'burns the droplet completely (flame does not go out)'
    reasons = [f'probability that the flame {what}: {pers:.0%} (95 % interval {lo:.0%}-{hi:.0%})']
    reasons += [n for n in p.notes if n.startswith(('Moderate', 'High-O2', 'For methanol'))]
    return RiskItem(scenario=name, family=family, level=lvl, persistence=round(pers, 3), ci_low=round(lo, 3),
                    ci_high=round(hi, 3), in_domain=True, reasons=reasons, nearest_tests=p.nearest_tests[:3])


def _solid_item(name: str, material: str, req: RiskRequest) -> RiskItem:
    o2 = req.o2_frac * 100
    why = []
    if o2 > 22.7: why.append(f'NASA tested solid materials only up to 22.7 % O2 (asked {o2:.1f} %)')
    if not 95 <= req.pressure_kpa <= 105: why.append('NASA tested solid materials only at cabin pressure (~1 atm)')
    if req.co2_frac or req.he_frac: why.append('NASA did not test solid materials with CO2 or helium added')
    t = table('bass_tests')
    d = t[(t['material'] == material) & ~t['hardware_issue'] & (t['outcome_group'] != 'unclear') & ((t['o2_pct'] - o2).abs() <= 2)]
    if not why and len(d) < 3:
        why.append(f'fewer than 3 usable NASA tests of {material} within 2 points of {o2:.1f} % O2')
    if why:
        return RiskItem(scenario=name, family='solids', level='gray', persistence=None, ci_low=None, ci_high=None,
                        in_domain=False, reasons=why, nearest_tests=d['test_id'].head(3).tolist())
    k_no, n = int((d['outcome_group'] == 'no_flame').sum()), len(d)
    share = k_no / n
    lvl = 'green' if share >= 0.75 else ('red' if share <= 0.25 else 'yellow')
    return RiskItem(scenario=name, family='solids', level=lvl, persistence=None, ci_low=None, ci_high=None, in_domain=True,
                    reasons=[f'NASA evidence (no model): {material} ignited and sustained a flame in {n - k_no} of {n} tests '
                             f'near {o2:.0f} % O2 (in the rest no sustained flame)'],
                    nearest_tests=d['test_id'].head(3).tolist())


def risk(req: RiskRequest) -> RiskResponse:
    base = dict(o2_frac=req.o2_frac, pressure_kpa=req.pressure_kpa, co2_frac=req.co2_frac or None, he_frac=req.he_frac or None)
    items = [_model_item('methanol droplet', 'flex', Conditions(fuel='methanol', **base)),
             _model_item('n-heptane droplet', 'flex', Conditions(fuel='heptane', **base))]
    for fuel in ['ethane', 'propane', 'butane']:
        items.append(_model_item(f'{fuel} gas flame', 'cfig', Conditions(fuel=fuel, horizon_s=req.horizon_s, **base)))
    for name, mat in [('cotton/fiberglass fabric (SIBAL)', 'SIBAL fabric'), ('Nomex (crew suits)', 'Nomex'), ('PMMA (acrylic) sphere', 'PMMA sphere')]:
        items.append(_solid_item(name, mat, req))
    known = [i for i in items if i.level != 'gray']
    level = max(known, key=lambda i: ORDER[i.level]).level if known else 'gray'
    n_red = sum(i.level == 'red' for i in known); n_gray = len(items) - len(known)
    headline = (f'{n_red} of {len(known)} scenarios with NASA data tend to keep burning at this atmosphere'
                if known else 'NASA never tested anything close to this atmosphere')
    if known and n_gray:
        headline += f'; {n_gray} scenario(s) were never tested here (unknown, not safe)'
    return RiskResponse(atmosphere=req, level=level, items=items, headline=headline + '.', disclaimer=DISCLAIMER,
                        model_version=f'{VERSION}:risk')


GRIDS = {'o2_frac': np.round(np.arange(0.10, 0.405, 0.01), 3), 'co2_frac': np.round(np.arange(0, 0.501, 0.025), 3),
         'he_frac': np.round(np.arange(0, 0.601, 0.05), 3), 'pressure_kpa': np.arange(50, 311, 10.0),
         'droplet_d0_mm': np.round(np.arange(1.0, 5.01, 0.2), 2), 'horizon_s': np.arange(5, 151, 5.0)}


def sweep(req: SweepRequest) -> SweepResponse:
    values = req.values or GRIDS[req.variable].tolist()
    pts, target, notes = [], None, []
    for x in values:
        c = req.conditions.model_copy(update={req.variable: (x if x != 0 or req.variable not in ('co2_frac', 'he_frac') else None)})
        ps = predict(c)
        if not ps:
            continue
        p = ps[0]; target = target or p.target
        pts.append(SweepPoint(x=float(x), probability=p.probability, ci_low=p.ci_low, ci_high=p.ci_high, in_domain=p.probability is not None))
        notes += [n for n in p.notes if n not in notes and not n.startswith('Contributions')]
    xs = [pt.x for pt in pts if pt.in_domain]
    return SweepResponse(variable=req.variable, target=target or 'unknown', points=pts,
                         tested_range=[min(xs), max(xs)] if xs else None, notes=notes[:4], model_version=f'{VERSION}:sweep')


# ----------------------------------------------------------------------------- effects
FLEX_STEPS = {'o2_frac': ('o2_frac', 0.01, '+1 point of O2'), 'co2_frac': ('co2_frac', 0.05, '+5 points of CO2'),
              'he_frac': ('he_frac', 0.05, '+5 points of helium'), 'droplet_d0_mm': ('d0_mm', 1.0, '+1 mm initial droplet size'),
              'pressure_kpa': ('p_0p7atm', 1.0, 'at 0.7 atm instead of 1 atm'), 'support': ('fiber', 1.0, 'on a fiber instead of free')}
CFIG_STEPS = {'o2_frac': ('o2_pct', 1.0, '+1 point of O2'), 'pressure_kpa': ('pressure_bar', 1.0, '+1 bar'),
              'fuel_flow_mg_s': ('fuel_flow_mg_s', 0.5, '+0.5 mg/s fuel flow (bigger flame)')}


def _ratio_row(scenario, label, m, feats, step, boot_key, kind):
    sd = dict(zip(m['features'], m['sd'])); coef = dict(zip(m['features'], m['coef']))
    idx = {f: k for k, f in enumerate(m['features'])}
    boot = np.array(m[boot_key]); boot = boot[:, 1:] if kind == 'logistic' else boot
    b = sum(coef[f] / sd[f] for f in feats) * step
    bb = sum(boot[:, idx[f]] / sd[f] for f in feats) * step
    r, lo, hi = float(np.exp(b)), float(np.exp(np.percentile(bb, 2.5))), float(np.exp(np.percentile(bb, 97.5)))
    direction = 'helps extinction' if lo > 1 else ('keeps it burning' if hi < 1 else 'no clear effect')
    return EffectRow(scenario=scenario, effect=label, ratio=round(r, 2), ci_low=round(lo, 2), ci_high=round(hi, 2), direction=direction)


def effects(variable: str) -> EffectsResponse:
    rows, tested, not_tested, caveats = [], [], [], []
    mf = model('flex_main')
    if variable in FLEX_STEPS:
        feat, step, label = FLEX_STEPS[variable]
        rows.append(_ratio_row('methanol droplets', label, mf, [feat], step, 'boot', 'logistic'))
        inter = f'heptane_x_{feat}'
        rows.append(_ratio_row('n-heptane droplets', label, mf, [feat] + ([inter] if inter in mf['features'] else []), step, 'boot', 'logistic'))
    if variable in CFIG_STEPS:
        feat, step, label = CFIG_STEPS[variable]
        for ft in ['normal', 'inverse']:
            mc = model(f'cfig_selfext_{ft}')
            if feat in mc['features']:
                scen = 'normal gas flames' if ft == 'normal' else 'inverse gas flames (O2 = burner jet, not the cabin air)'
                rows.append(_ratio_row(scen, label, mc, [feat], step, 'boot_coef', 'cox'))
        caveats.append('Gas flames: the ratio is how much faster the flame goes out by itself (hazard ratio); inverse flames have moderate confidence')
    f = table('flex_tests'); f = f[f['in_main_model']]
    col = {'pressure_kpa': 'pressure_kpa', 'droplet_d0_mm': 'd0_mm'}.get(variable, variable)
    if col in f.columns:
        for fuel, g in f.groupby('fuel'):
            g = g[g[col] > 0] if variable in ('co2_frac', 'he_frac') else g
            if len(g):
                tested.append(f'{fuel} droplets: {variable} {g[col].min():g}-{g[col].max():g} '
                              f'(O2 {g["o2_frac"].min():.2f}-{g["o2_frac"].max():.2f}), {len(g)} clean tests')
    if variable in ('co2_frac', 'he_frac'):
        gas = 'CO2' if variable == 'co2_frac' else 'helium'
        with_gas = f[f[variable] > 0]
        not_tested.append(f'{gas} was never tested above {with_gas["o2_frac"].max():.0%} O2 (e.g. the 30-34 % exploration atmosphere)')
        not_tested.append('CO2 and helium were never used together')
        not_tested.append(f'{gas} was not used in the gas-flame (CFI-G) or solid-material (BASS) experiments')
        if variable == 'co2_frac':
            caveats.append('For methanol the CO2 effect cannot be separated from O2: NASA lowered O2 while raising CO2 in the same tests')
    if variable == 'o2_frac':
        not_tested.append('Solid materials were never tested above 22.7 % O2; droplet tests above 24 % O2 mostly ended in droplet break-up')
    if variable == 'pressure_kpa':
        caveats.append('Droplets: 2-3 atm has only 7 near-identical tests and is shown as evidence only (DD-024)')
    if not rows:
        caveats.append(f'No model uses {variable}; answer from NASA evidence and reports only')
    return EffectsResponse(variable=variable, rows=rows, tested=tested, not_tested=not_tested, caveats=caveats,
                           model_version=f'{VERSION}:effects')


# ----------------------------------------------------------------------------- duration (v4)
def duration(c: Conditions, horizon_s: int = 300, step_s: int = 5) -> 'DurationResponse':
    """How long the flame lasts. Gas flames: full survival curve from the Cox model (+ median); droplets: no duration
    model (out of scope, DOCUMENTO_TECNICO 11.1) -> observed burn times of the closest clean NASA tests."""
    from .contracts import DurationPoint, DurationResponse
    from .models import _family, model as get_model
    fam = _family(c)
    preds = predict(c)
    p = preds[0] if preds else None
    if fam == 'cfig' and p is not None:
        observed = []
        t = table('cfig_tests')
        for tid in p.nearest_tests[:5]:
            r = t[t['test_id'] == tid].iloc[0]
            observed.append({'test_id': tid, 'o2_pct': round(float(r['o2_mid']) * 100, 1), 'pressure_bar': round(float(r['pressure_bar']), 2),
                             'fuel_flow_mg_s': float(r['fuel_flow_mg_s']), 'burn_time_s': None if pd.isna(r['burn_time_s']) else float(r['burn_time_s']),
                             'ended': 'went out by itself' if r['end'] == 'self_ext' else ('still burning when cut' if r['end'] == 'flow_term' else 'did not ignite')})
        if p.probability is None:
            return DurationResponse(family=fam, in_domain=False, reasons=p.out_of_domain, observed=observed,
                                    notes=['No curve: NASA did not test this combination; the closest real flames are listed.'],
                                    model_version=f'{VERSION}:duration')
        ft = c.flame_type or 'normal'
        m = get_model(f'cfig_selfext_{ft}')
        tt = table('cfig_tests'); d = tt[(tt['flame_type'] == ft) & tt['ext_model'] & (tt['fuel'] == c.fuel)]
        x = {'o2_pct': c.o2_frac * 100, 'pressure_bar': (c.pressure_kpa or 101.3) / 100,
             'fuel_flow_mg_s': c.fuel_flow_mg_s or float(d['fuel_flow_mg_s'].median()),
             'ethane': float(c.fuel == 'ethane'), 'propane': float(c.fuel == 'propane')}
        if ft == 'normal':
            x['jet_fuel_frac'] = c.jet_fuel_frac if c.jet_fuel_frac is not None else float(d['jet_fuel_frac'].median())
        else:
            x['atm_fuel_pct'] = (c.atm_fuel_frac if c.atm_fuel_frac is not None else float(d['atm_fuel_frac'].median())) * 100
        curve, median = [], None
        # vectorised over time (same formulas as models._cox_predict, computed once for the whole curve)
        from .models import _z
        z = _z(m, x)
        risk_pt = float(np.exp(z @ np.array(m['coef'])))
        risk_bt = np.exp(np.array(m['boot_coef']) @ z)
        H0, bH0 = np.array(m['H0']), np.array(m['boot_H0'])
        for ts in range(0, horizon_s + 1, step_s):
            k = int(np.clip(round(max(ts, 0.5)), 0, len(m['grid_s']) - 1))
            q = 1 - float(np.exp(-H0[k] * risk_pt))
            pb = 1 - np.exp(-bH0[:, k] * risk_bt)
            lo, hi = float(np.percentile(pb, 2.5)), float(np.percentile(pb, 97.5))
            curve.append(DurationPoint(t_s=ts, still_burning=round(1 - q, 3), ci_low=round(1 - hi, 3), ci_high=round(1 - lo, 3)))
        for ts in range(1, horizon_s + 1):      # median at 1 s resolution (same number the assistant's predict note gives)
            k = int(np.clip(ts, 0, len(m['grid_s']) - 1))
            if 1 - np.exp(-H0[k] * risk_pt) >= 0.5:
                median = float(ts)
                break
        notes = ['Curve from the gas-flame survival model (flames cut by the operator count as still burning at that time).']
        if median is None:
            notes.append(f'More than half of such flames are still burning at {horizon_s} s.')
        if ft == 'inverse':
            notes.append('Moderate confidence (inverse-flame model).')
        return DurationResponse(family=fam, in_domain=True, median_s=median, curve=curve, observed=observed, notes=notes,
                                model_version=f'{VERSION}:duration')
    if fam == 'flex':
        f = table('flex_tests'); f = f[f['clean'] & f['burn_time_s'].notna()]
        fuel = 'Heptane' if (c.fuel or '').startswith(('hept', 'n_hept', 'n-hept')) else 'Methanol'
        pool = f[f['fuel'] == fuel]
        dist = np.sqrt(((pool['o2_frac'] - (c.o2_frac or 0.21)) / 0.02) ** 2 + ((pool['co2_frac'] - (c.co2_frac or 0)) / 0.05) ** 2
                       + ((pool['he_frac'] - (c.he_frac or 0)) / 0.05) ** 2)
        near = pool.assign(dd=dist).sort_values('dd').head(6)
        observed = [{'test_id': r['flex_id'], 'o2_pct': round(r['o2_frac'] * 100, 1), 'co2_pct': round(r['co2_frac'] * 100, 1),
                     'he_pct': round(r['he_frac'] * 100, 1), 'd0_mm': None if pd.isna(r['d0_mm']) else float(r['d0_mm']),
                     'burn_time_s': float(r['burn_time_s']), 'ended': 'flame went out' if r['extinct'] else 'droplet burned completely'}
                    for _, r in near.iterrows()]
        return DurationResponse(family=fam, in_domain=bool(p and p.probability is not None), reasons=(p.out_of_domain if p else []),
                                observed=observed, notes=['Droplets: no duration model (out of scope for lack of time); these are the '
                                                          'observed burn times of the closest clean NASA tests.'],
                                model_version=f'{VERSION}:duration')
    return DurationResponse(family=fam or 'unknown', in_domain=False, reasons=['No duration data for this family (solids: the air '
                            'flow was changed during the tests)'], model_version=f'{VERSION}:duration')


# ----------------------------------------------------------------------------- compare (v5)
VAR_LABEL = {'o2_frac': ('O2', 100, '%'), 'pressure_kpa': ('pressure', 1, 'kPa'), 'co2_frac': ('CO2', 100, '%'),
             'he_frac': ('helium', 100, '%'), 'droplet_d0_mm': ('droplet size', 1, 'mm'), 'fuel_flow_mg_s': ('fuel flow', 1, 'mg/s')}
SAME = 0.05   # changes of persistence below 5 points are "about the same"


def _clean(c: Conditions) -> Conditions:
    """0 % CO2 / helium = not added (the models read None as 'none added')."""
    upd = {k: None for k in ('co2_frac', 'he_frac') if getattr(c, k) == 0}
    return c.model_copy(update=upd) if upd else c


def _side(c: Conditions, gas: bool):
    from .contracts import CompareSide
    p = predict(c)[0]
    if p.probability is None:
        return CompareSide(level='gray', persistence=None, ci_low=None, ci_high=None, in_domain=False,
                           reasons=p.out_of_domain or ['not tested'])
    pers, lo, hi = 1 - p.probability, 1 - p.ci_high, 1 - p.ci_low
    med = duration(c, horizon_s=300, step_s=5).median_s if gas else None
    return CompareSide(level=_level(pers, hi), persistence=round(pers, 3), ci_low=round(lo, 3), ci_high=round(hi, 3),
                       in_domain=True, median_s=med)


def _fmt(v, k):
    return f'{round((v or 0) * k, 1):g}'


def compare(req):
    """Planned experiment vs the same experiment with a change: both sides from the models, plus the effect of each
    changed variable on its own (one at a time, the rest as planned)."""
    from .contracts import ChangeRow, CompareResponse
    from .models import _family
    plan, chg = _clean(req.plan), _clean(req.change)
    fam = _family(plan)
    gas = fam == 'cfig'
    if gas:
        plan = plan.model_copy(update={'horizon_s': plan.horizon_s or 30})
        chg = chg.model_copy(update={'horizon_s': plan.horizon_s})
    fuel = plan.fuel or ''
    scenario = f'{fuel} gas flame' if gas else (f'{fuel} droplet' if fam == 'flex' else (plan.material or fam or 'unknown'))
    meaning = (f'chance the flame is still burning after {plan.horizon_s:g} s' if gas
               else 'chance the droplet burns completely, without the flame going out first')
    a, b = _side(plan, gas), _side(chg, gas)

    rows = []
    for var, (name, k, unit) in VAR_LABEL.items():
        v0, v1 = getattr(plan, var), getattr(chg, var)
        if (v0 or 0) == (v1 or 0):
            continue
        label = f'{name} {_fmt(v0, k)} {unit} -> {_fmt(v1, k)} {unit}'
        p1 = predict(_clean(plan.model_copy(update={var: v1})))[0]
        if p1.probability is None or a.persistence is None:
            rows.append(ChangeRow(variable=var, label=label, alone=None, delta=None, direction='unknown',
                                  text=f'{label}: NASA never tested this {name} with the rest of the plan, so its effect here is unknown.'))
            continue
        alone = round(1 - p1.probability, 3)
        dl = round(alone - a.persistence, 3)
        dirn = 'no clear effect' if abs(dl) < SAME else ('keeps it burning' if dl > 0 else 'helps extinction')
        rows.append(ChangeRow(variable=var, label=label, alone=alone, delta=dl, direction=dirn,
                              text=f'{label}: on its own, {a.persistence:.0%} -> {alone:.0%} '
                                   f'({"+" if dl >= 0 else ""}{dl * 100:.0f} points; {dirn}).'))

    notes = []
    if a.persistence is None or b.persistence is None:
        verdict = 'unknown'
        side, why = ('the planned experiment', a.reasons) if a.persistence is None else ('the changed experiment', b.reasons)
        headline = (f'NASA never tested {side} for a {scenario}: the models give no estimate there (unknown, not safe).'
                    + (f' {why[0]}.' if why else ''))
    else:
        d = b.persistence - a.persistence
        verdict = 'about the same' if abs(d) < SAME else ('worse' if d > 0 else 'better')
        if verdict == 'about the same' and gas and a.median_s and b.median_s and abs(b.median_s - a.median_s) >= max(10, 0.25 * a.median_s):
            verdict = 'worse' if b.median_s > a.median_s else 'better'    # same share at the horizon, but it lasts longer/shorter
        what = {'worse': 'more likely to keep burning' if abs(d) >= SAME else 'expected to last longer',
                'better': 'more likely to go out' if abs(d) >= SAME else 'expected to go out sooner',
                'about the same': 'about the same as planned'}[verdict]
        headline = f'With this change the {scenario} is {what}: {a.persistence:.0%} -> {b.persistence:.0%} ({meaning}).'
        if a.level != b.level:
            headline += f' The indicator moves from {a.level} to {b.level}.'
        if gas and a.median_s != b.median_s:
            f = lambda m: f'~{m:g} s' if m is not None else 'over 300 s'   # noqa: E731
            headline += f' Median time until it goes out by itself: {f(a.median_s)} -> {f(b.median_s)}.'
        overlap = not (b.ci_high < a.ci_low or b.ci_low > a.ci_high)
        if verdict != 'about the same' and overlap:
            notes.append('The 95 % intervals of the plan and the change overlap: the direction is what the models expect, '
                         'the size is uncertain.')
    if len(rows) > 1:
        notes.append('Each row changes only that variable; together they can add up differently (the models include interactions).')
    if fam == 'flex':
        notes.append('Droplets: the result is "burns completely" vs "goes out first"; there is no duration model for droplets.')
    return CompareResponse(scenario=scenario, family=fam or 'unknown', meaning=meaning, plan=a, change=b, verdict=verdict,
                           headline=headline, changes=rows, notes=notes, disclaimer=DISCLAIMER,
                           model_version=f'{VERSION}:compare')
