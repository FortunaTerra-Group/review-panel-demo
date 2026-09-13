# Performance lens, run 1

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 1 on `feat/per-tenant-rate-limit`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Suite: `8 passed in 0.09s`. I also ran a probe (in a scratch directory, repo untouched) using `CaptureQueriesContext` plus a spy on `execute_command` of the fakeredis client, with the branch's own `conftest.py`. Numbers below are from that run.

### Measured hot-path cost vs main

| Request | main | this branch |
|---|---|---|
| Authenticated, accepted (`/v1/metrics/summary`) | 2 DB queries (api_keys, view), 0 Redis | **3 DB queries** (api_keys, **tenant_tiers**, view), 1 Redis (`INCR`; +`EXPIRE` on first hit per window) |
| Authenticated, rejected (429) | n/a | **2 DB queries** (api_keys, tenant_tiers) + 1 Redis `INCR`, then 429 |
| `/healthz` | 0 DB queries, 1 Redis (global) | **1 DB query** (`tenant_tiers WHERE tenant_id IS NULL`) + 2 Redis (global `INCR`, `INCR ratelimit:None:<window>`) |

## Findings

**F1, BLOCKER. Synchronous DB round-trip added to every authenticated request, including rejected ones.**
`metrics/middleware/rate_limit.py:19` calls `get_tenant_limit(tenant)` unconditionally; `:32` is `TenantTier.objects.get(...)`. The author's own comment on line 19 says "DB read". `CONTRACT.md:28-29` forbids exactly this ("must be cached, not queried live") and `CONTRACT.md:35` requires a zero-query-after-warm-up test, which does not exist. The steady-state probe shows the `tenant_tiers` SELECT on request 2, 3, and the 429'd request 4. Under a burst the limiter is supposed to shed, the DB takes one extra query per rejected request, i.e. the protection mechanism itself scales load onto the resource it's meant to protect.
Refutation attempt: grepped for any cache layer (`cache`, Django cache framework, per-tenant memo): only `lru_cache` on `get_redis()`, nothing around the tier lookup. Confirmed.
Fix: cache the limit per tenant (Django cache framework with TTL, or a Redis hash `ratelimit:limits` read in the same pipeline as the INCR, one round trip total), with explicit invalidation hooked for Wave 2(c). Add the zero-query test the contract names.

**F2, BLOCKER. Unlimited-tier tenants are throttled at `DEFAULT_RATE_LIMIT`.**
`settings.UNLIMITED_TIER_TENANTS` (`acme_metrics/settings.py:43`) has zero readers; grep hits settings only. `CONTRACT.md:7-8, 27, 34`. Probe: `tenant-enterprise-01` gets 429 on request 101. This is the Reader Rule failure shape: a declared list nobody consumes. (Contract cites `acme/config.py`, which does not exist; settings is the real owner: one owner, just unread.)
Refutation attempt: checked whether the tier table could carry an "unlimited" row that short-circuits: `rate_limit` is an int or NULL, no sentinel, and NULL crashes (F3). Confirmed.
Fix: `if tenant in settings.UNLIMITED_TIER_TENANTS: return self.get_response(request)` before any lookup (also zero DB/Redis cost for those tenants); add the never-throttled test.

