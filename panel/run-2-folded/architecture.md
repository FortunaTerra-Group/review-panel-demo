# Architecture lens, run 2

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 2 on `feat/per-tenant-rate-limit-folded` at commit `7a6e499` (the state before the run-2 fold); paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Verified: read every file in the diff plus models, auth, global_limit, redis_client, conftest, views, urls, migration, CONTRACT.md. Suite: 18 passed. Ran five probes of my own with the Django test client (probe file created in `tests/`, run, deleted; `git status` clean, nothing in the repo was edited).

## Contract check

- **Decomposition**: spine + Wave 1(a) sliding window + Wave 1(b) tier lookup delivered; `tiers.invalidate` is a hook toward 2(c), not 2(c) itself. Correct scope.
- **DO-NOT list**: `global_limit.py` untouched (confirmed in the diff). No new datastore (Redis + in-process cache). Unlimited check at `rate_limit.py:24` runs before any Redis or DB I/O. Tier lookup is cached; the one DB query per tenant per 60 s per process is the "warm-up" the contract's own Evidence wording permits.
- **Evidence**: all three tests exist and discriminate. I traced `test_accepted_rate_over_any_window_span` against the Lua script (burst at t=w-1 survives the fixed-window bucket edge at w+1, ages out at 2w+1) and `test_admin_invalidation...` (without invalidation the limit-3 path would return 200 on the asserted request).
- **Module boundaries**: `metrics/ratelimit/{clock,windows,tiers}` has no HTTP dependency; `windows.py` is pure; middleware composes. Clean layering, small files.

