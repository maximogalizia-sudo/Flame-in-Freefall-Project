"""Model endpoints backed by LC's `flame` package (no database needed). Acceptance criteria of PLAN_ASISTENTE M1."""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_predict_flex_in_domain():
    p = client.post("/predict", json={"fuel": "methanol", "o2_frac": 0.18, "co2_frac": 0.3}).json()
    assert p[0]["model_version"] == "lc-models-v1:flex_main"
    assert abs(p[0]["probability"] - 0.98) < 0.02 and p[0]["in_domain"] is True
    assert p[0]["range_sensitivity"] and p[0]["assumptions"]


def test_predict_unknown_combination_says_why():
    p = client.post("/predict", json={"fuel": "heptane", "o2_frac": 0.34, "co2_frac": 0.2, "pressure_kpa": 70.3}).json()
    assert p[0]["probability"] is None and p[0]["in_domain"] is False and p[0]["out_of_domain"]


def test_solids_are_evidence_only():
    p = client.post("/predict", json={"family": "solids", "o2_frac": 0.34, "material": "sibal"}).json()
    assert p[0]["in_domain"] is False and p[0]["probability"] is None
    assert len(client.post("/coverage", json={"family": "cfig", "o2_frac": 0.4}).json()) > 0
    assert client.post("/predict", json={"o2_frac": 3}).status_code == 422


def test_explain_get_and_post():
    assert client.get("/explain/flex").json()["models"][0]["coefficients"]
    assert client.post("/explain", json={"family": "solids"}).json()["evidence_summary"]


def test_ask_template_fallback():
    a = client.post("/ask", json={"text": "Would cotton fabric burn at 34% oxygen in a lunar habitat?"}).json()
    assert a["understood"]["material"] == "sibal" and a["understood"]["o2_frac"] == 0.34
    assert a["predictions"][0]["in_domain"] is False and "outside the NASA data" in a["text"]
    assert a["decision"] == "partial" and a["verification"] == ["assistant unavailable: template answer"]
