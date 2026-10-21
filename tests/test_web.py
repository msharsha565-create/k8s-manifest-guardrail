from pathlib import Path

from starlette.testclient import TestClient

from k8s_guardrail import web

FIXTURES = Path(__file__).parent / "fixtures"
client = TestClient(web.app)


def test_index():
    assert "k8s-guardrail" in client.get("/").text


def test_check_valid_and_invalid():
    ok = client.post("/api/check", json={"manifest": (FIXTURES / "valid_deployment.yaml").read_text()})
    bad = client.post("/api/check", json={"manifest": (FIXTURES / "hallucinated_deployment.yaml").read_text()})
    assert ok.json()["is_valid"] is True
    assert bad.json()["is_valid"] is False


def test_check_rejects_oversized_input():
    assert client.post("/api/check", json={"manifest": "a" * 100_001}).status_code == 400
