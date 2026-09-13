import pytest
from django.conf import settings


@pytest.mark.django_db
def test_public_path_is_counted_with_a_window(client, fake_redis):
    client.get("/healthz")
    keys = fake_redis.keys("ratelimit:global:*")
    assert len(keys) == 1
    assert 0 < fake_redis.ttl(keys[0]) <= settings.RATE_LIMIT_WINDOW_SECONDS


@pytest.mark.django_db
def test_authenticated_requests_skip_the_global_counter(client, tenants, fake_redis):
    client.get("/v1/metrics/summary", HTTP_X_API_KEY=tenants["retail"])
    assert fake_redis.keys("ratelimit:global:*") == []
