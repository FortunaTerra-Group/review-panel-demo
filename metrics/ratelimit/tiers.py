from django.conf import settings
from django.core.cache import cache

from metrics.models import TenantTier

CACHE_TTL_SECONDS = 60


def _cache_key(tenant_id: str) -> str:
    return f"tier-limit:{tenant_id}"


def limit_for(tenant_id: str) -> int:
    """Per-tenant limit from the tier table, cached. The request hot path reads the cache only."""
    cached = cache.get(_cache_key(tenant_id))
    if cached is not None:
        return cached
    row = TenantTier.objects.filter(tenant_id=tenant_id).values_list("rate_limit", flat=True).first()
    limit = row if row is not None else settings.DEFAULT_RATE_LIMIT
    cache.set(_cache_key(tenant_id), limit, CACHE_TTL_SECONDS)
    return limit


def invalidate(tenant_id: str) -> None:
    """Drop the cached limit in this process. The cache is process-local, so other workers keep
    the old value until CACHE_TTL_SECONDS elapses; the wave-2 admin endpoint may call this as a
    local fast path but must not present it as immediate propagation."""
    cache.delete(_cache_key(tenant_id))
