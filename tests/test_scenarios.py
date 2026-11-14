"""v3 (mentors): risk indicator, scenario sweeps, per-variable effects."""
import pytest
from flame.contracts import AskRequest, Conditions, RiskRequest, SweepRequest
from flame.scenarios import effects, risk, sweep
from assistant.pipeline import answer
from assistant.tools import LocalTools


@pytest.fixture(autouse=True)
def _traces(tmp_path, monkeypatch):
    monkeypatch.setenv('ASSISTANT_TRACE_PATH', str(tmp_path / 't.jsonl'))
    from assistant.config import get_settings
    get_settings.cache_clear()


def test_exploration_atmosphere_is_red_with_untested_scenarios_gray():
    r = risk(RiskRequest(o2_frac=0.34, pressure_kpa=70.3))
    assert r.level == 'red' and any(i.level == 'gray' for i in r.items) and 'not a certified' in r.disclaimer


def test_suppressor_atmosphere_only_rates_what_was_tested():
    r = risk(RiskRequest(o2_frac=0.18, co2_frac=0.3))
    assert [i.scenario for i in r.items if i.level != 'gray'] == ['methanol droplet']


def test_sweep_leaves_untested_points_empty_and_is_monotone_for_co2():
    s = sweep(SweepRequest(conditions=Conditions(fuel='heptane', o2_frac=0.20), variable='co2_frac'))
    tested = [p.probability for p in s.points if p.in_domain]
    assert tested == sorted(tested) and any(not p.in_domain for p in s.points)


def test_effects_co2_has_caveat_and_never_tested():
    e = effects('co2_frac')
    assert any(r.direction == 'helps extinction' for r in e.rows)
    assert any('cannot be separated' in c for c in e.caveats) and any('never tested above' in n for n in e.not_tested)


@pytest.mark.parametrize('q, tool', [('¿Cómo incide el CO2 en el fenómeno?', 'effects'),
                                     ('¿Qué pasa si subo el CO2 de 10 a 30 % en una gota de heptano con 20 % de oxígeno?', 'sweep'),
                                     ('¿Es segura una atmósfera de cabina con 34 % de oxígeno a 0,7 atm?', 'risk')])
def test_assistant_routes_new_intents(q, tool):
    a = answer(AskRequest(text=q), tools=LocalTools())
    assert tool in a.tools_used and a.decision == 'answer'
