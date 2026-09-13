# Synthesis, run 3 (`feat/per-tenant-rate-limit-folded` at `84efc3a`)

> Written by the orchestrating session (Claude Opus 5, 2026-09-12) from the four lens reports in this directory.

## Verdicts

| Lens | Verdict | Required change |
|---|---|---|
| Architecture | SIGN-WITH-CHANGE | timeout kwargs test; tier cache on its own alias, cleared from conftest |
| Security | SIGN-WITH-CHANGE | timeout kwargs test (precedence test recommended) |
| Quality | SIGN-WITH-CHANGE | stall test through the real client path (a hanging socket); cache reset in conftest |
| Performance | SIGN-WITH-CHANGE | timeout kwargs test |

No BLOCK. Four lenses converged on one required change: the Redis timeouts added in the run-2 fold are correct (three lenses measured 0.50 s against a real silent or black-holed socket) but invisible to the suite, because the test fixture swaps the client factory with a lambda that discards keyword arguments. Two lenses independently expected redis-py's default retry policy to multiply the stall, measured it, and dropped the finding. That refute-before-report step is part of the skill's procedure.

## Fold (commit `f9bac6a`, the branch tip)

- `CACHES["tier_limits"]` alias with the env-sized `MAX_ENTRIES`; `tiers.py` binds to it; `default` cache left at Django's default for future users.
- `clear_tier_cache` autouse fixture in `tests/conftest.py`; the module-local `cache.clear()` removed.
- `test_redis_client_carries_the_configured_timeouts`: records the kwargs the factory receives.
- `test_a_stalled_redis_fails_closed_within_the_timeout`: a real accept-and-never-reply socket, the conftest fake undone, the real client, 503 inside four timeouts. Both new tests fail with the timeouts removed (checked before committing: 2 failed, one after a 5 s hang).
- `test_unlimited_list_takes_precedence_over_a_tier_row`.

24 tests pass. **No fourth run was made.** The remaining notes across the three runs (EVALSHA, a miss counter on the tier cache, an upper bound on `rate_limit`, `CACHE_TTL_SECONDS` into settings, a Redis-outage breaker, logging on 429/503, the global limiter's changed stall time to be stated in the PR description, unmetered 401s as a backlog item) are recorded here and in the lens files as wave-2 and backlog items.

## What the three runs show

- Run 1: a green suite, four BLOCKs, three seeded root causes found by all four lenses, two more found that were not seeded.
- Run 2: the author's fold, believed clean; three SIGN-WITH-CHANGE and one BLOCK, on a test that could not fail.
- Run 3: four SIGN-WITH-CHANGE converging on one missing proof.

The verdicts converge; they do not reach four bare SIGNs, and a reader should not expect their own run to either. What a run should reproduce is the shape: independent lenses, probes against the code, refutation before reporting, and findings that name a file and a line.
