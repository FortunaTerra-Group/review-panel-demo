import time

from django.conf import settings
from django.http import JsonResponse

from metrics.models import TenantTier
from metrics.ratelimit.windows import window_key
from metrics.redis_client import get_redis


class TenantRateLimitMiddleware:
    """Per-tenant request limit. Runs after ApiKeyAuthMiddleware, which sets request.tenant_id."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = request.tenant_id
        limit = get_tenant_limit(tenant)                      # DB read, see below
        key = window_key(tenant, time.time(), settings.RATE_LIMIT_WINDOW_SECONDS)
        redis = get_redis()
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, settings.RATE_LIMIT_WINDOW_SECONDS)
        if count > limit:
            return JsonResponse({"error": "rate_limited"}, status=429)
        return self.get_response(request)


def get_tenant_limit(tenant_id):
    try:
        return TenantTier.objects.get(tenant_id=tenant_id).rate_limit
    except TenantTier.DoesNotExist:
        return settings.DEFAULT_RATE_LIMIT
