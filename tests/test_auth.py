import pytest


@pytest.mark.django_db
def test_known_key_reaches_the_view(client, tenants):
    r = client.get("/v1/metrics/summary", HTTP_X_API_KEY=tenants["retail"])
    assert r.status_code == 200
    assert r.json()["tenant"] == "tenant-retail"


@pytest.mark.django_db
def test_unknown_key_is_rejected(client, tenants):
    assert client.get("/v1/metrics/summary", HTTP_X_API_KEY="nope").status_code == 401


@pytest.mark.django_db
def test_healthz_needs_no_key(client):
    assert client.get("/healthz").status_code == 200
