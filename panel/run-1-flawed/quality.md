# Quality lens, run 1

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 1 on `feat/per-tenant-rate-limit`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Working tree is clean (probe files removed; no edits to the branch).

Baseline: `python -m pytest -q` gives **8 passed**. Every finding below was confirmed by a probe run against the branch (Django test client, fake Redis from `conftest.py`), not inferred from reading.

## Findings

### F1: Unlimited-tier tenants are throttled. Contract "Do not" violated, no test. **BLOCKER**
- `metrics/middleware/rate_limit.py:18-26`: the middleware never consults `settings.UNLIMITED_TIER_TENANTS` (`acme_metrics/settings.py:43`). `tenant-enterprise-01` has no tier row, so it falls to `DEFAULT_RATE_LIMIT=100` and gets 429 on request 101.
- Probe: 101 requests as `key-ent-01`, last codes `[200, 200, 429]`.
- Why it matters: the Goal says "must not change behavior for existing unlimited-tier customers"; Evidence item 2 requires a never-throttled test. Neither is present. `UNLIMITED_TIER_TENANTS` is a declared list with **zero readers** (grep: the only hit is its definition), decorative config.
- Refutation attempt: checked whether `get_tenant_limit` or `windows.py` short-circuits on the list, or whether enterprise tenants carry a sentinel tier row. Neither; the `tenants` fixture deliberately seeds enterprise with no row and the code path is the default-limit path.
- Missing test: enterprise tenant fires `DEFAULT_RATE_LIMIT + 1` requests, all 200.

### F2: `rate_limit IS NULL` crashes the request with 500. **BLOCKER**
- `rate_limit.py:25` `if count > limit:` with `limit=None`, `TypeError`. `models.py:15-17` explicitly documents NULL as a legal state ("tier row exists but no limit was ever configured"), and the fixture seeds `tenant-legacy` that way.
- Probe: one GET as `key-legacy`, `TypeError: '>' not supported between instances of 'int' and 'NoneType'`, HTTP 500.
- Missing test: legacy tenant request returns 200 (with a decision recorded on whether NULL means default or unlimited; currently it means "outage for that tenant").
- Refutation attempt: `get_tenant_limit` catches only `DoesNotExist`; the NULL path returns `None` unmodified. Confirmed.

### F3: `/healthz` (public path) is now rate-limited under a shared `None` tenant and hits the DB. **BLOCKER**, and the test diff hides it
- `auth.py:15-17` sets `request.tenant_id = None` and calls `get_response` for public paths; `rate_limit.py:18-21` then runs `get_tenant_limit(None)` (SQL: `WHERE tenant_id IS NULL`) and counts under key `ratelimit:None:<window>`. All unauthenticated health-check traffic shares one 100/60s bucket.
- Probe: 101 GETs to `/healthz`, `[200, 200, 429]`; Redis holds `b'ratelimit:None:29820993'`; one `tenant_tiers` query per health check.
- `tests/test_auth.py:16` adds `@pytest.mark.django_db` to `test_healthz_needs_no_key`. I ran the unmarked `main` version of that test against this branch: it fails with `RuntimeError: Database access not allowed`. The mark was added to make a regression pass, not to test anything new. The test asserts status 200 on one call (a proxy); its named predicate, healthz needs no key, actually now implies "no tenant, no DB, no counter", none of which are asserted. This also violates "Do not change the existing global unauthenticated-endpoint limiter" in spirit: unauthenticated traffic now has a second, tighter limiter.
- Missing tests: `/healthz` with `CaptureQueriesContext`: 0 queries; `/healthz` x N > `DEFAULT_RATE_LIMIT`: no 429.

### F4: Fixed window, not sliding; boundary burst admits 2x the limit. Contract Wave 1(a) + Evidence item 1 unmet. **SIGN-WITH-CHANGE at minimum; BLOCK against the contract as written**
- `metrics/ratelimit/windows.py:1-7` is a fixed-window bucket. Contract specifies a **sliding-window** counter and requires a test asserting the accepted rate "over any span of `WINDOW_SECONDS`".
- Probe (limit 3, window 60): 3 requests at `t = W-1`, 3 at `t = W+1`: **6 accepted in a 2-second span**.
- No test touches `current_window`/`window_key` at all, and no test in `test_rate_limit.py` manipulates time.
- Refutation attempt: re-read the contract for any allowance of fixed windows. There is none; "sliding-window" is in the Decomposition, and the Evidence item is phrased specifically to catch fixed-window boundary doubling.

