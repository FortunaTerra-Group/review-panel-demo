# Quality lens, run 2

> **Claude Opus 5 (1M context) output, 2026-09-12.** Verbatim lens report from run 2 on `feat/per-tenant-rate-limit-folded` at commit `7a6e499`; paths made repository-relative, em dashes replaced with plain punctuation. See `../README.md`.

Suite run: `18 passed in 0.66s` (fakeredis 2.38 + lupa 2.8, redis-py 8.1, Django 5.2.17). Note on scope: `git diff main...HEAD` touches 7 files; `tests/test_auth.py`, `tests/test_global_limit.py`, `tests/conftest.py` are pre-existing on `main` and unchanged; I read them as dependencies only.

All probes below were run on a copy of the tree in my scratchpad; nothing in the branch was edited.

### F1, BLOCKER: the window-boundary test does not assert the sliding-window predicate (Evidence item 1 unmet)

`tests/test_rate_limit.py:53-63` `test_accepted_rate_over_any_window_span`. The comment at line 56 says `advance(w-1)` lands on "the last second of what a fixed window would call bucket N". It does not. `FakeClock` starts at `1_700_000_000.0` (line 14), and `1_700_000_000 mod 60 = 20`, so the burst fires at second 39 of a fixed bucket and the follow-up at second 41 of the *same* bucket. A fixed-window counter rejects that too.

Probe: replaced `metrics/ratelimit/windows.py` with a fixed-window `INCR key:{int(now//window)}` counter. Result: **11 passed**: every test in the file, including this one, is green against the exact defect the contract's sliding-window requirement exists to exclude. The test is a proxy (it re-checks "4th request in a burst is 429", which `test_over_limit_is_429` already covers), not the predicate ("accepted rate over *any* span of `WINDOW_SECONDS`").

The implementation itself is correct; this is a test-only fix. Verified replacement (red on the fixed-window mutant with `assert 200 == 429`, green on the real script):

```python
w = settings.RATE_LIMIT_WINDOW_SECONDS
fixed_clock.t = float((int(fixed_clock.t) // w) * w)   # start ON a fixed-window bucket edge
fixed_clock.advance(w - 1)                             # last second of bucket N
for _ in range(3):
    assert get(client, tenants["retail"]).status_code == 200
fixed_clock.advance(2)                                 # bucket N+1: a fixed window resets here
assert get(client, tenants["retail"]).status_code == 429
fixed_clock.advance(w - 3)                             # burst + w - 1: still inside the trailing window
assert get(client, tenants["retail"]).status_code == 429
fixed_clock.advance(1)                                 # burst + w: aged out ((now-w, now]; trim is inclusive)
assert get(client, tenants["retail"]).status_code == 200
```

Refutation attempt: I checked whether any *other* test in the file distinguishes sliding from fixed. None does; the mutant passed all 11. Kept.

### F2, SIGN-WITH-CHANGE: the per-tenant key's TTL is untested; the "no state left behind" claim in `windows.py:1-3` has no guard

Probe: deleted `redis.call('EXPIRE', key, window)` from `metrics/ratelimit/windows.py:9`. Result: **12 passed** (incl. my tightened boundary test). An inactive tenant's sorted set would then persist forever with up to `limit` members. The repo's own convention already tests this for the global key (`tests/test_global_limit.py:9`), so parity is one assertion after a request: `assert 0 < fake_redis.ttl(b"ratelimit:tenant-retail") <= settings.RATE_LIMIT_WINDOW_SECONDS`.

Refutation attempt: memory is bounded per active tenant by the trim on the next request; the leak is only for tenants that go quiet. Real but low-severity; kept as a required change because the code comment asserts the property and the rollback story (F-abs-1 below) depends on it.

### F3, note: the outage test mocks `allow`; a real-shaped failure works too

`tests/test_rate_limit.py:108-118` monkeypatches `rate_limit.allow` to raise. It proves the `except redis.RedisError` clause, and the probe (delete the try/except at `rate_limit.py:29-32`) confirms it bites. I also ran a real-shaped version, `fake_redis.connection_pool.connection_kwargs["server"].connected = False`, and it returns 503 with `Retry-After: 1` through the real `get_redis()` to `eval` path. Recommend swapping to that form (no mock, exercises the actual exception type fakeredis/redis-py raise), but the predicate holds either way, so not required.

### Evidence-item accounting

| Contract Evidence | Test | Asserts the named predicate? | Bites? (probe) |
|---|---|---|---|
| Accepted rate over any span of `WINDOW_SECONDS` | `test_accepted_rate_over_any_window_span` | **No**, see F1 | **No**, fixed-window mutant passes |
| Unlimited-tier tenant never throttled | `test_unlimited_tier_is_never_throttled` (line 48) | Yes: 105 requests > default 100 for a tenant with no tier row | Yes: dropping the `in settings.UNLIMITED_TIER_TENANTS` check at `rate_limit.py:25` fails it |
| Zero DB queries on hot path after warm-up | `test_hot_path_issues_no_db_query_for_the_limit_after_warm_up` (line 67) | Yes, correctly narrowed to `tenant_tiers`; the auth `api_keys` lookup and the view's own query are pre-existing and outside the DO-NOT ("must not *add* a round-trip") | Yes: removing the `cache.get` at `tiers.py:15-17` fails it |

