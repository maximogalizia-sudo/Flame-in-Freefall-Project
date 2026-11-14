"""What the ML system understood, in words the drafter (and a non-expert) can use (LC, 2026-11-14:
"the LLM has to interpret what the ML system understands, not the raw data").

`drivers(prediction)` turns each model's per-variable contributions (log-odds or log-hazard relative to an average
NASA test) into ranked, signed, named statements: which conditions push this case towards the flame going out and
which towards it keeping burning, and how strongly. No new numbers: names, directions and strength bands only.
"""
from __future__ import annotations

from flame.contracts import Prediction

NAMES = {
    'o2_frac': 'oxygen level', 'co2_frac': 'CO2 level', 'he_frac': 'helium level', 'd0_mm': 'droplet size',
    'heptane': 'fuel type (heptane rather than methanol)', 'fiber': 'droplet held on a fiber',
    'p_0p7atm': 'low pressure (0.7 atm)', 'heptane_x_d0_mm': 'droplet size (extra effect for heptane)',
    'heptane_x_o2_frac': 'oxygen level (extra effect for heptane)', 'heptane_x_co2_frac': 'CO2 level (extra effect for heptane)',
    'heptane_x_he_frac': 'helium level (extra effect for heptane)', 'o2_pct': 'oxygen level', 'pressure_bar': 'pressure',
    'jet_fuel_frac': 'fuel share in the burner jet', 'atm_fuel_pct': 'fuel share in the chamber air',
    'fuel_flow_mg_s': 'flame size (fuel flow)', 'ethane': 'fuel type (ethane rather than butane)',
    'propane': 'fuel type (propane rather than butane)', 'log10_jet_fuel_frac': 'fuel share in the burner jet',
}


def _directions(target: str) -> tuple[str, str]:
    if target == 'extinction_before_burnout':
        return 'pushes towards the flame going out', 'pushes towards the droplet burning completely'
    if target == 'droplet_breakup':
        return 'raises the break-up risk', 'lowers the break-up risk'
    if target.startswith('self_extinction'):
        return 'makes the flame go out sooner', 'keeps the flame burning longer'
    return 'raises the probability', 'lowers the probability'


def strength(v: float) -> str:
    a = abs(v)
    return 'strong' if a >= 1.0 else ('moderate' if a >= 0.4 else 'weak')


def drivers(p: Prediction, top: int = 4) -> list[str]:
    """Ranked statements, strongest first, relative to an average NASA test of the same model."""
    if p.probability is None or not p.contributions:
        return []
    up, down = _directions(p.target)
    net: dict[str, float] = {}
    for feat, v in p.contributions.items():          # fuel-specific extra effects are added to their variable:
        parent = feat.removeprefix('heptane_x_')       # one NET effect per variable for this case
        net[parent] = net.get(parent, 0.0) + v
    items = sorted(net.items(), key=lambda kv: -abs(kv[1]))
    out = []
    for feat, v in items[:top]:
        if abs(v) < 0.15:
            continue
        out.append(f'{NAMES.get(feat, feat)}: {up if v > 0 else down} ({strength(v)})')
    return out


def relevant_rows(effects, conditions):
    """Only the learned-effect rows about the case asked (its fuel / flame family)."""
    fuel = (conditions.fuel or '').lower()
    fam = conditions.family
    keep = []
    for r in effects.rows:
        s = r.scenario.lower()
        if fam == 'flex' and fuel and fuel in s:
            keep.append(r)
        elif fam == 'cfig' and 'normal gas' in s and conditions.flame_type != 'inverse':
            keep.append(r)
        elif fam == 'cfig' and 'inverse' in s and conditions.flame_type == 'inverse':
            keep.append(r)
    return keep or effects.rows


QUESTION_VARIABLES = [('co2_frac', 'co2_frac'), ('he_frac', 'he_frac'), ('o2_frac', 'o2_frac'),
                      ('pressure_kpa', 'pressure_kpa'), ('droplet_d0_mm', 'droplet_d0_mm')]


def variables_to_explain(conditions, family: str | None, k: int = 2) -> list[str]:
    """Variables the user set that a model uses -> their learned effects go with the prediction (max k)."""
    usable = {'flex': {'co2_frac', 'he_frac', 'o2_frac', 'pressure_kpa', 'droplet_d0_mm'},
              'cfig': {'o2_frac', 'pressure_kpa'}}.get(family or '', set())
    got = [v for attr, v in QUESTION_VARIABLES if getattr(conditions, attr, None) is not None and v in usable]
    return got[:k]