### F5: One synchronous DB query per request on the hot path. Contract "Do not" + Evidence item 3 unmet. **SIGN-WITH-CHANGE**
- `rate_limit.py:19, 30-34`: `TenantTier.objects.get(...)` every request, uncached. Comment `# DB read, see below` acknowledges it.
- Probe (retail, after warm-up): hot path issues 3 queries, one of them `SELECT ... FROM tenant_tiers WHERE tenant_id = 'tenant-retail'`. The auth query is pre-existing and contractually allowed; the tier query is new.
- Missing test: `assertNumQueries` / `CaptureQueriesContext` showing zero `tenant_tiers` queries after warm-up.

### F6: Existing tests assert proxies, not predicates
- `test_rate_limit.py:20-23` `test_tenant_without_tier_row_uses_default_limit` asserts a single 200. Any limit >= 1 passes it; it does not establish `DEFAULT_RATE_LIMIT` is applied. (My probe of 101 requests confirms 100 pass/1 fails; the behavior is correct, the test just doesn't prove it.)
- `test_under_limit_passes` / `test_over_limit_is_429` are sound for what they name; they are the only real coverage in the diff.

### F7: Notes (non-blocking)
- `rate_limit.py:22-24` INCR then EXPIRE is two round-trips, non-atomic. INCR itself is atomic so concurrent counting is correct; a crash between the two calls leaves a key with no TTL. Because the key embeds the window id, the consequence is a leaked key, not a stuck tenant, so this is hygiene (use `SET NX EX`+`INCR` in a pipeline, or `INCR`+`EXPIRE NX`), not a correctness bug. Verified TTL is set on the first hit (`ttl=60`).
- `rate_limit=0` gives every request 429. Probably intended; worth a one-line test.

## Four absence checks
1. **Rollback:** remove the `MIDDLEWARE` line at `settings.py:19`; Redis keys self-expire (modulo F7 leak). No migration. Adequate.
2. **Coverage:** new paths without a test: unlimited-tier branch (doesn't exist), NULL `rate_limit`, `tenant_id=None`/public path, window boundary, `windows.py` functions, zero-query hot path. Only the under/over-limit happy path is covered.
3. **One owner:** `UNLIMITED_TIER_TENANTS` has a single definition but **no consumer** (Reader Rule: decorative). The contract says the list lives in `acme/config.py`, which does not exist in the repo; `settings.py` is the de facto owner. Flag the doc drift.
4. **The bad day:** empty tenant (`None`, public path): F3. Malformed/NULL config: F2. Arrives twice at once: INCR is atomic, safe. Exempt tenant: F1. Legacy record: F2. Window straddle: F4. The contract calls out the exempt tenant and the legacy record explicitly; neither has a test.

## Contract Evidence scorecard
| Evidence item | Test present? |
|---|---|
| Accepted rate over any `WINDOW_SECONDS` span | No (and the implementation fails it: 6 in 2s at limit 3) |
| Unlimited-tier tenant never throttled | No (and it is throttled at 101) |
| Zero DB queries on hot path after warm-up | No (and there is one per request) |

## Verdict

**BLOCK.**

Named blockers: **F1** (unlimited-tier tenants throttled, direct violation of a "Do not" and of the Goal), **F2** (NULL `rate_limit` gives 500 for a legal, fixture-seeded state), **F3** (public `/healthz` now rate-limited and DB-dependent, with the regression masked by adding `@pytest.mark.django_db` to an existing test). F4 and F5 are contract failures that would each be SIGN-WITH-CHANGE on their own; they are not the reason for the block but must also be resolved, with the three Evidence tests written, before this branch can be re-panelled. The green 8/8 run is a proxy: the suite never exercises the enterprise, legacy, or `None` tenant, never crosses a window boundary, and never counts queries.
