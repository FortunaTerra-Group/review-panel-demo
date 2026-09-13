import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "metrics",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "metrics.middleware.auth.ApiKeyAuthMiddleware",
    "metrics.middleware.global_limit.GlobalRateLimitMiddleware",
    "metrics.middleware.rate_limit.TenantRateLimitMiddleware",
]

ROOT_URLCONF = "acme_metrics.urls"
WSGI_APPLICATION = "acme_metrics.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DATABASE_PATH", BASE_DIR / "acme.sqlite3"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_TIMEOUT_SECONDS = float(os.environ.get("REDIS_TIMEOUT_SECONDS", "0.5"))

# Tier-limit cache. Process-local by design; workers converge within CACHE_TTL_SECONDS. Sized
# from config because LocMemCache's default of 300 entries would put the tier query back on the
# hot path once more than ~300 tenants are active in a window.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "OPTIONS": {"MAX_ENTRIES": int(os.environ.get("TIER_CACHE_MAX_ENTRIES", "10000"))},
    }
}

# Rate limiting. Per-tenant limits live in the tenant_tiers table; these are the fallbacks.
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
DEFAULT_RATE_LIMIT = int(os.environ.get("DEFAULT_RATE_LIMIT", "100"))
GLOBAL_UNAUTHENTICATED_LIMIT = int(os.environ.get("GLOBAL_UNAUTHENTICATED_LIMIT", "10000"))

# Fixed, known list of tenants on the unlimited tier. Enforcement must never apply to them.
UNLIMITED_TIER_TENANTS = frozenset(
    t.strip()
    for t in os.environ.get("UNLIMITED_TIER_TENANTS", "tenant-enterprise-01,tenant-enterprise-02").split(",")
    if t.strip()
)
