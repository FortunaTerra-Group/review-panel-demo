# Performance lens, run 3

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 3 on `feat/per-tenant-rate-limit-folded` at commit `84efc3a`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Suite: 21 passed (`python -m pytest -q`, Django 5.2.17, redis-py 8.1.0, fakeredis 2.38.0). Probe files were written to my scratchpad only; nothing in the repo was edited.

## What I measured

**Per-request cost (probe: CaptureQueriesContext + wrapped `execute_command` on the fake client)**

| Path | DB queries (tables) | Redis | vs main |
|---|---|---|---|
| authenticated, cold (first hit per tenant per process) | 3 (api_keys, tenant_tiers, metric_points) | 1 EVAL | +1 DB, +1 Redis |
| authenticated, warm, accepted | 2 (api_keys, metric_points) | 1 EVAL | +1 Redis |
| authenticated, warm, rejected 429 | 1 (api_keys) | 1 EVAL | view query saved |
| unlimited tier | 2 | 0 | +0 (checked before any I/O, `rate_limit.py:24`) |
| NULL tier row / no tier row, warm | 2 | 1 EVAL | default cached; no repeated miss |
| public `/healthz` | 0 | 1 pipeline (global limiter, pre-existing; my wrapper does not see pipelines) | +0 |
| bad key 401 | 1 | 0 | +0 |

The auth `api_keys` lookup runs before the limiter, so a rate-limited burst still costs one indexed point read per request. That ordering is imposed by the contract (tenant must be resolved first); noted, not a defect.

**Sorted-set memory per tenant (real Redis 7.4.9, db 15, temp key, deleted after; `MEMORY USAGE`)**

| limit | encoding | bytes / tenant | per member |
|---|---|---|---|
| 100 (default) | listpack | 6,232 | 62 |
| 128 | listpack | 7,256 | 57 |
| 129 | skiplist | 17,024 | 132 |
| 1,000 | skiplist | 118,232 | 118 |
| 10,000 | skiplist | 1,294,160 | 129 |

Bounded at O(limit) per tenant because rejected requests do not ZADD (`windows.py:7-11`). 10k active tenants at the default is about 62 MB; a single tenant at 1M/window is about 130 MB. EVAL round trip over loopback incl. Python client: 0.1 to 0.7 ms (noisy, shared box). Using 8 random bytes instead of `uuid4().hex` halves the default-tier footprint (3,672 B measured).

**TTL**: 60 after accepts, still 60 after 5 rejects, zcard unchanged. EXPIRE refreshes only on accept (`windows.py:9`), which is correct: the key dies exactly when its newest member would age out.

**Redis stall/outage (real redis-py 8.1.0, same kwargs as `redis_client.py:11-16`)**: connection refused: `ConnectionError` in 0.00 s; black-hole address: `TimeoutError` in 0.50 s. `from_url` builds the pool with `retry=None`, so connection default `Retry(NoBackoff(), retries=0)`; there is no hidden retry/backoff multiplying the bound. I went in expecting redis-py >= 6's 3-retry default to stretch 0.5 s to several seconds; it does not on this constructor path. Candidate finding refuted by measurement.

**Tier cache eviction**: with `MAX_ENTRIES=100` and 150 tenants accessed cyclically, second pass issued 150/150 tier queries. LocMemCache culls the LRU third on the set that overflows (`locmem._cull`/`_set`, confirmed in installed source), so a cyclic working set larger than the cap degrades to a 100% miss rate, not a proportional one.

## Findings

1. **The outage bound the PR claims is invisible to its own tests.** `metrics/redis_client.py:11-16` sets `socket_timeout`/`socket_connect_timeout` from `REDIS_TIMEOUT_SECONDS`; `tests/conftest.py:13` replaces `Redis.from_url` with `lambda cls, *a, **k: client`, which discards every kwarg. `tests/test_rate_limit.py:135-139` (the "real client path" outage test) flips `connected=False` on the fake server and raises immediately, so neither the 0.5 s bound nor the kwarg names are ever exercised. A typo'd kwarg would pass CI and raise `TypeError` (not `RedisError`, so not caught at `rate_limit.py:30`) on every authenticated request in production. Today the wiring is correct (verified: pool kwargs `{socket_timeout: 0.5, socket_connect_timeout: 0.5}`), but only because I checked it by hand. Fix: one test that calls the un-monkeypatched `get_redis()` (or `redis.Redis.from_url(settings.REDIS_URL, **the same kwargs)`, lazy, needs no server) and asserts `connection_pool.connection_kwargs["socket_timeout"] == settings.REDIS_TIMEOUT_SECONDS`.

