"""POST /tests/search over the curated tables (PLAN_ASISTENTE M2). Needs the database loaded with
`python -m pipeline.load_curated`; skipped when the database is not reachable."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api import db
from api.main import app
from api.settings import get_settings

pytestmark = pytest.mark.skipif(not db.ping(), reason="database not reachable")
client = TestClient(app)
PROC = "../data/processed"


def search(**body):
    return client.post("/tests/search", json=body)


def test_nomex_grouped_by_outcome():                          # acceptance criterion of M2
    r = search(family="solids", material="Nomex", group_by=["outcome_group"]).json()
    assert r["n_total"] == 9 and sum(c["n"] for c in r["counts"]) == 9
    assert {"outcome_group": "no_flame", "n": 7} in r["counts"]
    row = r["rows"][0]
    assert row["psi_id"] in {"PSI-25", "PSI-26"} and row["observation"] and row["source"]


def test_counts_match_the_curated_files():
    flex = pd.read_parquet(f"{PROC}/flex_tests.parquet")
    r = search(family="flex", fuel="methanol", diluent="CO2", only_model_rows=True, group_by=["test_end"]).json()
    sub = flex[(flex.fuel == "Methanol") & (flex.diluent == "CO2") & flex.in_main_model]
    assert r["n_total"] == len(sub)
    assert {c["test_end"]: c["n"] for c in r["counts"]} == sub.test_end.value_counts().to_dict()


def test_cfig_filters_and_pressure_in_kpa():
    cfig = pd.read_parquet(f"{PROC}/cfig_tests.parquet")
    r = search(family="cfig", fuel="n_butane", flame_type="normal", o2_min=0.3, pressure_kpa_max=110, limit=200).json()
    sub = cfig[(cfig.fuel == "butane") & (cfig.flame_type == "normal") & (cfig.o2_mid >= 0.3) & (cfig.pressure_bar * 100 <= 110)]
    assert r["n_total"] == len(sub) and all(row["pressure_kpa"] <= 110 for row in r["rows"])
    assert search(family="cfig", group_by=["end", "ignited"]).status_code == 200    # "end" is an SQL keyword


def test_rejects_filters_that_do_not_apply():
    assert search(family="solids", diluent="CO2").status_code == 422
    assert search(family="solids", pressure_kpa_min=50).status_code == 422
    assert search(family="flex", material="Nomex").status_code == 422
    assert search(family="flex", group_by=["caption"]).status_code == 422               # not whitelisted
    assert search(family="flex", group_by=["fuel; DROP TABLE mart.flex_tests"]).status_code == 422
    assert search(family="sql").status_code == 422


def test_values_are_parameters_not_sql():
    r = search(family="solids", material="Nomex' OR '1'='1").json()
    assert r["n_total"] == 0


def test_limit_and_token(monkeypatch):
    assert len(search(family="flex", limit=3).json()["rows"]) == 3
    monkeypatch.setenv("ASSISTANT_API_TOKEN", "secret-for-test")
    get_settings.cache_clear()
    try:
        assert search(family="flex").status_code == 401
        ok = client.post("/tests/search", json={"family": "flex"}, headers={"Authorization": "Bearer secret-for-test"})
        assert ok.status_code == 200
    finally:
        monkeypatch.delenv("ASSISTANT_API_TOKEN")
        get_settings.cache_clear()
