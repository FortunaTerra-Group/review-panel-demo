from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="ApiKey",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("key", models.CharField(max_length=64, unique=True)),
                ("tenant_id", models.CharField(db_index=True, max_length=64)),
                ("revoked", models.BooleanField(default=False)),
            ],
            options={"db_table": "api_keys"},
        ),
        migrations.CreateModel(
            name="TenantTier",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tenant_id", models.CharField(max_length=64, unique=True)),
                ("tier", models.CharField(max_length=32)),
                ("rate_limit", models.IntegerField(blank=True, null=True)),
            ],
            options={"db_table": "tenant_tiers"},
        ),
        migrations.CreateModel(
            name="MetricPoint",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("tenant_id", models.CharField(db_index=True, max_length=64)),
                ("name", models.CharField(max_length=128)),
                ("value", models.FloatField()),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "metric_points"},
        ),
    ]
