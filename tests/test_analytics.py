"""Analytic tools: generic over variables, families and targets."""
import pytest
from flame.analytics import gaps, profile, rank
from flame.contracts import AskRequest
from assistant.pipeline import answer
from assistant.tools import LocalTools


@pytest.mark.parametrize('var', ['o2', 'co2', 'he', 'pressure', 'd0', 'fuel_flow'])
def test_profile_any_variable(var):
    p = profile(var)
    assert p['groups'] and all(g['n'] >= 3 for g in p['groups'])


@pytest.mark.parametrize('target', ['persistence', 'extinction', 'burn_time', 'fastest_out'])
@pytest.mark.parametrize('fam', [None, 'flex', 'cfig'])
def test_rank_any_target_and_family(target, fam):
    r = rank(target, fam, k=3)
    assert r['rows'] and all(x['n_tests'] >= 2 for x in r['rows'])


def test_no_oxygen_range_without_fire_in_droplets():
    g = [x for x in profile('o2', 'flex')['groups'] if x['group'].endswith('- all') and x['outcome'] == 'persistence'][0]
    assert g['zones_never'] == [] and g['min_where_observed'] <= 15


def test_gaps_cover_suppressors_and_solids():
    txt = ' '.join(i['gap'] for i in gaps()['items'])
    assert 'Suppressors' in txt and 'Solid materials were never tested above' in txt


@pytest.mark.parametrize('q, intent, tool', [
    ('¿En qué rangos de oxígeno no se prende fuego bajo ninguna condición?', 'profile', 'profile'),
    ('Dame las 5 configuraciones de variables que más tienden a incendiarse', 'rank', 'rank'),
    ('¿Cuáles son las configuraciones de variables que más tardan en apagarse?', 'rank', 'rank'),
    ('Mencioname 3 experimentos que se deberían realizar en el futuro', 'gaps', 'gaps'),
    ('¿Cuál es el peligro de la alta presión respecto a este fenómeno?', 'explain_variable', 'effects'),
])
def test_assistant_answers_analytic_questions(q, intent, tool, tmp_path, monkeypatch):
    monkeypatch.setenv('ASSISTANT_TRACE_PATH', str(tmp_path / 't.jsonl'))
    a = answer(AskRequest(text=q), tools=LocalTools())
    assert a.intent == intent and tool in a.tools_used and a.decision == 'answer'


def test_duration_curve_and_observed():
    from flame.contracts import Conditions as C
    from flame.scenarios import duration
    r = duration(C(fuel='butane', o2_frac=0.39, pressure_kpa=101.3))
    assert r.in_domain and r.curve[0].still_burning == 1.0 and r.curve[-1].still_burning < r.curve[1].still_burning and r.observed
    assert not duration(C(fuel='propane', o2_frac=0.40, pressure_kpa=200)).in_domain
    assert duration(C(fuel='heptane', o2_frac=0.21)).observed
