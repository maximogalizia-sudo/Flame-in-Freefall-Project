"""Prediction side of LC's models (docs/DOCUMENTO_TECNICO.md §7.2): predict, coverage, explain.

Loads the JSON models written by `flame/train.py` and the curated tables in `data/processed/`. Only numpy/pandas.
Honesty rules built in (NOTAS.md, Decidido 2026-11-14):
  * "We don't know" by COMBINATION: a query gets a number only if NASA ran clean tests with the same fuel, the same
    suppressor/diluent and the same pressure level, at a similar O2 (and suppressor dose). Otherwise probability=None,
    the reasons are listed and the closest real tests are returned.
  * FLEX: the main estimate is "if the droplet does not break up"; the break-up risk and the Disruption sensitivity
    range are reported next to it (DD-022). 2-3 atm is evidence only (DD-024).
  * Solids (BASS / BASS-II) have no predictive model (DD-033): evidence only.
  * Inputs the user did not give are filled with the typical tested value and listed in `assumptions`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .contracts import Conditions, EvidenceItem, Prediction

ROOT = Path(__file__).resolve().parents[1]
MODELS, PROC = ROOT / 'models', ROOT / 'data' / 'processed'
VERSION = 'lc-models-v1'
ATM_KPA, BAR_KPA = 101.325, 100.0
FLEX_FUELS = {'methanol': 'Methanol', 'heptane': 'Heptane', 'n_heptane': 'Heptane', 'n-heptane': 'Heptane'}
CFIG_FUELS = {'ethane': 'ethane', 'propane': 'propane', 'butane': 'butane', 'n_butane': 'butane', 'n-butane': 'butane'}
SOLIDS = {'pmma': ['PMMA sphere', 'PMMA slab', 'PMMA rod', 'PMMA thin film'], 'acrylic': ['PMMA sphere', 'PMMA slab', 'PMMA rod', 'PMMA thin film'],
          'sibal': ['SIBAL fabric'], 'cotton': ['SIBAL fabric'], 'fabric': ['SIBAL fabric'], 'nomex': ['Nomex'],
          'ultem': ['Ultem'], 'wax': ['wax'], 'candle': ['wax']}


@lru_cache
def model(name: str) -> dict:
    return json.loads((MODELS / f'{name}.json').read_text())


@lru_cache
def table(name: str) -> pd.DataFrame:
    return pd.read_parquet(PROC / f'{name}.parquet')


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _z(m: dict, x: dict) -> np.ndarray:
    return (np.array([x[f] for f in m['features']]) - np.array(m['mu'])) / np.array(m['sd'])


def _logit_predict(m: dict, x: dict):
    z = _z(m, x)
    p = float(_sigmoid(m['intercept'] + z @ np.array(m['coef'])))
    boot = np.array(m['boot'])
    pb = _sigmoid(boot[:, 0] + boot[:, 1:] @ z)
    contrib = {f: round(float(c * zi), 3) for f, c, zi in zip(m['features'], m['coef'], z)}
    return p, float(np.percentile(pb, 2.5)), float(np.percentile(pb, 97.5)), contrib


def _cox_predict(m: dict, x: dict, horizon: float):
    z = _z(m, x)
    k = int(np.clip(round(horizon), 0, len(m['grid_s']) - 1))
    p = 1 - float(np.exp(-m['H0'][k] * np.exp(z @ np.array(m['coef']))))
    bc, bh = np.array(m['boot_coef']), np.array(m['boot_H0'])[:, k]
    pb = 1 - np.exp(-bh * np.exp(bc @ z))
    contrib = {f: round(float(c * zi), 3) for f, c, zi in zip(m['features'], m['coef'], z)}   # log-hazard vs average flame
    return p, float(np.percentile(pb, 2.5)), float(np.percentile(pb, 97.5)), contrib


def _family(c: Conditions) -> str | None:
    if c.family:
        return c.family
    f = (c.fuel or c.material or '').lower()
    if f in FLEX_FUELS: return 'flex'
    if f in CFIG_FUELS: return 'cfig'
    if f in SOLIDS: return 'solids'
    return None


# ----------------------------------------------------------------------------- FLEX
def _flex_level(p_kpa: float | None) -> str | None:
    if p_kpa is None: return '1 atm'
    atm = p_kpa / ATM_KPA
    if 0.6 <= atm < 0.8: return '0.7 atm'
    if 0.8 <= atm < 1.2: return '1 atm'
    if 1.9 <= atm <= 3.2: return '2-3 atm'
    return None


def _flex_nearest(d: pd.DataFrame, o2, co2, he, k=5):
    dist = np.sqrt(((d['o2_frac'] - o2) / 0.02) ** 2 + ((d['co2_frac'] - co2) / 0.05) ** 2 + ((d['he_frac'] - he) / 0.05) ** 2)
    return d.assign(distance=dist).sort_values('distance').head(k)


def predict_flex(c: Conditions) -> list[Prediction]:
    t = table('flex_tests'); mm, mb = model('flex_main'), model('flex_breakup')
    fuel = FLEX_FUELS.get((c.fuel or '').lower())
    o2, co2, he = c.o2_frac, c.co2_frac or 0.0, c.he_frac or 0.0
    assumptions, ood = [], []
    if c.pressure_kpa is None: assumptions.append('pressure = 1 atm (not given)')
    if c.support is None: assumptions.append('free-floating droplet (support not given)')
    if fuel is None: ood.append('FLEX tested only methanol and n-heptane droplets')
    if o2 is None: ood.append('oxygen fraction is needed')
    if c.gravity_g not in (None, 0): ood.append(f'gravity_g={c.gravity_g}: NASA data are microgravity only')
    if co2 > 0 and he > 0: ood.append('CO2 and helium were never used together in FLEX')
    level = _flex_level(c.pressure_kpa)
    if level is None: ood.append(f'pressure {c.pressure_kpa:g} kPa: FLEX tested ~0.7, 1 and 2-3 atm only')
    elif level == '2-3 atm': ood.append('2-3 atm: only 7 near-identical clean tests, shown as evidence only (DD-024)')
    if ood or fuel is None:
        return [_flex_unknown(c, ood, assumptions, t, fuel, o2 or 0.21, co2, he)]
    diluent = 'CO2' if co2 > 0 else ('He' if he > 0 else 'N2 only')
    same = t[t['in_main_model'] & (t['fuel'] == fuel) & (t['diluent'] == diluent) & (t['p_level'] == level)]
    sup = co2 if diluent == 'CO2' else he
    supcol = 'co2_frac' if diluent == 'CO2' else 'he_frac'
    near = same[(same['o2_frac'] - o2).abs() <= 0.025]
    if diluent != 'N2 only':
        near = near[(near[supcol] - sup).abs() <= 0.07]
    if near.empty:
        rng = f"{same['o2_frac'].min():.2f}-{same['o2_frac'].max():.2f}" if len(same) else 'none'
        ood.append(f'no clean NASA test with {fuel}, {diluent}, {level} near O2={o2:.2f}'
                   + (f' and {diluent}={sup:.2f}' if diluent != 'N2 only' else '') + f' (tested O2 range for this combination: {rng})')
        return [_flex_unknown(c, ood, assumptions, t, fuel, o2, co2, he)]
    d0 = c.droplet_d0_mm or float(t.loc[t['in_main_model'] & (t['fuel'] == fuel), 'd0_mm'].median())
    if c.droplet_d0_mm is None: assumptions.append(f'initial droplet diameter = {d0:.1f} mm (typical for {fuel})')
    lo_d, hi_d = same['d0_mm'].min(), same['d0_mm'].max()
    if not (lo_d - 0.2 <= d0 <= hi_d + 0.2):
        ood.append(f'droplet diameter {d0:.2f} mm outside the tested sizes for this combination ({lo_d:.2f}-{hi_d:.2f} mm)')
        return [_flex_unknown(c, ood, assumptions, t, fuel, o2, co2, he)]
    x = {'o2_frac': o2, 'co2_frac': co2, 'he_frac': he, 'd0_mm': d0, 'heptane': float(fuel == 'Heptane'),
         'fiber': float(c.support == 'fiber'), 'p_0p7atm': float(level == '0.7 atm')}
    for k, v in mm['interaction_centers'].items():
        x[f'heptane_x_{k}'] = x['heptane'] * (x[k] - v)
    p, lo, hi, contrib = _logit_predict(mm, x)
    sens = []
    for s in mm['sensitivity'].values():
        z = (np.array([x[f] for f in mm['features']]) - np.array(s['mu'])) / np.array(s['sd'])
        sens.append(float(_sigmoid(s['intercept'] + z @ np.array(s['coef']))))
    pb, blo, bhi, bcontrib = _logit_predict(mb, x)
    notes = []
    if o2 > 0.24: notes.append('High-O2 zone (above 24 %): little clean evidence, most tests there ended in droplet break-up')
    if min(sens) < 0.5 < max(sens): notes.append('Counting the interrupted tests differently changes the conclusion: treat the estimate with care')
    nearest_ids = _flex_nearest(same, o2, co2, he)['flex_id'].tolist()
    return [
        Prediction(family='flex', target=mm['target'], probability=round(p, 3), ci_low=round(lo, 3), ci_high=round(hi, 3),
                   in_domain=True, out_of_domain=[], contributions=contrib, nearest_tests=nearest_ids,
                   model_version=f"{VERSION}:flex_main", range_sensitivity=[round(min(sens), 3), round(max(sens), 3)],
                   assumptions=assumptions, notes=notes),
        Prediction(family='flex', target=mb['target'], probability=round(pb, 3), ci_low=round(blo, 3), ci_high=round(bhi, 3),
                   in_domain=True, out_of_domain=[], contributions=bcontrib, nearest_tests=nearest_ids,
                   model_version=f"{VERSION}:flex_breakup", assumptions=assumptions,
                   notes=['Possible experiment problem (contaminated needles, NASA/TP-2015-216046 §5.2), not a property of the flame']),
    ]


def _flex_unknown(c, ood, assumptions, t, fuel, o2, co2, he):
    pool = t[t['fuel'] == fuel] if fuel else t
    ids = _flex_nearest(pool, o2, co2, he)['flex_id'].tolist()
    return Prediction(family='flex', target='extinction_before_burnout', probability=None, ci_low=None, ci_high=None,
                      in_domain=False, out_of_domain=ood, contributions={}, nearest_tests=ids,
                      model_version=f'{VERSION}:flex_main', assumptions=assumptions,
                      notes=['No estimate is given outside the tested combinations; the closest real NASA tests are listed'])


# ----------------------------------------------------------------------------- CFI-G
def predict_cfig(c: Conditions) -> list[Prediction]:
    t = table('cfig_tests')
    fuel = CFIG_FUELS.get((c.fuel or '').lower())
    ft = c.flame_type or 'normal'
    assumptions, ood = [], []
    if c.flame_type is None: assumptions.append('normal flame: gas flowing into an oxygen atmosphere (flame type not given)')
    horizon = c.horizon_s or 30.0
    if c.horizon_s is None: assumptions.append('horizon = 30 s')
    if fuel is None: ood.append('CFI-G tested only ethane, propane and n-butane')
    if c.o2_frac is None: ood.append('oxygen fraction is needed')
    if c.gravity_g not in (None, 0): ood.append(f'gravity_g={c.gravity_g}: NASA data are microgravity only')
    if c.co2_frac or c.he_frac: ood.append('CFI-G did not use CO2 or helium')
    p_bar = (c.pressure_kpa / BAR_KPA) if c.pressure_kpa else 1.013
    if c.pressure_kpa is None: assumptions.append('pressure = 1 atm (not given)')
    d = t[(t['flame_type'] == ft) & t['ext_model'] & (t['fuel'] == fuel)] if fuel else t.iloc[:0]
    if not ood:
        ok_p = d[(d['pressure_bar'] - p_bar).abs() <= 0.25]
        ok = ok_p[(ok_p['o2_mid'] - c.o2_frac).abs() <= (0.04 if ft == 'normal' else 0.08)]
        if ok.empty:
            ood.append(f'no NASA {ft} {fuel} flame near {p_bar:.2f} bar and O2={c.o2_frac:.2f} '
                       f'(tested pressures: {sorted(d["pressure_bar"].round(2).unique().tolist())}; '
                       f'tested O2: {sorted((d["o2_mid"] * 100).round().astype(int).unique().tolist())} %)')
    if horizon > 150: ood.append('horizon above 150 s: few flames were followed that long')
    pool = d if len(d) else t[t['flame_type'] == ft]
    dist = np.sqrt(((pool['o2_mid'] - (c.o2_frac or 0.21)) / 0.04) ** 2 + ((pool['pressure_bar'] - p_bar) / 0.5) ** 2)
    nearest = pool.assign(distance=dist).sort_values('distance').head(5)['test_id'].tolist()
    m = model(f'cfig_selfext_{ft}')
    if ood:
        return [Prediction(family='cfig', target=f'self_extinction_within_{horizon:g}s', probability=None, ci_low=None, ci_high=None,
                           in_domain=False, out_of_domain=ood, contributions={}, nearest_tests=nearest,
                           model_version=f"{VERSION}:{m['name']}", assumptions=assumptions)]
    flow = c.fuel_flow_mg_s or float(d['fuel_flow_mg_s'].median())
    if c.fuel_flow_mg_s is None: assumptions.append(f'fuel flow = {flow:.2f} mg/s (typical tested flame size)')
    x = {'o2_pct': c.o2_frac * 100, 'pressure_bar': p_bar, 'fuel_flow_mg_s': flow,
         'ethane': float(fuel == 'ethane'), 'propane': float(fuel == 'propane')}
    if ft == 'normal':
        x['jet_fuel_frac'] = c.jet_fuel_frac if c.jet_fuel_frac is not None else float(d['jet_fuel_frac'].median())
    else:
        x['atm_fuel_pct'] = (c.atm_fuel_frac if c.atm_fuel_frac is not None else float(d['atm_fuel_frac'].median())) * 100
    p, lo, hi, contrib = _cox_predict(m, x, horizon)
    out = [Prediction(family='cfig', target=f'self_extinction_within_{horizon:g}s', probability=round(p, 3), ci_low=round(lo, 3),
                      ci_high=round(hi, 3), in_domain=True, out_of_domain=[], contributions=contrib, nearest_tests=nearest,
                      model_version=f"{VERSION}:{m['name']}", assumptions=assumptions,
                      notes=['Contributions are log-hazard: positive = goes out sooner',
                             'Flames stopped by the operator were counted as "still burning at that time", not as "never goes out"'])]
    if ft == 'normal' and c.jet_fuel_frac is not None:
        mi = model('cfig_ignition')
        pi, ilo, ihi, icon = _logit_predict(mi, {'log10_jet_fuel_frac': float(np.log10(max(c.jet_fuel_frac, 0.005)))})
        out.append(Prediction(family='cfig', target='ignition', probability=round(pi, 3), ci_low=round(ilo, 3), ci_high=round(ihi, 3),
                              in_domain=0.005 <= c.jet_fuel_frac <= 1, out_of_domain=[], contributions=icon, nearest_tests=nearest,
                              model_version=f"{VERSION}:cfig_ignition", notes=['Depends almost only on how diluted the fuel jet is']))
    return out


# ----------------------------------------------------------------------------- public API
def predict(c: Conditions) -> list[Prediction]:
    fam = _family(c)
    if fam == 'flex': return predict_flex(c)
    if fam == 'cfig': return predict_cfig(c)
    if fam == 'solids':
        return [Prediction(family='solids', target='no_model', probability=None, ci_low=None, ci_high=None, in_domain=False,
                           out_of_domain=['Solid materials (BASS/BASS-II) are shown as NASA evidence, not as a model (DD-033)']
                           + (['O2 above 22.7 %: no solid-material tests'] if (c.o2_frac or 0) > 0.227 else []),
                           contributions={}, nearest_tests=[e.test_id for e in coverage(c, k=5)], model_version=f'{VERSION}:evidence')]
    return []


def coverage(c: Conditions, k: int = 10) -> list[EvidenceItem]:
    """Closest real NASA tests for the conditions (all families, including solids)."""
    fam = _family(c); o2 = c.o2_frac or 0.21; items = []
    if fam in (None, 'flex'):
        t = table('flex_tests'); fuel = FLEX_FUELS.get((c.fuel or '').lower())
        pool = t[t['fuel'] == fuel] if fuel else t
        for _, r in _flex_nearest(pool, o2, c.co2_frac or 0, c.he_frac or 0, k).iterrows():
            items.append(EvidenceItem(test_id=r['flex_id'], psi_id='PSI-69', distance=round(float(r['distance']), 3), outcome=r['test_end'],
                                      source=f"FLEX test {r['flex_no']} — {r['source']}"))
    if fam in (None, 'cfig'):
        t = table('cfig_tests'); fuel = CFIG_FUELS.get((c.fuel or '').lower())
        pool = t[t['fuel'] == fuel] if fuel else t
        p_bar = (c.pressure_kpa / BAR_KPA) if c.pressure_kpa else 1.013
        dist = np.sqrt(((pool['o2_mid'] - o2) / 0.04) ** 2 + ((pool['pressure_bar'] - p_bar) / 0.5) ** 2)
        for _, r in pool.assign(distance=dist).sort_values('distance').head(k).iterrows():
            outcome = r['end'] if r['ignited'] else 'no_ignition'
            items.append(EvidenceItem(test_id=r['test_id'], psi_id='PSI-159', distance=round(float(r['distance']), 3), outcome=outcome,
                                      source=f"CFI-G {r['flame_type']} flames, test {r['test_id']} — {r['source']}"))
    if fam in (None, 'solids'):
        t = table('bass_tests'); fams = SOLIDS.get((c.material or '').lower())
        pool = t[t['material'].isin(fams)] if fams else t
        dist = ((pool['o2_pct'] - o2 * 100).abs() / 2).fillna(99)
        for _, r in pool.assign(distance=dist).sort_values('distance').head(k).iterrows():
            items.append(EvidenceItem(test_id=r['test_id'], psi_id='PSI-25' if r['experiment'] == 'BASS-II' else 'PSI-26',
                                      distance=round(float(r['distance']), 3), outcome=r['outcome_group'],
                                      source=f"{r['experiment']} ({r['material']}) — {r['source']}. NASA note: {str(r['observation'])[:200]}"))
    return sorted(items, key=lambda e: e.distance)[:k] if fam is None else items[:k]


def explain(family: str) -> dict:
    names = {'flex': ['flex_main', 'flex_breakup'], 'cfig': ['cfig_selfext_normal', 'cfig_selfext_inverse', 'cfig_ignition']}.get(family, [])
    out = {'family': family, 'model_version': VERSION, 'models': []}
    for n in names:
        m = model(n)
        coef = dict(zip(m['features'], np.round(m['coef'], 3).tolist()))
        unit = 'log-odds per 1 SD of the input' if m['kind'] == 'logistic' else 'log-hazard per 1 SD of the input (positive = goes out sooner)'
        out['models'].append({'name': n, 'target': m['target'], 'target_text': m['target_text'], 'kind': m['kind'],
                              'coefficients': coef, 'coefficient_units': unit,
                              'input_sd': dict(zip(m['features'], np.round(m['sd'], 4).tolist())),
                              'cv_metrics': m['cv'], 'n': m['n'], 'training_ranges': m.get('training_ranges'), 'data': m.get('data')})
    if family == 'solids':
        s = pd.read_csv(PROC / 'bass_material_summary.csv')
        out['evidence_summary'] = s.to_dict('records')
        out['note'] = 'No predictive model for solids (DD-033): share of tests without a sustained flame, by material'
    return out