*(Editor's note: the Quality lens in the same run showed by mutation that the boundary test did not in fact discriminate; see quality.md. Two independent lenses, one traced the test and one executed a mutant; the mutant was right.)*

## Findings

**F1: Coordination contract with auth is silent on violation.** `rate_limit.py:24` uses `getattr(request, "tenant_id", None)`. The only condition under which that attribute is absent is `TenantRateLimitMiddleware` running before `ApiKeyAuthMiddleware`, and the default converts that misconfiguration into "every tenant is unlimited." Probe: with the two entries swapped in `MIDDLEWARE`, 10 requests from the limit-3 tenant returned `[200]*10`, no error. The sibling `global_limit.py:18` reads `request.tenant_id` directly and would fail loudly (AttributeError, 500) in the same situation. This is the absence-reads-as-clean shape: the fallback makes the bad day invisible. *Fix*: read `request.tenant_id` directly, matching the existing convention; optionally a test that asserts the swapped order raises rather than admits.

**F2: `tiers.invalidate` promises more than the cache backend delivers.** `settings.py:37` selects `LocMemCache`, which Django documents as per-process. `tiers.py:24-26` docstring says the wave-2 admin endpoint will call `invalidate`, and `test_admin_invalidation_takes_effect_without_waiting_for_ttl` proves it, in one process. Under any multi-worker WSGI deployment the admin request clears one worker's cache; the others serve the old limit until `CACHE_TTL_SECONDS` expires. Reader-Rule check: the artifact (`invalidate`) has a reader only in the process that wrote it. Attempted refutation: the 60 s bound is arguably acceptable for "adjust without a deploy," so this is not a correctness block, but the hook and test name assert a property the architecture does not have, and the wave-2 author will build on that claim. *Fix (either)*: (a) point `CACHES["default"]` at `django.core.cache.backends.redis.RedisCache` on `REDIS_URL` (Redis is provisioned; no new datastore; `redis>=5` already a dep) and move `tiers.limit_for` inside the `try` at `rate_limit.py:29` so a Redis outage on the tier read also yields 503, not 500; note this adds one Redis GET to the hot path, the Performance lens should weigh that; or (b) keep locmem and rewrite the `invalidate` docstring and the test name to state "process-local; cross-process propagation bounded by `CACHE_TTL_SECONDS`." Pick one and write it down.

**Notes (no change required)**
- `windows.py:18` uses `eval`, shipping the script body every request; `register_script` (EVALSHA with fallback) is the idiomatic shape. Performance-lens territory.
- Key namespaces: `ratelimit:{tenant}` (new) and `ratelimit:global:{bucket}` (existing) are hand-maintained prefixes in two files with no shared owner; a tenant_id of the form `global:N` would collide. `ratelimit:tenant:{id}` would make them disjoint.
- `Retry-After` on 429 is the full window; the script has the oldest score and could return the exact wait. Contract doesn't ask for it.
- `CACHE_TTL_SECONDS` is a module constant while every other limiter knob lives in `settings.py` from env. Consistency only.
- `cache.clear()` lives in `test_rate_limit.py`'s autouse fixture, not in `conftest.py` next to `fake_redis`; other modules that warm the tier cache leak state forward. Harmless today (fixture rows are identical every test), latent hazard.
- `test_hot_path_issues_no_db_query...` filters to `tenant_tiers`; the pre-existing `api_keys` query from auth still runs per request. Matches the contract's intent (the limit check must not add a query) but the name over-claims.
- `rate_limit=0` or negative gives permanent 429 (probed). Reasonable "blocked" semantics; wave-2 admin endpoint should validate the range.

## Four absence checks

1. **Rollback**: revert the commit. No migration, no schema change. `ratelimit:{tenant}` keys carry `EXPIRE window` and self-clean within 60 s; locmem cache dies with the process; removing `CACHES` returns to Django's default (also locmem). Removing the `MIDDLEWARE` line restores `main` behavior exactly.
2. **Coverage**: unlimited skip: `test_unlimited_tier...`; None-tenant skip: `test_public_path...` (no `django_db` mark, so a call into `limit_for` would raise; it genuinely proves the early return); 429: `test_over_limit...`; sliding boundary: `test_accepted_rate...`; no-row default: `test_tenant_without_tier_row...`; NULL row: `test_null_rate_limit_row...`; invalidation: tested (in-process, see F2); Redis failure: `test_redis_outage...` (monkeypatches `allow`; my probe confirmed a `ConnectionError` raised from `eval` itself also reaches 503, and `TimeoutError`/`ConnectionError` both subclass `RedisError` in redis 8.1.0); Retry-After: tested. Untested: the misordering failure mode (F1).
3. **One owner**: unlimited list: `settings.UNLIMITED_TIER_TENANTS`, the contract-named owner; per-tenant limit: `tenant_tiers` with `settings.DEFAULT_RATE_LIMIT` fallback, one derivation in `tiers.limit_for`; window: `settings.RATE_LIMIT_WINDOW_SECONDS`, shared by both limiters and the Retry-After header. Key prefixes are the one duplicated-by-hand set (note above).
4. **Bad day**: empty/None tenant: skipped; two requests at the same float timestamp: distinct uuid members, probed `ZCARD == 3` after a 3-burst; concurrent arrivals at the boundary: single Lua script, atomic in Redis; limit 0: 429; empty `UNLIMITED_TIER_TENANTS` env: nobody unlimited; cached value `0`: `is not None` check handles it correctly; Redis down: 503 fail-closed, documented in the middleware docstring; clock skew across app servers: per-server `time.time()` rather than Redis `TIME` means windows can differ by the skew; the injectable clock seam is the reason, acceptable at 60 s granularity.

## Verdict

**SIGN-WITH-CHANGE**
1. `rate_limit.py:24`: replace `getattr(request, "tenant_id", None)` with `request.tenant_id` so a middleware mis-order fails loudly instead of disabling per-tenant limits (F1).
2. Resolve F2 one way: RedisCache backend with `limit_for` moved inside the `try`, or amend `tiers.invalidate`'s docstring and the invalidation test's name to state the per-process, TTL-bounded propagation.

Everything else in the change is sound against the contract, the DO-NOT list, and the existing module boundaries.
