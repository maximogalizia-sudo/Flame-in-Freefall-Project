"""Train the interpretable models and save them as plain JSON (no pickles: the API only needs numpy).

Decisions behind every choice: Drive `docs/DECISIONES_DATOS.md` (DD-019..DD-031) and the EDA notebooks.
  FLEX  main      : regularized logistic, "extinction vs burned completely", clean tests at 0.7-1 atm (DD-022, DD-024),
                    fuel x droplet-size interaction always in, other fuel interactions chosen by grouped CV (DD-023).
  FLEX  breakup   : regularized logistic, "the droplet broke up (Disruption)", all tests at 0.7-1 atm.
  FLEX  sensitivity: main model refitted with every Disruption counted as extinction / as burned completely.
  CFI-G survival  : ridge Cox, time until the flame goes out by itself, flow-terminated = censored (DD-031),
                    one model per flame type, grouped CV by chamber atmosphere (DD-030).
  CFI-G ignition  : logistic on log10(jet fuel fraction), normal flames only (DD-031).
Every model stores: inputs, scaling, coefficients, grouped-CV metrics, training ranges and bootstrap coefficient
draws (resampling whole groups) used for the uncertainty interval at prediction time.

Usage: python -m flame.train   (from the repo root)
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

warnings.filterwarnings('ignore')
ROOT = Path(__file__).resolve().parents[1]
PROC, OUT = ROOT / 'data' / 'processed', ROOT / 'models'
N_BOOT = 300
RNG = np.random.default_rng(2026)


# ----------------------------------------------------------------------------- helpers
def standardize(X: pd.DataFrame):
    mu, sd = X.mean(), X.std(ddof=0).replace(0, 1.0)
    return (X - mu) / sd, mu, sd


def fit_logit(Z: np.ndarray, y: np.ndarray, C: float):
    m = LogisticRegression(C=C, max_iter=10000).fit(Z, y)
    return float(m.intercept_[0]), m.coef_[0]


def cv_logit(X: pd.DataFrame, y: np.ndarray, groups: np.ndarray, C: float, seeds=range(5)):
    """Grouped, stratified 5-fold CV repeated over seeds; scaling fitted inside each fold. Returns pooled metrics."""
    res = []
    for s in seeds:
        oof = np.zeros(len(y))
        for tr, te in StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=s).split(X, y, groups):
            Ztr, mu, sd = standardize(X.iloc[tr])
            b0, b = fit_logit(Ztr.values, y[tr], C)
            oof[te] = 1 / (1 + np.exp(-(b0 + ((X.iloc[te] - mu) / sd).values @ b)))
        oof = np.clip(oof, 1e-4, 1 - 1e-4)
        res.append([log_loss(y, oof), brier_score_loss(y, oof), roc_auc_score(y, oof)])
    r = np.array(res)
    return {'log_loss': round(r[:, 0].mean(), 4), 'brier': round(r[:, 1].mean(), 4), 'auc': round(r[:, 2].mean(), 4),
            'auc_sd': round(r[:, 2].std(), 4), 'base_rate_log_loss': round(log_loss(y, np.full(len(y), y.mean())), 4)}


def group_bootstrap(groups: np.ndarray, n=N_BOOT):
    ug = np.unique(groups)
    idx_by_g = {g: np.where(groups == g)[0] for g in ug}
    for _ in range(n):
        pick = RNG.choice(ug, size=len(ug), replace=True)
        yield np.concatenate([idx_by_g[g] for g in pick])


def logit_model(name, X, y, groups, C_grid, feature_sets, meta):
    """Pick feature set and C by grouped CV log loss; refit on all data; bootstrap the coefficients."""
    trials = []
    for fs_name, cols in feature_sets.items():
        for C in C_grid:
            trials.append({'features': fs_name, 'C': C, **cv_logit(X[cols], y, groups, C)})
    tr = pd.DataFrame(trials).sort_values('log_loss')
    best = tr.iloc[0]
    cols = feature_sets[best['features']]
    Z, mu, sd = standardize(X[cols])
    b0, b = fit_logit(Z.values, y, float(best['C']))
    draws = []
    for idx in group_bootstrap(groups):
        if len(np.unique(y[idx])) < 2:
            continue
        c0, c = fit_logit(Z.values[idx], y[idx], float(best['C']))
        draws.append([c0, *c])
    model = {'name': name, 'kind': 'logistic', 'features': cols, 'mu': mu.round(8).tolist(), 'sd': sd.round(8).tolist(),
             'intercept': b0, 'coef': b.tolist(), 'C': float(best['C']), 'boot': np.round(draws, 6).tolist(),
             'cv': {k: best[k] for k in ['log_loss', 'brier', 'auc', 'auc_sd', 'base_rate_log_loss']},
             'selection': tr.round(4).to_dict('records'), 'n': int(len(y)), 'n_positive': int(y.sum()),
             'n_groups': int(len(np.unique(groups))), **meta}
    return model


def ridge_cox(X, time, event, alpha):
    o = np.argsort(-time); X, time, event = X[o], time[o], event[o]
    def f(b):
        eta = X @ b; m = eta.max(); w = np.exp(eta - m)
        cw = np.cumsum(w); cwx = np.cumsum(w[:, None] * X, axis=0)
        ll = np.sum(event * (eta - m - np.log(cw)))
        return -ll + 0.5 * alpha * b @ b, -(event[:, None] * (X - cwx / cw[:, None])).sum(0) + alpha * b
    return minimize(f, np.zeros(X.shape[1]), jac=True, method='L-BFGS-B').x


def breslow(Z, time, event, b, grid):
    """Baseline cumulative hazard H0(t) on a time grid (Breslow estimator)."""
    r = np.exp(Z @ b)
    ev_t = np.unique(time[event == 1])
    dH = np.array([event[time == t].sum() / r[time >= t].sum() for t in ev_t])
    H = np.cumsum(dH)
    return np.array([H[ev_t <= g].max() if (ev_t <= g).any() else 0.0 for g in grid])


def cindex(time, event, risk):
    ok = tot = 0.0
    for a in np.where(event == 1)[0]:
        later = time > time[a]
        tot += later.sum(); ok += (risk[a] > risk[later]).sum() + 0.5 * (risk[a] == risk[later]).sum()
    return ok / tot


def cox_model(name, X, time, event, groups, alphas, meta, grid):
    trials = []
    for a in alphas:
        cs = []
        for s in range(5):
            ug = np.unique(groups); rng = np.random.RandomState(s); rng.shuffle(ug)
            fold = pd.Series(groups).map({g: k % 5 for k, g in enumerate(ug)}).values
            risk = np.zeros(len(time))
            for k in range(5):
                tr, te = fold != k, fold == k
                Ztr, mu, sd = standardize(X[tr])
                b = ridge_cox(Ztr.values, time[tr], event[tr], a)
                risk[te] = ((X[te] - mu) / sd).values @ b
            cs.append(cindex(time, event, risk))
        trials.append({'alpha': a, 'c_index': round(float(np.mean(cs)), 4), 'c_index_sd': round(float(np.std(cs)), 4)})
    tr = pd.DataFrame(trials).sort_values('c_index', ascending=False)
    a = float(tr.iloc[0]['alpha'])
    Z, mu, sd = standardize(X)
    b = ridge_cox(Z.values, time, event, a)
    H0 = breslow(Z.values, time, event, b, grid)
    draws, H0s = [], []
    for idx in group_bootstrap(groups, n=200):
        bb = ridge_cox(Z.values[idx], time[idx], event[idx], a)
        draws.append(bb); H0s.append(breslow(Z.values[idx], time[idx], event[idx], bb, grid))
    return {'name': name, 'kind': 'cox', 'features': list(X.columns), 'mu': mu.round(8).tolist(), 'sd': sd.round(8).tolist(),
            'coef': b.tolist(), 'alpha': a, 'grid_s': grid.tolist(), 'H0': np.round(H0, 6).tolist(),
            'boot_coef': np.round(draws, 6).tolist(), 'boot_H0': np.round(H0s, 5).tolist(),
            'cv': {'c_index': float(tr.iloc[0]['c_index']), 'c_index_sd': float(tr.iloc[0]['c_index_sd'])},
            'selection': tr.to_dict('records'), 'n': int(len(time)), 'n_events': int(event.sum()),
            'n_groups': int(len(np.unique(groups))), **meta}


def ranges(df, cols):
    return {c: [round(float(df[c].min()), 6), round(float(df[c].max()), 6)] for c in cols}


# ----------------------------------------------------------------------------- FLEX
def flex_frame(d: pd.DataFrame, centers=None) -> pd.DataFrame:
    X = pd.DataFrame({'o2_frac': d['o2_frac'], 'co2_frac': d['co2_frac'], 'he_frac': d['he_frac'], 'd0_mm': d['d0_mm'],
                      'heptane': (d['fuel'] == 'Heptane').astype(float), 'fiber': (d['support'] == 'fiber').astype(float),
                      'p_0p7atm': (d['pressure_atm'] < 0.8).astype(float)}, index=d.index)
    c = centers or {k: float(X[k].mean()) for k in ['o2_frac', 'co2_frac', 'he_frac', 'd0_mm']}
    for k in ['d0_mm', 'o2_frac', 'co2_frac', 'he_frac']:
        X[f'heptane_x_{k}'] = X['heptane'] * (X[k] - c[k])
    return X, c


def train_flex():
    f = pd.read_parquet(PROC / 'flex_tests.parquet')
    f = f[(f['p_level'] != '2-3 atm') & f['d0_mm'].notna()].copy()        # DD-024, DD-020
    base = ['o2_frac', 'co2_frac', 'he_frac', 'd0_mm', 'heptane', 'fiber', 'p_0p7atm']
    sets = {'base + fuel x size': base + ['heptane_x_d0_mm'],
            '+ fuel x O2': base + ['heptane_x_d0_mm', 'heptane_x_o2_frac'],
            '+ fuel x (O2, CO2, He)': base + ['heptane_x_d0_mm', 'heptane_x_o2_frac', 'heptane_x_co2_frac', 'heptane_x_he_frac']}
    main = f[f['clean']].copy()
    X, centers = flex_frame(main)
    y = main['extinct'].astype(int).values
    meta = {'family': 'flex', 'target': 'extinction_before_burnout',
            'target_text': 'If the droplet does not break up: probability that the flame goes out before the droplet burns completely',
            'interaction_centers': centers, 'training_ranges': ranges(main, ['o2_frac', 'co2_frac', 'he_frac', 'd0_mm', 'pressure_atm']),
            'data': 'data/processed/flex_tests.parquet: clean tests (no Disruption), 0.7-1 atm, with initial size'}
    m_main = logit_model('flex_main', X, y, main['group_id'].values, [0.1, 0.3, 1.0, 3.0], sets, meta)
    cols = m_main['features']; C = m_main['C']

    # sensitivity: every Disruption counted as extinction / as burned completely (same features and C)
    sens = {}
    for label, val in [('disruption_as_extinction', 1), ('disruption_as_completion', 0)]:
        a = f.copy(); a['y'] = np.where(a['test_end'] == 'Disruption', val, a['extinct'].astype(int))
        Xa, _ = flex_frame(a, centers)
        Za, mu, sd = standardize(Xa[cols])
        b0, b = fit_logit(Za.values, a['y'].values, C)
        sens[label] = {'mu': mu.tolist(), 'sd': sd.tolist(), 'intercept': b0, 'coef': b.tolist(), 'n': int(len(a))}
    m_main['sensitivity'] = sens

    # break-up (Disruption) risk, all tests at 0.7-1 atm
    Xb, _ = flex_frame(f, centers)
    yb = (f['test_end'] == 'Disruption').astype(int).values
    meta_b = {'family': 'flex', 'target': 'droplet_breakup',
              'target_text': 'Probability that the test is interrupted by droplet break-up (Disruption; largely a needle-contamination artefact per NASA)',
              'interaction_centers': centers, 'training_ranges': ranges(f, ['o2_frac', 'co2_frac', 'he_frac', 'd0_mm', 'pressure_atm']),
              'data': 'all FLEX tests at 0.7-1 atm with initial size'}
    m_break = logit_model('flex_breakup', Xb, yb, f['group_id'].values, [0.1, 0.3, 1.0], {'base': base, 'base + fuel x size': sets['base + fuel x size']}, meta_b)
    return m_main, m_break


# ----------------------------------------------------------------------------- CFI-G
def cfig_frame(d: pd.DataFrame, flame_type: str) -> pd.DataFrame:
    X = pd.DataFrame(index=d.index)
    X['o2_pct'] = d['o2_mid'] * 100
    X['pressure_bar'] = d['pressure_bar']
    if flame_type == 'normal':
        X['jet_fuel_frac'] = d['jet_fuel_frac']
    else:
        X['atm_fuel_pct'] = d['atm_fuel_frac'] * 100
    X['fuel_flow_mg_s'] = d['fuel_flow_mg_s']
    X['ethane'] = (d['fuel'] == 'ethane').astype(float)
    X['propane'] = (d['fuel'] == 'propane').astype(float)
    return X


def train_cfig():
    c = pd.read_parquet(PROC / 'cfig_tests.parquet')
    grid = np.arange(0, 301, 1.0)
    out = []
    for ft in ['normal', 'inverse']:
        d = c[(c['flame_type'] == ft) & c['ext_model']].dropna(subset=['burn_time_s']).copy()
        d['burn_time_s'] = d['burn_time_s'].clip(lower=0.5)
        X = cfig_frame(d, ft)
        meta = {'family': 'cfig', 'flame_type': ft, 'target': 'self_extinction',
                'target_text': 'Probability that the lit flame goes out by itself within the horizon (seconds)',
                'training_ranges': ranges(X.assign(burn_time_s=d['burn_time_s']), list(X.columns) + ['burn_time_s']),
                'fuels': sorted(d['fuel'].unique().tolist()),
                'data': f'data/processed/cfig_tests.parquet: {ft} flames that ignited; flow-terminated = censored'}
        out.append(cox_model(f'cfig_selfext_{ft}', X, d['burn_time_s'].values, d['self_ext'].astype(int).values,
                             d['fill_key'].values, [0.3, 1.0, 3.0, 10.0], meta, grid))
    g = c[c['ign_model']].copy()
    Xi = pd.DataFrame({'log10_jet_fuel_frac': np.log10(g['jet_fuel_frac'].clip(lower=0.005))}, index=g.index)
    yi = g['ignited'].astype(int).values
    meta = {'family': 'cfig', 'flame_type': 'normal', 'target': 'ignition',
            'target_text': 'Probability that a normal (fuel-into-oxygen) flame lights, given how diluted the fuel jet is',
            'training_ranges': ranges(g, ['jet_fuel_frac', 'o2_mid', 'pressure_bar']),
            'data': 'normal-flame tests without deliberate low-energy or hardware failures (DD-027)'}
    # StratifiedGroupKFold needs >= 5 groups with both classes; fall back to fewer seeds if needed
    out.append(logit_model('cfig_ignition', Xi, yi, g['fill_key'].values, [0.3, 1.0, 3.0], {'jet fuel only': ['log10_jet_fuel_frac']}, meta))
    return out


def main():
    OUT.mkdir(exist_ok=True)
    models = [*train_flex(), *train_cfig()]
    for m in models:
        (OUT / f"{m['name']}.json").write_text(json.dumps(m, default=float))
        print(f"{m['name']:22s} n={m['n']:4d}  cv={m['cv']}  features={m['features']}")


if __name__ == '__main__':
    main()
