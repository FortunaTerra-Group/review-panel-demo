# Security lens, run 1

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 1 on `feat/per-tenant-rate-limit`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Probes confirmed six behaviours (the one teardown error is an artifact of my own monkeypatch, not the code under review; that probe's assertion passed). Findings follow.

**Scope reviewed:** `git diff main...HEAD` (2 commits, 6 files). Tests on branch: 8 passed. Probes run with the repo's fake-Redis conftest.

### F1, BLOCK: unauthenticated callers share one rate-limit bucket on the public path, and can starve the health check with zero credentials
- `metrics/middleware/rate_limit.py:18-25` reads `request.tenant_id`, which `metrics/middleware/auth.py:15-17` sets to `None` and leaves `None` for `PUBLIC_PATHS`. The limiter then queries `TenantTier` for `tenant_id=None`, falls to `DEFAULT_RATE_LIMIT` (100), and counts every anonymous request under key `ratelimit:None:<window>`.
- Probe: 101 GETs to `/healthz`, 101st returns 429; Redis holds `b'ratelimit:None:29820994'`.
- Why it matters: any Internet caller can 429 `/healthz` for everyone with about 2 req/s, no key needed. Worse, sending a junk `X-Api-Key` header skips `GlobalRateLimitMiddleware` (`global_limit.py:14`) while auth still treats `/healthz` as public, so the diff's new limiter is the *only* thing counting those requests and it is trivially exhausted. Most load balancers treat non-2xx health responses as unhealthy, instances get pulled, outage. Cross-tenant blast radius, caused by conflating "no tenant" with "a tenant named None".
- Tell in the diff: `tests/test_auth.py:16` added `@pytest.mark.django_db` to `test_healthz_needs_no_key`. That mark was only needed because the public path now hits the DB; the diff silenced the signal instead of reading it.
- Refutation attempted: checked whether the global limiter bounds this first: it allows 10 000 lifetime (never-expiring, pre-existing) requests, so the 100/min None-bucket is the tighter bound; and with the header trick it is bypassed entirely. Finding stands.
- Fix: `if request.tenant_id is None: return self.get_response(request)` before any lookup (public paths are already covered by the global limiter and auth's 401). Restore `test_healthz_needs_no_key` to run without `django_db`; that is the regression test. Add an assertion that no `ratelimit:None:*` key is ever written.

*(Editor's note, not part of the lens output: the global-limiter behaviours this finding describes as pre-existing were defects in the demo's own baseline and were fixed on `main` after this run. The finding about this branch stands unchanged.)*

### F2, BLOCK: unlimited-tier tenants are throttled (contract DO-NOT #3)
- `acme_metrics/settings.py:43-45` defines `UNLIMITED_TIER_TENANTS`; nothing in the diff reads it (`grep UNLIMITED metrics/`: no hits). `tenant-enterprise-01` has no `TenantTier` row (conftest) so `get_tenant_limit` (`rate_limit.py:30-34`) returns the default 100.
- Probe: 101 requests with `key-ent-01`, 429.
- Reader Rule: the exempt list has a writer (settings) and zero readers; the contract's "check that list before enforcing" is decorative today. (CONTRACT.md:14 also names `acme/config.py`, which does not exist; the list lives in settings; flag the stale pointer.)
- Fix: check `tenant in settings.UNLIMITED_TIER_TENANTS` *before* the DB/cache lookup and short-circuit; add the contract's Evidence #2 test (enterprise fires `DEFAULT_RATE_LIMIT + 1` requests, all 200).

### F3, BLOCK: a `TenantTier` row with `rate_limit=NULL` turns every request from that tenant into a 500
- `metrics/models.py:16-17` documents NULL as a legitimate state ("row exists but no limit was ever configured"); `tests/conftest.py:25` seeds exactly that tenant. `rate_limit.py:25` evaluates `count > None`, `TypeError`.
- Probe: `client.get(URL, HTTP_X_API_KEY=key-legacy)` raises `TypeError` (500 in prod).
- Why it matters: a whole customer class is denied service, not throttled. Once wave-2's admin endpoint can write `rate_limit`, one NULL/bad write takes a tenant down remotely.
- Fix: in `get_tenant_limit`, treat `None` as "unconfigured" (`DEFAULT_RATE_LIMIT`, or unlimited, per product decision; state it); add the legacy-tenant test the fixture already anticipates.

### F4, SIGN-WITH-CHANGE: limiter cost is not bounded by the limit (synchronous DB read on the hot path; contract DO-NOT #4)
- `rate_limit.py:19,30-34` does `TenantTier.objects.get` on every request, including ones that will be 429'd.
- Probe: after warm-up, one request = 3 queries (api_keys, tenant_tiers, view); 5 throttled requests = 10 queries. A flooding tenant still costs the DB two round-trips per rejected request, so the limiter does not protect the datastore it was meant to shield; it doubles the per-request DB load relative to `main`.
- Fix: cache the limit (Redis `ratelimit:limit:<tenant>` with TTL is consistent with "no new datastore"), and add the contract's Evidence #3 test (`CaptureQueriesContext`: 0 queries from the limiter after warm-up). Design the cache key so wave-2 can invalidate it (see F7).

### F5, SIGN-WITH-CHANGE: fixed window, contract specifies sliding
- `metrics/ratelimit/windows.py:1-7` is a fixed bucket. Probe: at limit=3, six requests accepted in a 2 s span across a boundary (2x burst). Not a bypass of the total budget, but it permits the exact burst shape the goal statement targets. Contract Evidence #1 (boundary test) is absent. Either implement sliding (sorted-set or two-bucket weighted) or amend the contract explicitly.

### F6, SIGN-WITH-CHANGE (decision required): new hard Redis dependency for all authenticated traffic, failing closed
- Before this diff, keyed requests never touched Redis (`global_limit.py:14` skips them). Now `rate_limit.py:22` raises on Redis unavailability. Probe: `ConnectionError` propagates, 500 for every authenticated request. A cache outage becomes a full API outage. Pick and document fail-open (log + pass) or fail-closed; add a test for whichever is chosen.

### Notes (no change required by this lens)
- `redis.incr` then `redis.expire` (`rate_limit.py:22-24`) is non-atomic; a crash between them leaves a TTL-less key. Because the window id is in the key, this is a Redis memory leak, not a lockout. A pipeline/Lua or `SET NX EX` + `INCR` removes it.
- Key injection: `tenant_id` comes from the `ApiKey` row (operator-controlled), not from the caller; `:` inside a tenant_id cannot collide with another tenant's key because the integer window is always the final segment. Not exploitable.
- No `Retry-After` on the 429; minor.
- Pre-existing, out of scope (DO-NOT #1): `ratelimit:global` never expires; junk `X-Api-Key` skips the global limiter and drives an unauthenticated DB lookup per request in `auth.py:21`. Not introduced by this diff; the F1 fix preserves rather than worsens it.

### F7, Wave-2 admin endpoint: gaps this diff should anticipate
1. `ApiKey` has no privilege/scope field; under the current middleware chain any tenant key would authenticate to an admin endpoint. Wave-2 must add an authz concept; the diff should not be structured so that "is authenticated" is the only gate a route sees.
2. Precedence: the unlimited list must dominate any DB value (F2 fix ordering) so an admin write cannot throttle an exempt tenant.
3. Validation: the admin write path must reject NULL/negative; otherwise F3 becomes remotely triggerable.
4. Invalidation: the F4 cache needs a delete hook the admin endpoint calls; otherwise "adjust without a deploy" degrades to "after TTL".
5. The admin endpoint must not sit behind the limiter it configures (an admin at 429 cannot raise their own limit); derive the exemption from one owner (auth's `PUBLIC_PATHS` / `tenant_id is None`, or a scope on the key), not a second hand-maintained path set.

## Four absence checks
1. **Rollback:** remove `acme_metrics/settings.py:19`; no migration; counter keys self-expire in 60 s. Clean. No runtime kill-switch flag; given F1-F3, a `RATE_LIMIT_ENABLED` setting would be cheap insurance, not required.
2. **Coverage:** covered: under-limit, over-limit, missing tier row. Uncovered: public/None path (test was weakened, not written), unlimited tenant, NULL limit, window boundary, Redis outage, zero-query hot path. Every uncovered path is one the contract or the fixture names explicitly.
3. **One owner:** `UNLIMITED_TIER_TENANTS` has one owner (settings) and no reader (F2); CONTRACT.md points at a non-existent `acme/config.py`. Limits: DB row, then settings default, single chain. Window seconds: one setting. No duplicated lists introduced.
4. **Bad day:** tenant_id `None`: shared bucket (F1); `rate_limit` NULL: 500 (F3); Redis down: 500 (F6); middleware reordered: `AttributeError` 500 (docstring only guards it; `getattr(request, "tenant_id", None)` would be safer); concurrent requests: `INCR` is atomic, fine; contract-named cases (exempt tenant, legacy record): both fail, both untested.

## Verdict: BLOCK

**Blockers:** F1 (anonymous callers can 429 `/healthz` for everyone via the shared `None` bucket, bypassing the global limiter with a junk header), F2 (unlimited-tier tenants throttled at the default limit; contract DO-NOT #3), F3 (NULL `rate_limit` gives 500 for the whole tenant). **Required alongside:** F4 cached lookup + zero-query test, F5 sliding window or explicit contract amendment, F6 documented Redis-failure policy with test, and restoring `test_healthz_needs_no_key` to run without `django_db`.
