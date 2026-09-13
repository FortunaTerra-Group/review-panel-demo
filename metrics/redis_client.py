from functools import lru_cache

import redis
from django.conf import settings


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    # Bounded so that a stalled Redis fails closed quickly instead of holding a worker for the
    # library default. Every authenticated request crosses this client.
    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=False,
        socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
        socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
    )
