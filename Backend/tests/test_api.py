"""API tests against the local database (skipped when DATABASE_URL is not reachable)."""
import pytest
from fastapi.testclient import TestClient

from api import db
from api.main import app

pytestmark = pytest.mark.skipif(not db.ping(), reason="database not reachable")
client = TestClient(app)


def test_health():
    r = client.get("/health").json()
    assert r["database"] == "ok" and set(r["services"]) == {"rag", "llm"}


def test_stats_and_investigations():
    s = client.get("/stats").json()
    assert s["n_investigations"] == 24 and s["n_tables_mapped"] == 22
    inv = client.get("/investigations").json()
    assert len(inv) == 24 and {"psi_id", "doi_url", "n_tests"} <= set(inv[0])
    assert client.get("/investigations/psi-69").json()["acronym"] == "FLEX"
    assert client.get("/investigations/PSI-999").status_code == 404


def test_tests_measurements_and_steps():
    flex = client.get("/tests", params={"psi_id": "PSI-69"}).json()
    assert len(flex) == 274 and all(t["test_id"].startswith("PSI-69:") for t in flex)
    m = client.get("/tests/PSI-69:114/measurements").json()
    pressure = next(x for x in m if x["variable"] == "pressure_kpa")
    assert pressure["flag"] == "sensor_error" and pressure["value_num"] == 0    # R12: marked, value kept
    assert client.get("/tests/PSI-69:114").json()["is_valid"] is False
    assert len(client.get("/tests/PSI-25:B21/steps").json()) >= 1
    assert client.get("/tests/nope/measurements").status_code == 404


def test_model_functions_follow_the_contract():
    p = client.post("/predict", json={"family": "flex", "o2_frac": 0.21, "pressure_kpa": 101.3}).json()
    assert p[0]["model_version"].startswith("placeholder") and "in_domain" in p[0]
    ood = client.post("/predict", json={"family": "solids", "o2_frac": 0.34, "material": "sibal"}).json()
    assert ood[0]["in_domain"] is False                                     # no solids above 22.7 % O2
    assert ood[0]["probability"] is None                                    # §2.1: no extrapolation
    assert len(client.post("/coverage", json={"family": "cfig", "o2_frac": 0.4}).json()) > 0
    assert client.post("/predict", json={"o2_frac": 3}).status_code == 422  # validated input


def test_ask():
    a = client.post("/ask", json={"text": "Would cotton fabric burn at 34% oxygen in a lunar habitat?"}).json()
    assert a["understood"]["material"] == "sibal" and a["understood"]["o2_frac"] == 0.34
    assert a["predictions"][0]["in_domain"] is False
    assert "outside the NASA data" in a["text"]


def test_documents_search_without_service():
    assert client.get("/documents/search", params={"q": "pmma flammability"}).status_code in (503, 502, 200)
