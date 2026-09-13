# Architecture lens, run 3

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 3 on `feat/per-tenant-rate-limit-folded` at commit `84efc3a` (after the run-2 fold); paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

**Baseline:** 21/21 tests pass in the shared venv. Every touched file and dependency read; `panel/` not read.

### Contract conformance (checked, not assumed)

- **Decomposition**: Spine (`metrics/middleware/rate_limit.py`), Wave 1(a) sliding window (`metrics/ratelimit/windows.py`), Wave 1(b) tier lookup with no schema change (`metrics/ratelimit/tiers.py`; `metrics/models.py` untouched, no migration). Waves 2(c)/(d) correctly not attempted; `tiers.invalidate()` is the declared seam for 2(c).
- **DO-NOT list**: Global limiter source untouched (see F1 for the shared-client caveat). No new datastore (LocMemCache is process memory). Unlimited list checked at `rate_limit.py:24` before `limit_for` and before any Redis call. Hot path: `tiers.limit_for` reads cache first; missing rows and NULL rows are negative-cached with the default, so no repeat query for tenants without a row.
- **Evidence section**: all three named tests exist: `test_accepted_rate_over_any_window_span` (bucket-edge aligned; a fixed window admits at `+2`, this asserts 429), `test_unlimited_tier_is_never_throttled`, `test_hot_path_issues_no_db_query_for_the_limit_after_warm_up`.
- **Module boundaries**: `windows.py` is a pure Redis algorithm with no Django import and an injected client; `tiers.py` owns ORM+cache; `clock.py` is the only wall-clock seam; the middleware composes them. Clean.

### Findings

**F1: Shared Redis client change alters the untouched global limiter's failure mode; no test observes it.**
`metrics/redis_client.py:9-16`. `get_redis()` is shared by `GlobalRateLimitMiddleware`. Before: `socket_timeout=None` (unbounded hang on a stalled Redis). After: 0.5 s, then `redis.TimeoutError`, which `global_limit.py` does not catch. Probe: `/healthz` under a raising pipeline gives **500** (tenant path under the same error gives **503**, as designed). The letter of "do not change the global limiter" holds; its runtime behaviour did change. Second half: `tests/conftest.py:13` patches `from_url` with a lambda that discards kwargs, so no test in the suite sees `socket_connect_timeout`/`socket_timeout` (probe with a capturing patch confirms the kwargs are correct today: both 0.5). A future kwarg typo would pass green and fail on first Redis call in prod.
Fix: (i) state the global-limiter effect in the PR description (no code change; its unhandled-error path is pre-existing and out of scope); (ii) add one test that patches `from_url` with a capturing callable, calls `get_redis()`, and asserts both timeouts equal `settings.REDIS_TIMEOUT_SECONDS`.

**F2: `TIER_CACHE_MAX_ENTRIES` names a scope the setting does not have (one-owner gap).**
`acme_metrics/settings.py:41-46` sizes `CACHES["default"]`, the process-wide default cache used by `django.core.cache.cache`. `tiers.py:2,15,20,28` and `tests/test_rate_limit.py:26` (`cache.clear()`) all bind to that shared alias. Today there is no other cache user (grep confirms), so no defect. But Wave 2(d), a dashboard in this same contract, is exactly the feature that adds a second user; its entries then share the 10 000-entry cull budget with tier limits, and any `cache.clear()` it issues evicts every tier limit and puts the tier query back on the hot path for one request per tenant. Re-checked: the 400-tenant test does have teeth (`TIER_CACHE_MAX_ENTRIES=300` makes it fail), so the sizing is real; the issue is only where the size is bound.
Fix: give the tier cache its own alias (`CACHES["tier_limits"]`), use `caches["tier_limits"]` in `tiers.py`, and clear that alias in a conftest-level autouse fixture (also resolves N6). Three small edits, no behaviour change.

### Notes (recorded, not required)

- N1 Fail-closed 503 on Redis error (`rate_limit.py:30-31`) is a policy the contract does not state; the contract's goal is inter-tenant isolation, and fail-closed makes a Redis outage an outage for every metered tenant (unlimited tier survives: it is checked first). The docstring records the choice; it needs an explicit owner ack in the PR.
- N2 Time has N owners: each app server writes its own wall clock into the shared ZSET (`windows.py:17`). Clock skew widens/narrows the window by the skew; with NTP and a 60 s window this is negligible, and `redis.call('TIME')` would cost the injectable clock seam. Defensible; record it.
- N3 Tier cache is LocMem, not Redis, though the contract says Redis is "used for caching". Zero extra RTT on the hot path is the right call for the spine; `invalidate()`'s docstring is honest that propagation is per-process/TTL. Record the decision so 2(c) does not promise instant propagation.
- N4 `redis.eval` ships the script body every request; `register_script` gives EVALSHA with NOSCRIPT fallback. Performance lens item; one line to change.
- N5 `rate_limit` of 0 or negative gives every request 429 (probed). Reasonable as "suspended", but unvalidated; Wave 2(c)'s writer must validate >= 0.
- N6 `cache.clear()` lives only in `test_rate_limit.py`'s fixture; `test_auth`/`test_global_limit` inherit cache state. Order-independent today only because `fake_redis` is per-test (re-ran files in reverse order: 19 pass). Folds into F2.
- N7 `Retry-After` on 429 is the full window (upper bound); the oldest ZSET score would give the exact wait. Fine for now.

### Four absence checks

1. **Rollback**: No migration. Undo = remove the one `MIDDLEWARE` line; Redis keys self-expire within the window (TTL asserted by test); cache is in-process; both new env vars have defaults. Named and clean.
2. **Coverage**: Every branch in `rate_limit.py`, `tiers.py`, `windows.py` has a test (public path, unlimited, cache hit/miss, missing row, NULL row, admit/reject, boundary, TTL, invalidate, outage via patch and via real client, Retry-After, >300 tenants). One gap: `redis_client.py` timeouts (F1). Mis-ordered chain (`rate_limit.py:23`) gives 500 by design (probed), untested; deferred as a settings invariant rather than a code path.
3. **One owner**: limit: `tenant_tiers` (cache is a TTL'd replica); unlimited list: `settings.UNLIMITED_TIER_TENANTS` per contract; window: one setting shared with the global limiter; "no tenant means public path": `auth.py:PUBLIC_PATHS` via `tenant_id=None`, same invariant the global limiter already relies on; key namespace literal in `windows.py:18`, pinned by tests. Gap: the cache alias (F2).
4. **Bad day**: empty tenant: pass-through (tested, DB-blocked); NULL limit: default (tested); 0/negative limit: all rejected (probed, N5); duplicate/concurrent at the limit: single atomic Lua script, uuid member so equal timestamps cannot collapse; Redis down: 503 (tested two ways); Redis stalled: `TimeoutError` is a `RedisError`, 503 (probed); env list with whitespace: stripped; exempt tenant and legacy record both tested.

### Verdict

**SIGN-WITH-CHANGE**
1. Add the `get_redis()` timeout-kwargs test and name the global-limiter failure-mode change in the PR description (F1).
2. Bind the tier cache to its own `CACHES` alias and move the cache clear into conftest (F2/N6).

Neither change alters runtime behaviour; the design is sound, matches the contract's decomposition and DO-NOT list, and the three contract evidence tests are present and have teeth.
