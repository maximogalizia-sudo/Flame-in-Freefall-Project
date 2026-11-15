"""M7: /risk, /sweep, /effects/{variable} are thin wrappers of flame.scenarios (contract v3). Public, no database."""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_risk_exploration_atmosphere_is_red_with_gray_items():
    r = client.post("/risk", json={"o2_frac": 0.34, "pressure_kpa": 70.3})
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "red"
    assert any(i["level"] == "gray" for i in body["items"])
    assert "not a certified" in body["disclaimer"]


def test_risk_suppressor_atmosphere_rates_only_methanol():
    body = client.post("/risk", json={"o2_frac": 0.18, "co2_frac": 0.3}).json()
    rated = [i["scenario"] for i in body["items"] if i["level"] != "gray"]
    assert rated == ["methanol droplet"]


def test_risk_validation():
    assert client.post("/risk", json={"o2_frac": 0.01}).status_code == 422
    assert client.post("/risk", json={}).status_code == 422


def test_sweep_leaves_untested_points_empty():
    r = client.post("/sweep", json={"conditions": {"fuel": "heptane", "o2_frac": 0.20}, "variable": "co2_frac"})
    assert r.status_code == 200
    pts = r.json()["points"]
    assert any(p["probability"] is None and not p["in_domain"] for p in pts)
    assert any(p["probability"] is not None for p in pts)


def test_effects_by_variable():
    body = client.get("/effects/co2_frac").json()
    assert body["variable"] == "co2_frac" and body["rows"] and body["not_tested"]
    assert client.get("/effects/horizon_s").status_code == 422          # not an explainable variable
    assert client.get("/effects/nonsense").status_code == 422


def test_duration_gas_flame_has_curve_and_median():
    body = client.post("/duration", json={"fuel": "butane", "o2_frac": 0.40}).json()
    assert body["in_domain"] and body["curve"] and body["median_s"] and body["observed"]


def test_compare_plan_vs_change():
    body = client.post("/compare", json={"plan": {"fuel": "butane", "o2_frac": 0.21},
                                         "change": {"fuel": "butane", "o2_frac": 0.40}}).json()
    assert body["verdict"] == "worse" and body["changes"][0]["variable"] == "o2_frac"
