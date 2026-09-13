import json

from django.db.models import Avg, Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from metrics.models import MetricPoint


@require_GET
def healthz(request):
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def ingest(request):
    try:
        body = json.loads(request.body or b"{}")
        name, value = body["name"], float(body["value"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "bad_request"}, status=400)
    MetricPoint.objects.create(tenant_id=request.tenant_id, name=name, value=value)
    return JsonResponse({"accepted": True}, status=202)


@require_GET
def summary(request):
    rows = (
        MetricPoint.objects.filter(tenant_id=request.tenant_id)
        .values("name")
        .annotate(count=Count("id"), avg=Avg("value"))
        .order_by("name")[:100]
    )
    return JsonResponse({"tenant": request.tenant_id, "metrics": list(rows)})
