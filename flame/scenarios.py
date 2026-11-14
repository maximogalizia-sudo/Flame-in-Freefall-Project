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
