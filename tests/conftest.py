import fakeredis
import pytest

from metrics import redis_client
from metrics.models import ApiKey, TenantTier


@pytest.fixture(autouse=True)
def clear_tier_cache():
    from django.core.cache import caches
    caches["tier_limits"].clear()
    yield
    caches["tier_limits"].clear()


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Every test gets its own Redis. Patched at the connection factory so every importer sees it."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server)
    monkeypatch.setattr(redis_client.redis.Redis, "from_url", classmethod(lambda cls, *a, **k: client))
    redis_client.get_redis.cache_clear()
    yield client
    redis_client.get_redis.cache_clear()


@pytest.fixture
def tenants(db):
    ApiKey.objects.create(key="key-retail", tenant_id="tenant-retail")
    ApiKey.objects.create(key="key-ent-01", tenant_id="tenant-enterprise-01")
    ApiKey.objects.create(key="key-legacy", tenant_id="tenant-legacy")
    TenantTier.objects.create(tenant_id="tenant-retail", tier="starter", rate_limit=3)
    TenantTier.objects.create(tenant_id="tenant-legacy", tier="starter", rate_limit=None)
    return {"retail": "key-retail", "enterprise": "key-ent-01", "legacy": "key-legacy"}
