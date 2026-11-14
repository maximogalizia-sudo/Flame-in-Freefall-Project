"""Smoke tests for LC's models: shapes, honesty rules and directions that the EDA established."""
from flame.contracts import Conditions as C
from flame.models import coverage, explain, predict


def test_flex_in_domain_has_number_and_breakup():
    p = predict(C(fuel='methanol', o2_frac=0.18, co2_frac=0.3))
    assert p[0].in_domain and 0 <= p[0].probability <= 1 and p[0].range_sensitivity
    assert p[1].target == 'droplet_breakup'


def test_flex_unknown_combination_gives_no_number():
    p = predict(C(fuel='heptane', o2_frac=0.34, co2_frac=0.2, pressure_kpa=70.3))[0]
    assert p.probability is None and not p.in_domain and p.out_of_domain and p.nearest_tests


def test_flex_more_co2_more_extinction():
    lo = predict(C(fuel='methanol', o2_frac=0.18, co2_frac=0.10))[0].probability
    hi = predict(C(fuel='methanol', o2_frac=0.18, co2_frac=0.20))[0].probability
    assert hi >= lo


def test_flex_2_3_atm_is_evidence_only():
    assert predict(C(fuel='methanol', o2_frac=0.21, co2_frac=0.2, pressure_kpa=280))[0].probability is None


def test_cfig_more_o2_slower_self_extinction():
    a = predict(C(fuel='butane', o2_frac=0.21, pressure_kpa=101.3))[0].probability
    b = predict(C(fuel='butane', o2_frac=0.39, pressure_kpa=101.3))[0].probability
    assert a > b


def test_partial_gravity_is_out_of_domain():
    assert not predict(C(fuel='methanol', o2_frac=0.21, gravity_g=0.165))[0].in_domain


def test_solids_are_evidence_only():
    p = predict(C(material='nomex', o2_frac=0.21))[0]
    assert p.probability is None and p.nearest_tests
    assert coverage(C(material='nomex', o2_frac=0.21), k=3)


def test_explain():
    assert explain('flex')['models'][0]['coefficients']
    assert explain('solids')['evidence_summary']
