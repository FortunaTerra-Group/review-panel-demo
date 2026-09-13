# Performance lens, run 2

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 2 on `feat/per-tenant-rate-limit-folded` at commit `7a6e499`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Measurements complete. Report follows.

**Suite:** 18 passed in 0.64s (`python -m pytest -q`, sqlite + fakeredis).

### What I measured (probe, not impressions)

Per-request cost on `/v1/metrics/summary`, counting SQL via `CaptureQueriesContext` and Redis commands via a counting wrapper around the fakeredis client:

| Path | SQL | Redis | Delta vs `main` |
|---|---|---|---|
| Cold (first request for tenant) | 3 (`api_keys`, `tenant_tiers`, `metric_points`) | 1 `EVAL` | +1 SQL (once per tenant per 60s), +1 Redis RT |
| Warm | 2 (`api_keys`, `metric_points`) | 1 `EVAL` | **+0 SQL**, +1 Redis RT |
| Unlimited tier | 2 | 0 | +0 / +0 (bypass before any I/O, as the contract requires) |
| 429 | 1 (`api_keys`) | 1 `EVAL` | view short-circuited |

The `api_keys` query is pre-existing auth cost on `main`. The contract's DO-NOT hot-path clause holds for the single-tenant warm case: zero new synchronous DB round trips.

Sorted set shape at `limit=5000`: `ZCARD=5000`, TTL=60, member = 32-hex uuid. TTL is refreshed only on admit; I set TTL to 5, fired a rejected request, TTL stayed 5. So a tenant stuck at 429 does not keep its key alive; the key dies `window` seconds after its last admit, by which point every member is outside the window anyway. No leak. Negative tier lookups (no row) are cached too: 20 cold tenants = 20 tier queries, second request for a no-row tenant = 0.

### Findings

**F1: Tier cache silently collapses above 300 active tenants, putting the DB query back on the hot path.**
`acme_metrics/settings.py:37` (`LocMemCache` with no `OPTIONS`), consumed by `metrics/ratelimit/tiers.py:15-20`.
Django's `LocMemCache` defaults to `MAX_ENTRIES=300`, `CULL_FREQUENCY=3` (verified: `cache._max_entries == 300`). Measured: 400 tenants, all warmed, second pass over the same 400 issued **400 `tenant_tiers` queries (100% miss)**. That is the exact regression the contract forbids ("must be cached, not queried live"), and `tests/test_rate_limit.py:64-70` cannot see it because it is single-tenant. Refutation attempt: I checked whether the cyclic access pattern in my probe was unfairly worst-case for LRU. It is worst-case, but the cull (evict 1/3 whenever full) means the effective cap oscillates 200 to 300, so any tenant population over about 300 active within a TTL sees a material miss rate, not just the cyclic pattern. Kept.
Fix: size the cache to the tenant population, from config, e.g. `CACHES = {"default": {"BACKEND": ..., "OPTIONS": {"MAX_ENTRIES": int(os.environ.get("TIER_CACHE_MAX_ENTRIES", "10000"))}}}`, and add the test that catches it (warm N > MAX_ENTRIES tenants, second pass asserts zero `tenant_tiers` queries). Alternative: Django's built-in `RedisCache` on the provisioned instance (see F2 tradeoff).

**F2: `invalidate()` is per-process; the test double hides that.**
`metrics/ratelimit/tiers.py:24-26`, `tests/test_rate_limit.py:87-95`, `acme_metrics/settings.py:37`.
`LocMemCache` is process-local. Under any multi-worker WSGI deployment (`acme_metrics/wsgi.py` exists), the wave-2 admin endpoint's `tiers.invalidate()` clears only the worker that served the admin call; every other worker/host enforces the stale limit for up to `CACHE_TTL_SECONDS=60`. The test named `..._takes_effect_without_waiting_for_ttl` passes only because the test runner is one process. Refutation attempt: is 60s eventual consistency acceptable for an admin limit change? Plausibly yes, and it is the cheaper choice on the hot path (in-process lookup costs about 0; a Redis-backed Django cache would add a second Redis RT per request, roughly doubling the branch's added Redis cost). Kept as a change because the docstring and test assert a property the deployed topology does not have.
Fix (choose one, state it): (a) keep LocMem, document "convergence bound = `CACHE_TTL_SECONDS` per process; `invalidate()` is a local fast-path" in the `invalidate` docstring and rename/annotate the test; or (b) switch `CACHES["default"]` to `django.core.cache.backends.redis.RedisCache` on `REDIS_URL` (no new datastore, satisfies the contract) and accept +1 Redis GET per request. I recommend (a) for the spine; (b) if wave 2 needs sub-minute effect.

