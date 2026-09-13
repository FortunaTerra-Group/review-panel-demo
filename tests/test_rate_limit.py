import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from metrics.ratelimit import clock

URL = "/v1/metrics/summary"


class FakeClock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(clock, "now", c)
    cache.clear()
    return c


def get(client, key):
    return client.get(URL, HTTP_X_API_KEY=key)


@pytest.mark.django_db
def test_under_limit_passes(client, tenants):
    assert all(get(client, tenants["retail"]).status_code == 200 for _ in range(3))


@pytest.mark.django_db
def test_over_limit_is_429(client, tenants):
    for _ in range(3):
        get(client, tenants["retail"])
    assert get(client, tenants["retail"]).status_code == 429


@pytest.mark.django_db
def test_unlimited_tier_is_never_throttled(client, tenants):
    assert all(get(client, tenants["enterprise"]).status_code == 200 for _ in range(settings.DEFAULT_RATE_LIMIT + 5))


@pytest.mark.django_db
def test_accepted_rate_over_any_window_span(client, tenants, fixed_clock):
    # Contract: the accepted rate over ANY span of RATE_LIMIT_WINDOW_SECONDS stays within the limit.
    # The clock is aligned to a fixed-window bucket edge first, so that a fixed-window counter
    # (which resets at the edge) fails this test and a sliding window passes it.
    w = settings.RATE_LIMIT_WINDOW_SECONDS
    fixed_clock.t = float((int(fixed_clock.t) // w) * w)
    fixed_clock.advance(w - 1)                       # last second of bucket N
    for _ in range(3):
        assert get(client, tenants["retail"]).status_code == 200
    fixed_clock.advance(2)                           # bucket N+1: a fixed window would reset here
    assert get(client, tenants["retail"]).status_code == 429
    fixed_clock.advance(w - 3)                       # burst + w - 1: still inside the trailing window
    assert get(client, tenants["retail"]).status_code == 429
    fixed_clock.advance(1)                           # burst + w: aged out
    assert get(client, tenants["retail"]).status_code == 200


@pytest.mark.django_db
def test_tenant_key_carries_a_ttl(client, tenants, fake_redis):
    get(client, tenants["retail"])
    assert 0 < fake_redis.ttl("ratelimit:tenant:tenant-retail") <= settings.RATE_LIMIT_WINDOW_SECONDS


@pytest.mark.django_db
def test_hot_path_issues_no_db_query_for_the_limit_after_warm_up(client, tenants):
    get(client, tenants["retail"])                   # warm the tier cache
    with CaptureQueriesContext(connection) as ctx:
        get(client, tenants["retail"])
    tier_queries = [q["sql"] for q in ctx.captured_queries if "tenant_tiers" in q["sql"]]
    assert tier_queries == []


@pytest.mark.django_db
def test_tenant_without_tier_row_gets_exactly_the_default_limit(client, tenants):
    from metrics.models import ApiKey
    ApiKey.objects.create(key="key-new", tenant_id="tenant-new")
    assert all(get(client, "key-new").status_code == 200 for _ in range(settings.DEFAULT_RATE_LIMIT))
    assert get(client, "key-new").status_code == 429


@pytest.mark.django_db
def test_null_rate_limit_row_falls_back_to_default(client, tenants):
    assert all(get(client, tenants["legacy"]).status_code == 200 for _ in range(settings.DEFAULT_RATE_LIMIT))
    assert get(client, tenants["legacy"]).status_code == 429


@pytest.mark.django_db
def test_admin_invalidation_takes_effect_in_this_process_without_waiting_for_ttl(client, tenants):
    from metrics.models import TenantTier
    from metrics.ratelimit import tiers
    get(client, tenants["retail"])
    TenantTier.objects.filter(tenant_id="tenant-retail").update(rate_limit=1)
    tiers.invalidate("tenant-retail")
    get(client, tenants["retail"])
    assert get(client, tenants["retail"]).status_code == 429


def test_public_path_is_not_counted_and_touches_no_db(client, fake_redis):
    # No django_db mark on purpose: this must pass with database access blocked.
    for _ in range(settings.DEFAULT_RATE_LIMIT + 5):
        assert client.get("/healthz").status_code == 200
    assert fake_redis.keys("ratelimit:tenant:*") == []


@pytest.mark.django_db
def test_redis_outage_fails_closed_with_503(client, tenants, monkeypatch):
    import redis as redis_lib
    from metrics.middleware import rate_limit

    def boom(*a, **k):
        raise redis_lib.ConnectionError("redis down")

    monkeypatch.setattr(rate_limit, "allow", boom)
    r = get(client, tenants["retail"])
    assert r.status_code == 503 and r["Retry-After"] == "1"


@pytest.mark.django_db
def test_redis_outage_through_the_real_client_path_is_a_503(client, tenants, fake_redis):
    fake_redis.connection_pool.connection_kwargs["server"].connected = False
    r = get(client, tenants["retail"])
    assert r.status_code == 503


@pytest.mark.django_db
def test_hot_path_stays_query_free_with_many_active_tenants(client, tenants):
    from metrics.models import ApiKey
    n = 400
    ApiKey.objects.bulk_create([ApiKey(key=f"key-{i}", tenant_id=f"tenant-{i}") for i in range(n)])
    for i in range(n):
        get(client, f"key-{i}")                      # warm every tenant's limit
    with CaptureQueriesContext(connection) as ctx:
        for i in range(n):
            get(client, f"key-{i}")
    assert [q["sql"] for q in ctx.captured_queries if "tenant_tiers" in q["sql"]] == []


@pytest.mark.django_db
def test_429_carries_retry_after(client, tenants):
    for _ in range(3):
        get(client, tenants["retail"])
    r = get(client, tenants["retail"])
    assert r.status_code == 429 and r["Retry-After"] == str(settings.RATE_LIMIT_WINDOW_SECONDS)