**F3, BLOCKER. Legacy tier row with `rate_limit = NULL` returns 500 on every request.**
`metrics/models.py:16-17` documents NULL as a valid state; `tests/conftest.py:25` seeds it (`tenant-legacy`) and no test touches it. `rate_limit.py:25` `count > limit` gives `TypeError: '>' not supported between 'int' and 'NoneType'`, reproduced. This tenant is locked out entirely, not rate limited.
Fix: `limit = row.rate_limit if row.rate_limit is not None else settings.DEFAULT_RATE_LIMIT`; add a test for the legacy row (the contract's named "legacy record" case).

**F4. `/healthz` now depends on the database and shares one 100/min bucket across every unauthenticated caller.**
`auth.py:15-17` sets `tenant_id = None` and passes through to the next middleware, which is now `TenantRateLimitMiddleware`; `rate_limit.py:18-21` runs with `tenant=None`, SELECT `tenant_id IS NULL`, INCR `ratelimit:None:<window>`. Probe confirms 1 query and that key. Consequences: (a) health checks from a fleet's load balancer/orchestrator exceed 100/min trivially (10 instances x probe every 5s = 120/min), 429, instances marked unhealthy; (b) a DB outage turns a liveness endpoint into a 500. The diff's edit to `tests/test_auth.py:16` (adding `@pytest.mark.django_db` to `test_healthz_needs_no_key`) is the receipt: on `main` this test passed with DB access blocked; the mark was added to make the regression pass rather than to expose it.
Fix: `if request.tenant_id is None: return self.get_response(request)` at the top; revert the `django_db` mark so the test guards this again.

**F5. Fixed window, not sliding; 2x limit admitted across a boundary.**
`metrics/ratelimit/windows.py:1-7` is a fixed bucket. `CONTRACT.md:19` specifies sliding-window and `CONTRACT.md:32-33` requires a boundary test; neither is present. Probe: limit 3/60s, 6 requests accepted within a 1.5s span straddling the boundary (keys `...:29820994` and `...:29820995`). Halves the isolation guarantee the goal statement is about.
Fix: weighted two-window approximation (read previous window's count alongside the INCR in one pipelined round trip) or a Lua token bucket; add the boundary test.

**F6. `INCR` then `EXPIRE` is two commands; a lost `EXPIRE` leaves a key with no TTL forever.**
`rate_limit.py:22-24`. `INCR` is atomic; `EXPIRE` is a separate round trip, guarded by `count == 1` only, so a crash/connection error between them is never repaired (later requests see `count > 1` and skip it). Probe: TTL `-1` after `INCR` without `EXPIRE`, still `-1` after the next request. Growth is bounded to one orphaned key per incident but permanent.
Fix: `pipeline(transaction=True)` with `INCR` + `EXPIRE` (atomic and one round trip), or Redis 7's `EXPIRE key W NX` on every request, or `SET key 0 EX W NX` + `INCR` in a pipeline.

**F7. Notes (no verdict weight).** No `Retry-After` on the 429 (`rate_limit.py:26`), so clients will tight-retry into F1's DB cost. Redis unavailable now fails closed for the whole authenticated API (redis-py 8.1 default `socket_timeout=5`, so a hung Redis adds 5s per request before the 500); contract is silent; needs a decision, not a fix from me. Pre-existing and out of scope per the contract's DO-NOT: `global_limit.py:16` INCRs `ratelimit:global` with no TTL, so the global limiter never resets; flagging, not touching.

*(Editor's note, not part of the lens output: the pre-existing global-limiter defect named in F7 was fixed on `main` after this run.)*

### What the test double hides
fakeredis is in-process: no latency, no connection errors, so F6's gap and F7's fail-closed path are unobservable; sqlite in-memory makes F1's extra query nearly free and no test counts queries; `tenant-legacy` is seeded but unused (F3); `test_tenant_without_tier_row_uses_default_limit` fires one request, so it proves 200, not "uses default limit".

### Four absence checks
1. **Rollback:** remove the `MIDDLEWARE` line (`settings.py:19`) and revert the `test_auth.py` mark; no migration; windowed keys expire in 60s, F6 orphans do not. Exists.
2. **Coverage:** under-limit, over-limit, missing-tier-row: tested. Unlimited tenant, NULL limit, window boundary, zero-query hot path, `tenant_id=None`: no test. All four contract Evidence items are missing or not met.
3. **One owner:** window/default limits derive from settings; the unlimited list has one owner and zero readers (F2).
4. **Bad day:** exempt tenant: throttled (F2). Legacy record: 500 (F3). Empty tenant: DB hit + shared bucket (F4). Two-at-once at a boundary: 2x admitted (F5). Redis down: 500 for all authenticated traffic (F7).

## Verdict: BLOCK

Named blocker: **F1**, a live `tenant_tiers` SELECT on every authenticated request, including rejected ones, in direct violation of `CONTRACT.md:28-29`, with the contract's zero-query test absent. F2 and F3 are independently blocking (unlimited tenants throttled; legacy NULL row 500s). F4, F5, F6 are required changes to carry along in the same fix.
