# Sliding window over Redis: one sorted set per tenant whose members are request timestamps.
# Trim, count, admit, and refresh the TTL happen in one script, so there is one round trip per
# request and no state is left behind if the process dies between steps.
SLIDING_WINDOW = """
local key, now, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, now, ARGV[4])
  redis.call('EXPIRE', key, window)
  return 1
end
return 0
"""


def allow(redis, tenant_id: str, now: float, window_seconds: int, limit: int, request_id: str) -> bool:
    """True if this request fits within `limit` over the trailing `window_seconds`."""
    return redis.eval(SLIDING_WINDOW, 1, f"ratelimit:tenant:{tenant_id}", now, window_seconds, limit, request_id) == 1