**F3: Redis blackhole costs 5 s of a worker per request before the 503; branch extends that to all tenant traffic.**
`metrics/middleware/rate_limit.py:29-31`, `metrics/redis_client.py:9`.
Measured with the real redis-py 8.1 client: refused port: `ConnectionError` in 0.00 s (fine); unreachable host: `TimeoutError` after **5.01 s** (redis-py's default `socket_connect_timeout`). On `main`, authenticated requests never touched Redis, so a Redis blackhole only stalled public paths. On this branch every tenant request queues behind a 5 s connect attempt, then returns `Retry-After: 1`, which invites the client back before any worker has freed. During such an outage, capacity is roughly workers / 5 s. The outage test (`tests/test_rate_limit.py:106-116`) monkeypatches `allow` to raise instantly, so the real client's timeout is never exercised.
Fix: bound the client in `redis_client.py`: `redis.Redis.from_url(settings.REDIS_URL, decode_responses=False, socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS, socket_timeout=settings.REDIS_TIMEOUT_SECONDS)` with a sub-second default from env. It touches a `main` file, but the branch is what puts Redis on the authenticated hot path, so the branch owns the consequence; the global limiter benefits for free. Optionally raise `Retry-After` on the 503 to match the measured stall.

**F4 (note): Memory per tenant is O(limit), acceptable at default, worth a stated ceiling.**
`metrics/ratelimit/windows.py:4-13`. Estimated (not measured; fakeredis does not report real memory): at the default limit of 100 the zset stays listpack-encoded (<=128 entries, members <=64 B) at roughly 4 to 5 KB per active tenant; at 5000 it is skiplist-encoded at roughly 130 to 150 B/entry, about 0.7 MB peak per tenant, transient. A tier row with `rate_limit=1_000_000` would be about 140 MB per hot tenant. The contract asked for a sliding window, so this is the right shape for the spine; flag that very large tiers should either use the unlimited list or a sliding-window-counter approximation. Per-request Lua work is O(log N + trimmed) and runs on rejected requests too, which is correct and cheap.

**F5 (note): `redis.eval` ships the script body on every call.**
`metrics/ratelimit/windows.py:18`. About 280 bytes upstream per request plus a server-side SHA1. `redis.register_script(SLIDING_WINDOW)` gives EVALSHA with automatic NOSCRIPT fallback at no code cost. Not required.

**F6 (note): `Retry-After` on 429 is the whole window.**
`metrics/middleware/rate_limit.py:33`. Conservative and safe under burst (clients back off 60 s), but the exact wait (`oldest score + window - now`) is one O(1) `ZRANGE` away inside the same script if quota utilisation ever matters. Not required.

**F7 (note, correctness-adjacent): window clock is the app host's, not Redis's.**
`metrics/middleware/rate_limit.py:29` passes `clock.now()`. With multiple app hosts, clock skew of s seconds makes the "any span of WINDOW_SECONDS" guarantee fuzzy by s. `redis.call('TIME')` inside the script is the usual fix but conflicts with the injectable clock design. Flagging for the architecture lens; no perf cost either way.

### Four absence checks

1. **Rollback:** remove the `MIDDLEWARE` line (`settings.py:19`) and the `CACHES` line (`:37`). No migration, no schema change. Redis keys self-expire within `RATE_LIMIT_WINDOW_SECONDS`; LocMem dies with the process. Named, adequate.
2. **Coverage:** unlimited bypass: `test_unlimited_tier_is_never_throttled`; public path: `test_public_path_is_not_counted_and_touches_no_db`; no tier row: `test_tenant_without_tier_row_gets_exactly_the_default_limit`; NULL row: `test_null_rate_limit_row_falls_back_to_default`; window semantics: `test_accepted_rate_over_any_window_span`; outage: `test_redis_outage_fails_closed_with_503`. **Uncovered:** cache behaviour above `MAX_ENTRIES` (F1), real-client timeout path (F3), `rate_limit=0` (every request 429 by `ZCARD < 0`; reasonable, untested).
3. **One owner:** limit derives from `tenant_tiers` via `tiers.limit_for` only; default and unlimited list live in settings only; consumed in one place each. Key prefix `ratelimit:` is a repeated literal across `windows.py:18` and `global_limit.py:21`; a tenant_id shaped `global:<int>` would share the global counter's key. Far-fetched, architecture lens territory.
4. **Bad day:** empty tenant: bypass (tested via `/healthz`); malformed: tenant_id comes from the DB, not the request; duplicate/concurrent: single atomic Lua script, uuid member, verified no collision at 5000 admits; exempt tenant and legacy NULL row: tested. Cache-miss storm on restart: one indexed single-row query per tenant, bounded (measured 20/20, then 0).

### Verdict

**SIGN-WITH-CHANGE.** The warm hot path adds zero DB queries and exactly one Redis round trip versus `main`; the unlimited tier adds nothing; the sorted set is leak-free with correct TTL behaviour. Required before push:

1. **F1**: configure `MAX_ENTRIES` on the tier cache (env-driven) and add the >300-tenant zero-query test; as written, the contract's hot-path clause fails once the active tenant count exceeds 300 (measured 100% miss at 400).
2. **F3**: bound Redis connect/socket timeouts on the shared client; the branch moves a measured 5 s-per-request outage stall from public paths onto every tenant request while advertising `Retry-After: 1`.
3. **F2**: state the invalidation semantics honestly: either document the per-process, <=TTL convergence and rename the test, or move `CACHES` to Django's `RedisCache`.

F4 to F7 are notes, not conditions.
