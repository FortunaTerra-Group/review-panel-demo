# Synthesis, run 1 (`feat/per-tenant-rate-limit`)

> Written by the orchestrating session (Claude Opus 5, 2026-09-12) from the four lens reports in this directory, following step 4 of the skill: deduplicate, cluster by root cause, rank, fold.

## Verdicts

| Lens | Verdict | Named blocker |
|---|---|---|
| Architecture | BLOCK | unlimited list unread; hot-path query; NULL limit crash |
| Security | BLOCK | anonymous callers can 429 `/healthz` via the `None` bucket; unlimited tier throttled; NULL limit 500 |
| Quality | BLOCK | unlimited tier throttled; NULL limit 500; `/healthz` limited and DB-dependent, masked by a test mark |
| Performance | BLOCK | live tier query on every request including rejected ones; unlimited tier throttled; NULL limit 500 |

## Root causes, clustered

| # | Root cause | Found by | Seeded? |
|---|---|---|---|
| 1 | `TenantTier` queried live on the hot path (`rate_limit.py:19,32`); contract DO-NOT #4 | 4 of 4 | yes |
| 2 | `settings.UNLIMITED_TIER_TENANTS` has no reader; enterprise tenants throttled at 101 | 4 of 4 | yes |
| 3 | Fixed window (`windows.py`) where the contract specified sliding; 2x burst across a boundary | 4 of 4 | yes |
| 4 | Public paths counted under `ratelimit:None:<window>`; `/healthz` gains a DB dependency; the `django_db` mark on `test_healthz_needs_no_key` hides it | 4 of 4 | yes (the mark was left in deliberately) |
| 5 | `rate_limit` NULL raises `TypeError` at `count > limit` | 4 of 4 | no |
| 6 | `INCR` then `EXPIRE` as two commands; a lost `EXPIRE` leaks a key (not a lockout, since the key is per window) | 4 of 4 | yes |
| 7 | `test_tenant_without_tier_row_uses_default_limit` asserts a proxy (one 200) | 3 of 4 | no |
| 8 | Clock read directly from `time.time()`, so the boundary test cannot be written | 2 of 4 | no |
| 9 | No `Retry-After`; Redis-failure policy undecided; counter logic not in the `ratelimit` package | 1 to 2 of 4 | no |

Every lens's stated blocker maps to root causes 1, 2, 4, or 5. The three seeded root causes (1, 2, 3) were found by all four lenses independently, which is the convergence the skill's step 4 calls the strongest signal.

## Fold (what `feat/per-tenant-rate-limit-folded` contains)

- `metrics/ratelimit/tiers.py`: cached limit lookup (Django cache, 60 s TTL) with `invalidate(tenant_id)` for the wave-2 admin path; NULL falls back to `DEFAULT_RATE_LIMIT`. (1, 5)
- Unlimited-tier check before any I/O. (2)
- `metrics/ratelimit/windows.py`: sliding window as one Lua script (trim, count, admit, TTL). (3, 6)
- `metrics/ratelimit/clock.py`: injectable `now()`. (8)
- Middleware skips requests with no tenant, deriving the public-path decision from auth's output; `django_db` mark removed from the healthz test so it guards the regression again. (4)
- Fail closed with 503 on `redis.RedisError`; `Retry-After` on 503 and 429. (9)
- Tests: the contract's three evidence items (boundary, exempt tenant, zero tier queries after warm-up) plus default-limit, NULL row, public path with no DB and no key, outage, invalidation, `Retry-After`. 18 pass. (7 and all of the above)

Not folded: moving the middleware's remaining wiring into the package (Architecture finding 7) beyond what the three new modules already do; a runtime kill switch; a scope on `ApiKey` for wave 2. Recorded as follow-ups in the contract's next wave.

See `../run-2-folded/` for the panel's verdict on the result.
