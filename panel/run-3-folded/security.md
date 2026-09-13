# Security lens, run 3

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 3 on `feat/per-tenant-rate-limit-folded` at commit `84efc3a`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Suite: 21 passed (1.86s). I also ran 9 probe tests through Django's test client plus a live-socket stall harness (temp files removed; tree clean).

## Findings

**1. The client-timeout control has no test (coverage absence, the DoS control of the diff).**
`metrics/redis_client.py:11-16` sets `socket_connect_timeout`/`socket_timeout` from `settings.REDIS_TIMEOUT_SECONDS`. `tests/conftest.py:13` monkeypatches `redis.Redis.from_url` with `lambda cls, *a, **k: client`, so the kwargs are swallowed in every test; nothing asserts they reach the factory. This is the one control that turns a stalled Redis from a multi-second worker hold into 0.5s. I verified it empirically outside the suite (below), but a regression (e.g., someone dropping the kwargs or renaming the setting) is invisible to CI.
Fix: one test that patches `from_url` with a recorder and asserts `k["socket_timeout"] == k["socket_connect_timeout"] == settings.REDIS_TIMEOUT_SECONDS` after `get_redis.cache_clear(); get_redis()`.

**2. Wave-2 precedence is correct but unpinned.** `metrics/middleware/rate_limit.py:24` checks `UNLIMITED_TIER_TENANTS` before `tiers.limit_for`, so a table row for an unlimited tenant is ignored (probe: `rate_limit=1` row for `tenant-enterprise-01`, 10/10 requests 200). That matches the contract, but the wave-2 admin endpoint will write rows the limiter never reads; no test pins the precedence. Also `rate_limit=0` and negative values block every request (probe confirmed: first request 429). Recommend a precedence test now and, for wave 2, reject/flag writes for tenants on the unlimited list and validate `rate_limit >= 1` (or define 0 as "blocked" explicitly).

**3. Cache-key characters (low).** `metrics/ratelimit/tiers.py:9-10` interpolates the raw `tenant_id` (DB-controlled, up to 64 arbitrary chars). On LocMemCache this only emits `CacheKeyWarning` (probe: `"a b"`, `"tenant\nnewline"`, `"']; drop"` each warned; behaviour correct and isolated). If `CACHES` is later moved to memcached to get cross-worker invalidation, those keys raise `InvalidCacheKey` and the limiter 500s for that tenant. Cheap hardening: hash or `urllib.parse.quote` the tenant id in `_cache_key`.

**4. Clock is the app server's, not Redis's (design note).** `windows.py:16` passes `clock.now()` as the ZSET score and trim bound. Across multiple app servers, skew shrinks or stretches the effective window by the skew amount (probe: a server 61s "ahead" ages out another's burst immediately). Not attacker-controllable; NTP-scale skew is negligible against a 60s window. If it matters later, `redis.call('TIME')` inside the script removes it at the cost of the injectable clock seam.

**5. `UNLIMITED_TIER_TENANTS=""` set-but-empty disables the exemption** (`settings.py:54-58`; probe: enterprise tenant 429 with an empty frozenset). Deliberate semantics, but it is the one env mistake that violates "must never apply to them." Consider failing at boot or logging loudly when the env var is present and yields an empty set.

**6. Out of scope, pre-existing, worth a backlog item:** invalid or revoked API-key attempts are metered by nothing. `auth.py:20-23` returns 401 before `GlobalRateLimitMiddleware`, and the global limiter only sees `tenant_id is None` on public paths, so key brute-forcing is unbounded (probe: 100 x 401, zero `ratelimit:*` keys). Not introduced here; CONTRACT forbids touching the global limiter.

**7. Observability (note).** 429 and 503 decisions emit no log line; during a Redis outage the only signal is client-visible 503s.

## Candidates I tried to refute and dropped

- **"redis-py 8.x default retries multiply the stall."** redis-py 8.1.0's `Redis.__init__` default is `Retry(ExponentialWithJitterBackoff, retries=N)`, so I expected a 0.5s timeout to become several seconds per request. Refuted: `from_url` builds the client via `ConnectionPool.from_url`, whose connections get `Retry(NoBackoff(), 0)`. Measured with a real accepting-but-silent socket: stalled 0.50s, connection refused 0.00s, blackholed connect 0.50s, `conn.retry._retries == 0`; the same client without the diff's kwargs held for 5.01s. The stall behaviour is sound.
- **Lua injection.** Tenant id goes in `KEYS[1]` as a separate argument; `ARGV` are numbers coerced with `tonumber` plus a uuid hex; nothing is string-concatenated into the script. Probed `*`, `:`, newline, quotes, emoji, and `ratelimit:global:1` as tenant ids: each got its own `ratelimit:tenant:` key, no collision with `ratelimit:global:*`, script never errored.
- **Cross-tenant / memory DoS.** Keys exist only for tenants that pass auth (DB-controlled set); each ZSET holds at most `limit` members (admit only when `ZCARD < limit`) and carries a TTL (asserted by `test_tenant_key_carries_a_ttl`). Atomicity across two clients confirmed: `[200,200,200,429,429,429]`.
- **Middleware ordering.** Reordering `TenantRateLimitMiddleware` ahead of auth yields 500 on every route including `/healthz` (probe), so a mis-ordered chain cannot silently skip enforcement. Unlimited tenants do zero Redis/DB I/O in the limiter, so a Redis outage only affects them through the worker pool, bounded at 0.5s per limited request.

## Four absence checks

1. **Rollback:** removing the `MIDDLEWARE` line reverts enforcement; no schema change; Redis keys self-expire within the window; tier cache dies with the process; new env vars all have defaults. No runtime kill switch, so rollback is a redeploy; acceptable here.
2. **Coverage:** every decision path has a named test except the client timeouts (finding 1) and the unlimited-over-table precedence (finding 2).
3. **One owner:** unlimited list derived once from env; public paths owned by `auth.PUBLIC_PATHS` and consumed as `tenant is None` rather than copied; key namespace and cache key each defined once in code (test literals are acceptable duplication).
4. **Bad day:** None tenant, malformed tenant ids, NULL/0/negative limits, duplicate concurrent requests, stalled/refused/blackholed Redis, empty and whitespace-padded env list all examined above; exempt-tenant and legacy-row tests exist (`test_unlimited_tier_is_never_throttled`, `test_null_rate_limit_row_falls_back_to_default`).

## Verdict

**SIGN-WITH-CHANGE**: required: add the test in finding 1 that asserts `REDIS_TIMEOUT_SECONDS` reaches the Redis client factory. Recommended (not blocking): the precedence test in finding 2. Findings 3-7 are notes for wave 2 and the backlog.