### Edge cases asked for

- **Window boundary**: F1. Semantics are `(now-w, now]` (ZREMRANGEBYSCORE upper bound inclusive), verified directly under fakeredis with float scores; the fix in F1 pins that.
- **NULL `rate_limit`**: `tiers.py:20` folds NULL and no-row into the default; `test_null_rate_limit_row_falls_back_to_default` and `test_tenant_without_tier_row_gets_exactly_the_default_limit` both go red when the fallback is removed (probe). `rate_limit=0` is untested and means "reject everything"; plausible as a blocked-tenant semantic, not in contract; note only.
- **Unlimited tier**: checked before any I/O (`rate_limit.py:25`); satisfies the DO-NOT. Exemption membership comes from the env default in `settings.py:45-47`; a CI env that sets `UNLIMITED_TIER_TENANTS` would silently change what the test proves. Pinning via the pytest-django `settings` fixture is cheap; optional.
- **Missing tenant / public path**: `test_public_path_is_not_counted_and_touches_no_db` (line 97) bites on the status loop, not only on the key-pattern assertion; I probed counting `None` tenants with a constant limit *and* renaming the key prefix; it still failed on request 101. My "absence reads as clean" suspicion about `keys("ratelimit:None*") == []` is refuted.
- **Redis outage**: F3. Fail-closed is a stated policy in the docstring and tested. There is no runtime toggle to fail-open; that is the architecture lens's call, not a Quality defect.
- **Cache invalidation**: `test_admin_invalidation_takes_effect_without_waiting_for_ttl` bites (no-op `invalidate()` fails it). But `invalidate()` has **no production caller** (grep: only the definition at `tiers.py:24`), decorative until wave 2(c). And `CACHES` is `LocMemCache` (`settings.py:37`), so in a multi-worker deploy `invalidate()` clears only the process that handled the admin call; the test proves the single-process predicate only. Wave 2(c)'s "without a deploy" is still met by the 60s TTL, so not blocking, but the docstring at `tiers.py:25` should say "process-local; other workers converge within `CACHE_TTL_SECONDS`", or the admin endpoint should not rely on it.
- **Concurrent requests**: atomicity comes from single-script execution in Redis; a fakeredis single-thread test can't exercise a race meaningfully. Deferred with that reason. `uuid4().hex` members rule out ZADD collisions.
- **Lua faithfulness**: fakeredis runs the script under real Lua (lupa; confirmed `lupa` is in `fakeredis.commands_mixins.scripting_mixin`). Float scores, ZREMRANGEBYSCORE, ZCARD, EXPIRE and the return code behave as on real Redis in my direct probe. Real-Redis-only differences (Lua number to string formatting at about 14 significant digits) truncate below the millisecond and are irrelevant here. Off-by-one probe (`<=` in place of `<` at `windows.py:7`) fails 6 tests; the count logic is well guarded.

### Four absence checks

1. **Rollback**: forward steps are two settings lines (`MIDDLEWARE` entry `settings.py:19`, `CACHES` `settings.py:37`). Undo = revert both; no migration, no schema, no data. Leftover `ratelimit:<tenant>` keys self-expire, *if* EXPIRE is present, which is exactly what F2 leaves unguarded. No kill switch short of a redeploy; noted, not required by the contract.
2. **Coverage**: every branch in `rate_limit.py`, `tiers.py`, `windows.py` has a test that goes red when the branch is reverted (probes 2 to 8), except the EXPIRE line (F2). `clock.now()`'s real body runs via `test_auth.py`/`test_ingest.py`, which don't patch it.
3. **One owner**: `UNLIMITED_TIER_TENANTS`: settings only. Tier cache key: `tiers._cache_key` only. Redis key format: `windows.allow` only (the test's literal `"ratelimit:None*"` is a second copy, but the test does not depend on it to bite). `PUBLIC_PATHS`: `auth.py` only; the limiter correctly derives "public" from `tenant_id is None` rather than re-listing paths.
4. **Bad day**: empty tenant: public path, tested; duplicate/concurrent: atomic script + uuid member; Redis down: tested; NULL/missing row: tested; exempt tenant: tested; window boundary: **test exists but does not bite** (F1); `rate_limit=0`/negative: untested, non-contractual.

## Verdict

**BLOCK**, named blocker: `tests/test_rate_limit.py::test_accepted_rate_over_any_window_span` passes against a fixed-window counter (probe-confirmed), so contract Evidence item 1 ("accepted rate over any span of `WINDOW_SECONDS`") is not demonstrated. Fix is test-only: bucket-align the fake clock and assert 429 at `burst + w - 1` and 200 at `burst + w` (verified red/green above). Fold F2 (tenant-key TTL assertion) in the same change. F3 and the `invalidate()` process-locality docstring are recommended, not required.
