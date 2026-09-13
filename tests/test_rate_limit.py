import pytest

URL = "/v1/metrics/summary"


@pytest.mark.django_db
def test_under_limit_passes(client, tenants):
    for _ in range(3):
        assert client.get(URL, HTTP_X_API_KEY=tenants["retail"]).status_code == 200


@pytest.mark.django_db
def test_over_limit_is_429(client, tenants):
    for _ in range(3):
        client.get(URL, HTTP_X_API_KEY=tenants["retail"])
    assert client.get(URL, HTTP_X_API_KEY=tenants["retail"]).status_code == 429


@pytest.mark.django_db
def test_tenant_without_tier_row_uses_default_limit(client, tenants):
    from metrics.models import ApiKey
    ApiKey.objects.create(key="key-new", tenant_id="tenant-new")
    assert client.get(URL, HTTP_X_API_KEY="key-new").status_code == 200
