import uuid

import redis
from django.conf import settings
from django.http import JsonResponse

from metrics.ratelimit import clock, tiers
from metrics.ratelimit.windows import allow
from metrics.redis_client import get_redis


class TenantRateLimitMiddleware:
    """Per-tenant request limit. Runs after ApiKeyAuthMiddleware, which sets request.tenant_id.

    Policy decisions recorded here: a request with no tenant is a public-path request (auth has
    already rejected every other keyless request), so it is not counted; Redis failure fails
    closed with a 503, because an unmetered API is the outage the contract exists to prevent.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = request.tenant_id  # raises if auth did not run first: a mis-ordered chain must fail loudly
        if tenant is None or tenant in settings.UNLIMITED_TIER_TENANTS:
            return self.get_response(request)
        window = settings.RATE_LIMIT_WINDOW_SECONDS
        limit = tiers.limit_for(tenant)
        try:
            allowed = allow(get_redis(), tenant, clock.now(), window, limit, uuid.uuid4().hex)
        except redis.RedisError:
            return JsonResponse({"error": "rate_limiter_unavailable"}, status=503, headers={"Retry-After": "1"})
        if not allowed:
            return JsonResponse({"error": "rate_limited"}, status=429, headers={"Retry-After": str(window)})
        return self.get_response(request)
