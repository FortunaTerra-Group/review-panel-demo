# Quality lens, run 3

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 3 on `feat/per-tenant-rate-limit-folded` at commit `84efc3a`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Baseline: `21 passed` on the branch. All mutation work was done in a scratch copy under my session scratchpad; `git status` on the branch is clean.

## Evidence items to predicate tests (each mutated in scratch; all bite)

| Contract Evidence | Test | Mutation | Result |
|---|---|---|---|
| Accepted rate over any span of `WINDOW_SECONDS` across a boundary | `tests/test_rate_limit.py:52-68` `test_accepted_rate_over_any_window_span` | Replaced Lua sliding window with a fixed-bucket INCR counter | RED (`:63`, 200 where 429 expected). Also RED when trim bound made exclusive (`(now-window`); the test pins the edge at exactly `burst + w`, `:67` |
| Unlimited-tier tenant never throttled | `:46-49` | Dropped `tenant in settings.UNLIMITED_TIER_TENANTS` at `metrics/middleware/rate_limit.py:25` | RED |
| Zero DB queries on the hot path after warm-up | `:77-83` and `:140-148` (400 tenants) | Removed the `cache.get` short-circuit in `metrics/ratelimit/tiers.py:15-17` | both RED. Also set `TIER_CACHE_MAX_ENTRIES=300` (Django's implicit default): many-tenant test RED, single-tenant test stays green, so the 400-tenant test is the one earning the `CACHES` block in `settings.py:41-46` |
| (fold) tenant key TTL | `:71-74` | Deleted `EXPIRE` at `windows.py:9` | RED (`ttl == -1`) |

Additional bites confirmed: NULL/no-row default fallback (`tiers.py:20` to `limit = row`) RED at `:89,:95`; ZADD member = timestamp instead of request id: 5 RED; `ZCARD <= limit`: 5 RED; fail-open on RedisError: both outage tests RED; `invalidate()` no-op: RED `:107`; middleware ordered before auth: loud `AttributeError` at `rate_limit.py:24` as the comment promises.

**fakeredis Lua fidelity:** ran `windows.SLIDING_WINDOW` against a throwaway `redis-server --port 6390` and against `FakeStrictRedis` with the exact test sequence (bucket-edge alignment, `T+w-1`, `T+w`, `T+w+1e-6`, microsecond-resolution `time.time()` scores, `limit=None`). Identical admit/deny vectors, identical stored scores, identical TTL (60), identical integer return, identical `DataError` for `None`. Faithful for this script.

## Findings

**F1: `metrics/redis_client.py:11-16` (timeouts) has zero test coverage; the "stall" bad-day is unproven.**
Deleting both `socket_connect_timeout`/`socket_timeout` kwargs leaves the suite at `21 passed`. `tests/conftest.py:13` replaces `Redis.from_url` with a lambda that discards kwargs, so no existing test can observe them. The "real client path" outage test (`test_rate_limit.py:130-134`) flips `server.connected=False`, which raises `ConnectionError` immediately; it exercises the down case, not the stall case that the timeouts exist for. Refutation attempted: is the stall behaviour load-bearing for the contract? Yes: the fail-closed 503 policy (`rate_limit.py:30-31`) only holds if a stall *becomes* a `RedisError` in bounded time; without the timeout a stalled Redis holds the worker and the 503 path never runs. Feasibility checked: a 15-line pytest with an accept-and-never-reply TCP socket, `monkeypatch.undo()` on the conftest fake, and `override_settings(REDIS_URL=...)` passes on the branch in 0.68 s (503) and fails with the timeouts removed (5.3 s, no 503 within bound). Physical, not a mock.

**F2: tier cache is not reset per test outside `test_rate_limit.py`; suite is order-dependent.**
`cache.clear()` lives only in the module-local autouse fixture `tests/test_rate_limit.py:22-27`. The `LocMemCache` is process-global, and `test_admin_invalidation_...` (`:99-107`) leaves `tier-limit:tenant-retail = 1` behind. Running `-k invalidation tests/test_ingest.py` gives `test_ingest_then_summary FAILED: assert 429 == 202`. Refutation: default alphabetical order and fully reversed module order both pass (a later rate-limit test happens to re-clear and re-warm to 3), so this is latent, not currently red; hence a change, not a block. Fix: move `cache.clear()` into the conftest autouse `fake_redis` fixture (or its own autouse) so the tier cache is as per-test as the Redis is.

**Notes (non-blocking):**
- `tests/conftest.py:22` hardcodes `tenant-enterprise-01`, a second copy of the `settings.py:56` env default. If CI sets `UNLIMITED_TIER_TENANTS` differently the exempt test fails for the wrong reason; prefer `override_settings(UNLIMITED_TIER_TENANTS=frozenset({"tenant-enterprise-01"}))` on that test.
- `tests/test_ingest.py:6-10` makes exactly 3 retail requests against `rate_limit=3`; sits on the boundary; any added request in that test 429s. Worth a comment or a higher fixture limit for non-limiter tests.
- The zero-query tests filter to `"tenant_tiers" in sql`; that is the right predicate for the DO-NOT ("the per-request limit check"), and the mutation proves it bites, but a limiter that added a query to a different table would pass. Optional tightening: assert total query count equals the auth+view baseline.
- Shared-client side effect: the new 0.5 s timeouts also apply to `GlobalRateLimitMiddleware` (`global_limit.py:22`). Policy is unchanged (it still 500s unhandled on any RedisError), only the stall duration shrinks; not a DO-NOT violation, but the PR description should say it.
- `rate_limit=0` (or negative) makes every request 429; not specified by the contract. State it or clamp it.
- `env` list `.strip()` change (`settings.py:55-57`) is untested; trivial.

## Four absence checks
1. **Rollback**: remove `settings.py:19`; tenant keys carry a TTL (`windows.py:9`, tested), so no Redis residue; `CACHES` block reverts to Django's implicit default; no migration, no schema change. Named and adequate.
2. **Coverage**: every new path has a biting test except `redis_client.py:11-16` (F1) and the env `.strip()` parse (trivial).
3. **One owner**: `UNLIMITED_TIER_TENANTS` derived once (`settings.py:54`); cache key prefix once (`tiers.py:9`); Redis namespace once in code (`windows.py:17`); the string appears in two tests as the asserted predicate, acceptable. Second hand-copy: conftest tenant id (note above).
4. **Bad day**: empty tenant: public-path test with DB blocked (`:110-114`, real predicate); NULL / no-row: tested; Redis down: tested two ways; Redis stall: **not tested** (F1); many tenants: tested and bites at 300; duplicates/concurrency: deferred with reason: atomicity is Redis's EVAL guarantee and the script was verified byte-for-byte on a real server; fakeredis is single-threaded so an in-process concurrency test would be a proxy.

## Verdict: **SIGN-WITH-CHANGE**

1. Add a stall test through the real client path (accept-and-hang socket, `override_settings(REDIS_URL=...)`, assert 503 within a bound). Demonstrated feasible and red/green-discriminating; minimal fallback is asserting `from_url` receives `socket_timeout == settings.REDIS_TIMEOUT_SECONDS`, but the socket version is the real predicate.
2. Move `cache.clear()` from `tests/test_rate_limit.py:26` into a conftest autouse fixture so the tier cache is reset per test suite-wide (demonstrated order-dependent failure `429 == 202`).

Everything else in the branch is sound: each contract Evidence item has a test that asserts its named predicate, every one of those tests goes red under the behaviour it guards, and fakeredis reproduces real Redis exactly for this script.