2. **Tier-cache capacity cliff is silent.** `acme_metrics/settings.py:41-46` sizes LocMemCache at 10,000; `tiers.py:15-21` falls back to a live query on miss. Past the cap the hot-path clause of CONTRACT.md is violated at 100% of requests for the overflowing working set, with no log, metric, or test. The settings comment is honest about the mechanism, but nothing tells an operator when it has happened. Recommendation (not required for this PR, since the repo has no metrics substrate): count or sample-log misses in `limit_for`, and surface `TIER_CACHE_MAX_ENTRIES` in ops docs next to expected active-tenant counts. Also `CACHE_TTL_SECONDS = 60` at `tiers.py:6` is the only tunable in this feature not read from env, and it governs the steady-state tier-query rate (workers x active tenants / 60 s per process) and the invalidation lag; consider moving it beside the others in settings.

3. **`EVAL` ships 292 bytes of script per request** (`windows.py:18`). `redis.register_script` / `EVALSHA` cuts that to about 40 bytes with automatic fallback on NOSCRIPT. Negligible on loopback, measurable on a networked Redis at high rps. Note only.

4. **Memory is O(limit) per tenant** (table above). Fine at the default; a tier at 10^5 to 10^6/window is 13 to 130 MB and O(log N) skiplist work per request. Nothing in `TenantTier.rate_limit` (IntegerField) or the middleware bounds it. The wave-2 admin endpoint should validate an upper bound, or such tenants belong on the unlimited list. Note, with a number attached.

5. **Shared-client timeout changes the global limiter's stall behaviour.** `global_limit.py:22-25` uses the same `get_redis()` and has no `RedisError` handling. On main a stalled Redis hung the worker indefinitely; now it raises `TimeoutError` after 0.5 s, 500. Code in `global_limit.py` is untouched, so the contract's "do not change" is met literally, and the new behaviour is strictly better, but it is an unstated side effect on a do-not-touch component. Say so in the PR description.

6. **Fail-closed under a stall has no breaker.** While Redis is stalled, every authenticated request holds a worker 0.5 s before its 503 (`rate_limit.py:29-30`); sync workers cap at 2 x workers rps. The policy is explicit in the docstring and correct per the contract; a short-lived breaker is the natural follow-up if the 503 storm is a concern. Note.

Things I checked and found sound: unlimited tier costs zero I/O; one round trip per request, trim/count/admit/expire atomic in one script; NULL and missing tier rows are cached as the default so there is no repeated-miss storm for tenants without rows; the tier cache has no other users of `CACHES["default"]` (grep); the window-boundary test is bucket-aligned so a fixed window fails it; 400-tenant zero-query test is inside the cap, so it is a valid predicate for what it claims.

## Four absence checks

1. **Rollback**: remove the MIDDLEWARE line at `settings.py:19`; no migration, no schema; tenant keys self-expire within one window (TTL asserted); new env vars all have defaults; `CACHES` block reverts to Django's identical LocMemCache default. Client timeout revert is one hunk. Clean.
2. **Coverage**: every behavioural path has a test (accept, reject, boundary, TTL, warm zero-query, default, NULL, invalidation, public path, two outage tests, 400-tenant, Retry-After). Uncovered: the client timeout kwargs (finding 1), the beyond-cap eviction cliff (finding 2), and the mis-ordered-chain `AttributeError` at `rate_limit.py:23` (documented intent, no test; not a perf concern).
3. **One owner**: "public" is derived from `tenant_id is None`, not a second copy of `PUBLIC_PATHS`; unlimited set from settings only; key namespaces `ratelimit:tenant:` and `ratelimit:global:` are distinct keys, not copies. `CACHE_TTL_SECONDS` is a separate literal from the env-configured window (finding 2, minor).
4. **Bad day**: empty tenant: public, not counted (tested); duplicate/concurrent: atomic Lua; Redis down: 503 (tested); Redis stalled: 503 at 0.5 s (measured, not tested; finding 1); DB down on a cache miss: unhandled 500 (same class as the pre-existing auth query); `rate_limit=0`: always 429 (plausible "blocked" semantics, untested); huge limit: finding 4.

## Verdict

**SIGN-WITH-CHANGE**: one named change: add the test from finding 1 so the Redis timeout configuration (the PR's stated outage bound) is asserted against the real client rather than hidden by the conftest double. The code as written measures correct on every axis I probed (one Redis round trip, zero tier queries warm, zero I/O for unlimited, 0.5 s stall bound with no retry amplification, O(limit) memory, correct TTL); findings 2 to 6 are notes for the PR description and wave 2, not conditions.
