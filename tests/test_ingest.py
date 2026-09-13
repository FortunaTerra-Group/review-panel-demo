import pytest


@pytest.mark.django_db
def test_ingest_then_summary(client, tenants):
    for v in (1.0, 3.0):
        r = client.post("/v1/metrics/ingest", data={"name": "latency_ms", "value": v},
                        content_type="application/json", HTTP_X_API_KEY=tenants["retail"])
        assert r.status_code == 202
    body = client.get("/v1/metrics/summary", HTTP_X_API_KEY=tenants["retail"]).json()
    assert body["metrics"] == [{"name": "latency_ms", "count": 2, "avg": 2.0}]


@pytest.mark.django_db
def test_ingest_rejects_malformed_body(client, tenants):
    r = client.post("/v1/metrics/ingest", data="not json", content_type="application/json",
                    HTTP_X_API_KEY=tenants["retail"])
    assert r.status_code == 400
