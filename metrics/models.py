from django.db import models


class ApiKey(models.Model):
    key = models.CharField(max_length=64, unique=True)
    tenant_id = models.CharField(max_length=64, db_index=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        db_table = "api_keys"


class TenantTier(models.Model):
    tenant_id = models.CharField(max_length=64, unique=True)
    tier = models.CharField(max_length=32)
    # Requests per window. NULL means the tier row exists but no limit was ever configured.
    rate_limit = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "tenant_tiers"


class MetricPoint(models.Model):
    tenant_id = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=128)
    value = models.FloatField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "metric_points"
