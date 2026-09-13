import time

from django.conf import settings
from django.http import JsonResponse

from metrics.redis_client import get_redis


class GlobalRateLimitMiddleware:
    """Coarse protection for unauthenticated (public) routes. Runs after auth, so a request that
    reaches it with no tenant is a public-path request by construction. Out of scope for the
    per-tenant change."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.tenant_id is not None:
            return self.get_response(request)
        window = settings.RATE_LIMIT_WINDOW_SECONDS
        key = f"ratelimit:global:{int(time.time() // window)}"
        with get_redis().pipeline(transaction=True) as p:
            p.incr(key)
            p.expire(key, window, nx=True)
            count, _ = p.execute()
        if count > settings.GLOBAL_UNAUTHENTICATED_LIMIT:
            return JsonResponse({"error": "rate_limited"}, status=429)
        return self.get_response(request)
