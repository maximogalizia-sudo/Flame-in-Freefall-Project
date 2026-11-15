"""Analytic tools over ALL the curated NASA data (LC, 2026-11-14: questions about ranges, rankings and gaps, not one case).

Three generic operations, valid for any variable and any outcome, every number backed by a count of real tests:
  profile(variable, family=None) -> tested range per family, outcome share by bands of the variable, and the zones
                                    where the outcome was ALWAYS or NEVER observed (thresholds) - or that none exists.
  rank(target, family=None, k=5) -> tested condition combinations ranked by a target:
                                    'persistence' (flame kept burning / burned completely / sustained),
                                    'extinction' (flame went out), 'burn_time' (longest burning), 'fastest_out'.
  gaps(family=None)              -> what NASA never tested and where the data are weak, each with a suggested experiment.
Outcome meaning per family (from the EDA decisions):
  flex   (droplets, clean tests): extinction = flame went out before the droplet burned; persistence = burned completely.
  cfig   (gas flames, ignited): extinction = went out by itself; persistence = still burning when the gas was cut.
         ignition = lit (normal flames, without deliberate low-energy or hardware failures).
  solids (BASS/BASS-II, evidence only): persistence = sustained a flame (ignited and kept it); excludes unclear/hardware.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .models import table

FAMILIES = ('flex', 'cfig', 'solids')
FAMILY_NAME = {'flex': 'fuel droplets (FLEX)', 'cfig': 'gas flames (CFI-G)', 'solids': 'solid materials (BASS/BASS-II)'}
VAR_LABEL = {'o2': 'O2 (%)', 'co2': 'CO2 (%)', 'he': 'helium (%)', 'pressure': 'pressure (kPa)', 'd0': 'droplet diameter (mm)',
             'fuel_flow': 'fuel flow (mg/s)', 'jet_fuel': 'fuel in the burner jet (%)'}
ALIASES = {'o2_frac': 'o2', 'co2_frac': 'co2', 'he_frac': 'he', 'pressure_kpa': 'pressure', 'droplet_d0_mm': 'd0',
           'fuel_flow_mg_s': 'fuel_flow', 'jet_fuel_frac': 'jet_fuel'}


# ----------------------------------------------------------------------------- per-family frames with a common shape
def _flex() -> pd.DataFrame:
    t = table('flex_tests')
    d = t[t['clean']].copy()
    return pd.DataFrame({'family': 'flex', 'subject': d['fuel'].str.lower(), 'test_id': d['flex_id'],
                         'o2': d['o2_frac'] * 100, 'co2': d['co2_frac'] * 100, 'he': d['he_frac'] * 100,
                         'pressure': d['pressure_kpa'], 'd0': d['d0_mm'], 'support': d['support'], 'p_level': d['p_level'],
                         'extinction': d['extinct'].astype(float), 'persistence': 1 - d['extinct'].astype(float),
                         'burn_time': d['burn_time_s'], 'censored': False})


def _cfig(flame_type: str = 'normal') -> pd.DataFrame:
    t = table('cfig_tests')
    d = t[(t['flame_type'] == flame_type) & t['ext_model']].copy()
    return pd.DataFrame({'family': 'cfig', 'subject': d['fuel'], 'test_id': d['test_id'], 'flame_type': flame_type,
                         'o2': d['o2_mid'] * 100, 'o2_nominal': d['o2_nominal'] * 100, 'pressure': d['pressure_bar'] * 100,
                         'fuel_flow': d['fuel_flow_mg_s'], 'jet_fuel': d['jet_fuel_frac'] * 100,
                         'extinction': d['self_ext'].astype(float), 'persistence': 1 - d['self_ext'].astype(float),
                         'burn_time': d['burn_time_s'], 'censored': ~d['self_ext']})


def _cfig_ignition() -> pd.DataFrame:
    t = table('cfig_tests')
    d = t[t['ign_model']].copy()
    return pd.DataFrame({'family': 'cfig', 'subject': d['fuel'], 'test_id': d['test_id'], 'o2': d['o2_mid'] * 100,
                         'pressure': d['pressure_bar'] * 100, 'jet_fuel': d['jet_fuel_frac'] * 100,
                         'fuel_flow': d['fuel_flow_mg_s'], 'ignition': d['ignited'].astype(float)})


def _solids() -> pd.DataFrame:
    t = table('bass_tests')
    d = t[~t['hardware_issue'] & (t['outcome_group'] != 'unclear') & t['o2_pct'].notna()].copy()
    sustained = (d['outcome_group'] != 'no_flame').astype(float)
    return pd.DataFrame({'family': 'solids', 'subject': d['material'], 'test_id': d['test_id'], 'o2': d['o2_pct'],
                         'persistence': sustained, 'extinction': 1 - sustained, 'burn_time': np.nan, 'censored': False})


def _frame(family: str) -> pd.DataFrame:
    return {'flex': _flex, 'cfig': _cfig, 'solids': _solids}[family]()


OUTCOME_TEXT = {'flex': {'extinction': 'flame went out before the droplet burned', 'persistence': 'droplet burned completely'},
                'cfig': {'extinction': 'flame went out by itself', 'persistence': 'still burning when the gas was cut'},
                'solids': {'extinction': 'no sustained flame', 'persistence': 'ignited and sustained a flame'}}


# ----------------------------------------------------------------------------- profile
def _bands(x: pd.Series, n_bands: int = 5) -> list[float]:
    lo, hi = float(x.min()), float(x.max())
    if hi - lo < 1e-9:
        return [lo, hi]
    edges = np.unique(np.round(np.quantile(x, np.linspace(0, 1, n_bands + 1)), 2))
    return edges.tolist() if len(edges) > 2 else [lo, hi]


def _profile_one(d: pd.DataFrame, var: str, outcome: str, label: str) -> dict | None:
    d = d[d[var].notna() & d[outcome].notna()]
    if var in ('co2', 'he'):
        d = d[d[var] > 0]
    if len(d) < 3 or d[var].nunique() < 2:
        return None
    edges = _bands(d[var])
    cut = pd.cut(d[var], edges, include_lowest=True, duplicates='drop')
    g = d.groupby(cut, observed=True)[outcome].agg(['size', 'mean'])
    bands = [{'from': round(float(iv.left), 2), 'to': round(float(iv.right), 2), 'n': int(r['size']), 'share': round(float(r['mean']), 2)}
             for iv, r in g.iterrows()]
    occurred = d[d[outcome] == 1][var]
    # ALWAYS / NEVER zones: bands with >= 3 tests where the outcome happened in all / none of them
    always = [[b['from'], b['to'], b['n']] for b in bands if b['n'] >= 3 and b['share'] == 1]
    never = [[b['from'], b['to'], b['n']] for b in bands if b['n'] >= 3 and b['share'] == 0]
    return {'group': label, 'outcome': outcome, 'n': int(len(d)), 'tested_range': [round(float(d[var].min()), 2), round(float(d[var].max()), 2)],
            'bands': bands, 'min_where_observed': round(float(occurred.min()), 2) if len(occurred) else None,
            'max_where_observed': round(float(occurred.max()), 2) if len(occurred) else None,
            'zones_always': always, 'zones_never': never}


def _matches(group_label: str, subject: str | None) -> bool:
    if not subject or group_label.endswith(' - all'):
        return False
    return subject.lower().replace('_', ' ').replace('n heptane', 'heptane') in group_label.lower()


def profile_groups(d: dict, k: int = 6) -> list[dict]:
    """Groups to show: the asked fuel/material first (DT-032), then the whole-family groups as context."""
    every = [x for x in d['groups'] if x['group'].endswith(' - all')] or d['groups']
    own = [x for x in d['groups'] if _matches(x['group'], d.get('subject'))]
    return (own + [x for x in every if x not in own])[:k]


def profile(variable: str, family: str | None = None, subject: str | None = None) -> dict:
    """`subject`: the fuel or material asked about (e.g. 'nomex', 'methanol'); its groups come first."""
    var = ALIASES.get(variable, variable)
    out = {'variable': var, 'label': VAR_LABEL.get(var, var), 'groups': [], 'notes': [], 'subject': subject}
    fams = [family] if family else list(FAMILIES)
    for fam in fams:
        if fam == 'cfig':
            frames = [('cfig', _cfig('normal'), ['extinction', 'persistence'], 'normal gas flames'),
                      ('cfig', _cfig_ignition(), ['ignition'], 'normal gas flames (ignition)')]
        else:
            frames = [(fam, _frame(fam), ['extinction', 'persistence'], FAMILY_NAME[fam])]
        for f, d, outcomes, name in frames:
            if var not in d.columns:
                continue
            for subj, ds in [('all', d)] + list(d.groupby('subject')):
                for oc in outcomes:
                    r = _profile_one(ds, var, oc, f'{name} - {subj}')
                    if r:
                        r['family'] = f
                        r['meaning'] = (OUTCOME_TEXT.get(f, {}).get(oc) or ('the gas flame lit' if oc == 'ignition' else oc))
                        out['groups'].append(r)
    if var == 'o2':
        out['notes'].append('Inverse gas flames are excluded: there O2 is the burner jet, not the surrounding air.')
        out['notes'].append('Gas-flame ignition depended mostly on how diluted the fuel jet was, not on O2: '
                            'non-ignitions at ~21 % O2 come from very diluted jets.')
    if any(g['family'] == 'flex' for g in out['groups']):
        out['notes'].append('Droplets were always ignited on purpose; for them the outcome is whether the flame went out before burnout.')
    if var in ('co2', 'he'):
        out['notes'].append('Suppressors (CO2, helium) were only used in the droplet experiment (FLEX).')
    if not out['groups']:
        out['notes'].append(f'No NASA table in this project varies {var} for the requested family.')
    if subject:
        own = [g for g in out['groups'] if _matches(g['group'], subject)]
        if not own:
            out['notes'].insert(0, f'Too few usable NASA tests of {subject} to profile {var} on its own (fewer than 3, '
                                   f'or a single value): the groups below are for the whole family, NOT for {subject}.')
        elif min(g['n'] for g in own) < 10:
            out['notes'].insert(0, f'Few NASA tests of {subject} ({min(g["n"] for g in own)}–{max(g["n"] for g in own)}): '
                                   f'read its ranges as the tests that exist, not as a general limit.')
    return out


# ----------------------------------------------------------------------------- rank
def _describe(row: pd.Series, fam: str) -> str:
    if fam == 'flex':
        parts = [f"{row['subject']} droplet", f"O2 {row['o2']:.0f}%"]
        if row.get('co2', 0) > 0: parts.append(f"CO2 {row['co2']:.0f}%")
        if row.get('he', 0) > 0: parts.append(f"He {row['he']:.0f}%")
        parts += [str(row['p_level']), f"{row['support']} droplet"]
        return ', '.join(parts)
    if fam == 'cfig':
        return f"{row['subject']} {row.get('flame_type', 'normal')} flame, O2 ~{row['o2_nominal']:.0f}%, {row['pressure'] / 100:.2f} bar, fuel flow {row['fuel_flow']:.2f} mg/s"
    return f"{row['subject']}, O2 ~{row['o2']:.0f}%"


def _groups(fam: str) -> pd.DataFrame:
    d = _frame(fam)
    if fam == 'flex':
        keys = ['subject', 'support', 'p_level', 'o2r', 'co2r', 'her']
        d = d.assign(o2r=d['o2'].round(0), co2r=d['co2'].round(0), her=d['he'].round(0))
    elif fam == 'cfig':
        d = pd.concat([d, _cfig('inverse')], ignore_index=True)
        keys = ['subject', 'flame_type', 'o2_nominal', 'pr', 'fr']
        d = d.assign(pr=d['pressure'].round(-1), fr=d['fuel_flow'].round(2))
    else:
        keys = ['subject', 'o2b']
        d = d.assign(o2b=(d['o2'] / 2).round() * 2)
    agg = d.groupby(keys, dropna=False).agg(n=('test_id', 'size'), persistence=('persistence', 'mean'),
                                            extinction=('extinction', 'mean'), burn_time=('burn_time', 'median'),
                                            censored=('censored', 'any'), tests=('test_id', lambda s: list(s)[:4]),
                                            first=('test_id', 'first')).reset_index()
    first_rows = {tuple(row[k] for k in keys): row for _, row in d.drop_duplicates(keys).iterrows()}
    agg['description'] = [_describe(first_rows[tuple(r[k] for k in keys)], fam) for _, r in agg.iterrows()]
    agg['family'] = fam
    return agg


def rank(target: str = 'persistence', family: str | None = None, k: int = 5, min_n: int = 2) -> dict:
    fams = [family] if family else list(FAMILIES)
    rows, notes = [], []
    for fam in fams:
        if target in ('burn_time', 'fastest_out') and fam == 'solids':
            notes.append('Solid-material tests have no comparable burn time (the operator changed the air flow during the test).')
            continue
        g = _groups(fam)
        g = g[g['n'] >= min_n]
        if target == 'persistence':
            g = g.sort_values(['persistence', 'n'], ascending=[False, False])
        elif target == 'extinction':
            g = g.sort_values(['extinction', 'n'], ascending=[False, False])
        elif target == 'burn_time':
            g = g[g['burn_time'].notna()].sort_values('burn_time', ascending=False)
        elif target == 'fastest_out':
            g = g[g['burn_time'].notna() & (g['extinction'] == 1)].sort_values('burn_time', ascending=True)
        for _, r in g.head(k).iterrows():
            rows.append({'family': fam, 'configuration': r['description'], 'n_tests': int(r['n']),
                         'share_kept_burning': round(float(r['persistence']), 2), 'share_went_out': round(float(r['extinction']), 2),
                         'median_burn_time_s': None if pd.isna(r['burn_time']) else round(float(r['burn_time']), 1),
                         'burn_time_is_lower_bound': bool(r['censored']), 'example_tests': r['tests']})
    if target == 'persistence':
        notes.append("'kept burning' means: droplets = burned completely; gas flames = still burning when the gas was cut; "
                     'solids = ignited and sustained a flame')
    if any(r['burn_time_is_lower_bound'] for r in rows):
        notes.append('For gas flames cut by the operator the burn time is a LOWER bound (the flame was still burning).')
    notes.append(f'Only tested combinations with at least {min_n} tests are ranked; every row is observed NASA data, not a prediction.')
    return {'target': target, 'rows': rows, 'notes': notes}


# ----------------------------------------------------------------------------- gaps
def gaps(family: str | None = None) -> dict:
    f = table('flex_tests'); c = table('cfig_tests'); s = table('bass_tests')
    fl = f[f['clean']]
    sup = fl[(fl['co2_frac'] > 0) | (fl['he_frac'] > 0)]
    items = [
        {'family': 'flex', 'gap': f"Suppressors (CO2, helium) were never tested above {sup['o2_frac'].max():.0%} O2 "
                                   f"(the 30-34 % 'exploration atmosphere' has no suppressor data)",
         'suggested_test': 'Droplet (or gas-flame) tests with CO2 and helium at 30-34 % O2 and ~0.7 atm'},
        {'family': 'flex', 'gap': 'CO2 and helium were never used together', 'suggested_test': 'Mixed CO2 + helium suppressant tests'},
        {'family': 'flex', 'gap': f"High-O2 droplet tests (> 24 % O2) mostly ended in droplet break-up (contaminated needles): only "
                                   f"{int(((fl['o2_frac'] > 0.24)).sum())} clean tests there",
         'suggested_test': 'Repeat high-O2 droplet tests with clean needles'},
        {'family': 'flex', 'gap': f"Only {int((fl['p_level'] == '2-3 atm').sum())} clean tests at 2-3 atm, almost identical",
         'suggested_test': 'Droplet tests at 2-3 atm across O2 levels and both fuels'},
        {'family': 'flex', 'gap': f"Few droplets burned completely ({int((fl['test_end'] == 'Completion').sum())} of {len(fl)} clean tests): "
                                   'the edge between going out and burning out is thinly sampled',
         'suggested_test': 'More tests near the extinction limit (small heptane droplets, 20-24 % O2)'},
        {'family': 'cfig', 'gap': 'Normal gas flames were run only around 21 % and 40 % O2 (nothing in between)',
         'suggested_test': 'Gas-flame tests at 25-35 % O2'},
        {'family': 'cfig', 'gap': 'No suppressor (CO2, helium) was used with gas flames', 'suggested_test': 'Gas flames with CO2 / helium dilution'},
        {'family': 'solids', 'gap': f"Solid materials were never tested above {s['o2_pct'].max():.1f} % O2",
         'suggested_test': 'Cabin materials (cotton/fiberglass fabric, Nomex, PMMA) at 25-35 % O2 and reduced pressure'},
        {'family': 'solids', 'gap': 'Solid materials were tested only at cabin pressure (~1 atm)', 'suggested_test': 'Solid materials at 0.5-0.7 atm'},
        {'family': 'solids', 'gap': f"{int((s['outcome_group'] == 'unclear').mean() * 100)} % of the solid-material test logs do not say how the test ended, "
                                     'and air-flow values are not on one calibrated scale',
         'suggested_test': 'Record extinction flow in calibrated cm/s for every test'},
        {'family': None, 'gap': 'No data at partial gravity (Moon 0.16 g, Mars 0.38 g): all tests are microgravity',
         'suggested_test': 'Repeat key tests in partial gravity (e.g. lunar-lander or centrifuge facilities)'},
        {'family': None, 'gap': 'Few fuels/materials: 2 liquids, 3 gases, a handful of solids',
         'suggested_test': 'Broaden to real cabin items (cables, foams, electronics, food packaging)'},
    ]
    if family:
        items = [i for i in items if i['family'] in (family, None)]
    return {'items': items}


# ----------------------------------------------------------------------------- measured quantities (DT-025, 2026-11-15)
# What NASA MEASURED in each test, not the outcome: before this, the droplet diameter at extinction, the burning rate
# and the cool-flame / soot marks were in the curated tables but no tool showed them (the assistant said "no data").
QUANTITIES = {
    'dext': ('droplet diameter at extinction', 'mm', 'flex'),
    'burn_rate': ('burning rate (d-squared law constant)', 'mm2/s', 'flex'),
    'burn_time': ('burn time', 's', None),
    'cool_flame': ('cool flame observed after the hot flame', 'flag', 'flex'),
    'soot': ('soot mentioned in the NASA test description', 'flag', 'flex'),
}


def _measure_flex(quantity: str) -> tuple[pd.DataFrame, list[str]]:
    t = table('flex_tests')
    d = t[t['clean']].copy()
    notes = ['Clean FLEX tests only (no droplet break-up, no hardware problem).']
    if quantity == 'dext':
        d = d[(d['test_end'] == 'Extinction') & (d['dext_mm'] > 0)]       # DD-012: only meaningful when the flame went out
        d['value'] = d['dext_mm']
        notes.append('Diameter at extinction only for tests where the flame went out (DD-012); "radiative" vs "diffusive" '
                     'extinction is not a column of the data: it is discussed in the NASA report.')
    elif quantity == 'burn_rate':
        d = d[d['burn_rate'].notna() & ~d['conflicting_values'].fillna(False)]
        short = int((d['burn_time_s'] < 1).sum())
        d = d[d['burn_time_s'] >= 1]                                         # DD-014: rates measured in < 1 s are unreliable
        d['value'] = d['burn_rate']
        notes.append(f'Burning rates measured in less than 1 s left out ({short} tests, DD-014).')
    elif quantity == 'burn_time':
        d = d[d['burn_time_s'].notna()]
        d['value'] = d['burn_time_s']
    else:
        col = 'cool_flame' if quantity == 'cool_flame' else 'soot_mentioned'
        d['value'] = d[col].fillna(False).astype(float)
    d['group'] = d['fuel'] + ', ' + d['diluent'].replace({'N2 only': 'no CO2/He'}) + ', ' + d['p_level']
    return d, notes


def _measure_cfig() -> tuple[pd.DataFrame, list[str]]:
    t = table('cfig_tests')
    d = t[t['ext_model'] & t['burn_time_s'].notna()].copy()
    d['value'] = d['burn_time_s']
    d['group'] = d['fuel'] + ', ' + d['flame_type'] + ' flames'
    d['o2_frac'] = d['o2_mid']
    return d, ['Gas flames: burn time until it went out by itself or until the operator cut the gas (then it is a '
               'lower bound: the flame was still burning).']


def measure(quantity: str, family: str | None = None, prefer: list[str] | None = None) -> dict:
    """Typical measured values per group of tested conditions, with counts; for flags, how often and where it happened.
    `prefer`: words of the question (e.g. ['heptane', 'CO2', '1 atm']); groups that contain more of them come first."""
    if quantity not in QUANTITIES:
        raise ValueError(f'unknown quantity {quantity}')
    label, unit, only = QUANTITIES[quantity]
    fams = [only] if only else ([family] if family in ('flex', 'cfig') else ['flex', 'cfig'])
    groups, notes = [], []
    for fam in fams:
        d, n = _measure_flex(quantity) if fam == 'flex' else _measure_cfig()
        notes += n
        for g, x in d.groupby('group'):
            if unit == 'flag':
                hit = x[x['value'] > 0]
                if not len(hit):
                    groups.append({'family': fam, 'group': g, 'n': int(len(x)), 'count': 0, 'share': 0.0})
                    continue
                groups.append({'family': fam, 'group': g, 'n': int(len(x)), 'count': int(len(hit)),
                               'share': round(len(hit) / len(x), 2),
                               'd0_mm_range': [round(float(hit['d0_mm'].min()), 2), round(float(hit['d0_mm'].max()), 2)]
                               if hit['d0_mm'].notna().any() else None,
                               'o2_pct_range': [round(float(hit['o2_frac'].min()) * 100, 1), round(float(hit['o2_frac'].max()) * 100, 1)],
                               'tests': hit['flex_id'].head(5).tolist()})
            elif len(x) >= 2:
                v = x['value']
                groups.append({'family': fam, 'group': g, 'n': int(len(x)), 'median': round(float(v.median()), 2),
                               'p25': round(float(v.quantile(.25)), 2), 'p75': round(float(v.quantile(.75)), 2),
                               'min': round(float(v.min()), 2), 'max': round(float(v.max()), 2),
                               'o2_pct_range': [round(float(x['o2_frac'].min()) * 100, 1), round(float(x['o2_frac'].max()) * 100, 1)]})
    pref = [w.lower() for w in (prefer or [])]
    score = lambda r: sum(w in r['group'].lower() for w in pref)   # noqa: E731
    key = (lambda r: (-score(r), -r['count'])) if unit == 'flag' else (lambda r: (-score(r), -r['n']))
    return {'quantity': quantity, 'label': label, 'unit': unit, 'groups': sorted(groups, key=key), 'notes': notes}
