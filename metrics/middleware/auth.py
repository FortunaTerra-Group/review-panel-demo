from django.http import JsonResponse

from metrics.models import ApiKey

PUBLIC_PATHS = {"/healthz"}


class ApiKeyAuthMiddleware:
    """Resolves the tenant from X-Api-Key. Everything downstream may rely on request.tenant_id."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant_id = None
        if request.path in PUBLIC_PATHS:
            return self.get_response(request)
        key = request.headers.get("X-Api-Key")
        if not key:
            return JsonResponse({"error": "missing_api_key"}, status=401)
        row = ApiKey.objects.filter(key=key, revoked=False).only("tenant_id").first()
        if row is None:
            return JsonResponse({"error": "invalid_api_key"}, status=401)
        request.tenant_id = row.tenant_id
        return self.get_response(request)
