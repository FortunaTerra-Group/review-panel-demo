# Synthesis, run 2 (`feat/per-tenant-rate-limit-folded` at `7a6e499`)

> Written by the orchestrating session (Claude Opus 5, 2026-09-12) from the four lens reports in this directory.

## Verdicts

| Lens | Verdict |
|---|---|
| Architecture | SIGN-WITH-CHANGE (2 named changes) |
| Security | SIGN-WITH-CHANGE (3 named changes) |
| Quality | BLOCK (boundary test does not bite) |
| Performance | SIGN-WITH-CHANGE (3 named changes) |

This is the run that matters most for anyone learning the skill. The author (the same session that wrote the fold) believed the folded branch was clean and had written "second run: four SIGNs" into a draft before running it. The panel said otherwise, and the sharpest finding was about a test, not the code: the boundary test passed against a fixed-window mutant because the fake clock never crossed a bucket edge. The implementation was right; the proof was not.

## Findings, clustered

| # | Finding | Found by |
|---|---|---|
| 1 | `getattr(request, "tenant_id", None)` turns a middleware mis-order into "everyone unlimited" with no signal | Architecture, Security (both probed the swap) |
| 2 | LocMem cache is per-process, so `invalidate()` and its test over-claim | Architecture, Security, Quality, Performance |
| 3 | LocMem default `MAX_ENTRIES=300`: above ~300 active tenants the tier query is back on the hot path (measured 100% miss at 400) | Performance |
| 4 | No socket timeouts on the Redis client: "fails closed" is true for a refusal, not a stall (measured 5.01 s) | Security, Performance |
| 5 | Boundary test does not discriminate sliding from fixed (mutant passes all 11) | Quality |
| 6 | Tenant key TTL untested (removing EXPIRE passes) | Quality |
| 7 | Outage test mocks `allow` rather than exercising the client path | Security, Quality |
| 8 | `ratelimit:` prefix shared with the global counter; `ratelimit:tenant:` would be disjoint | Architecture, Security, Performance |
| 9 | Env list parsing does not strip whitespace | Security |

## Fold (commit `84efc3a` on the same branch)

- `request.tenant_id` read directly; a mis-ordered chain raises. (1)
- `invalidate()` docstring states process-local, TTL-bounded convergence; test renamed `..._in_this_process_...`. LocMem kept, per Performance's recommendation for the spine. (2)
- `TIER_CACHE_MAX_ENTRIES` from env (default 10,000); new test warms 400 tenants and asserts zero tier queries on the second pass. (3)
- `REDIS_TIMEOUT_SECONDS` from env (default 0.5) on the shared client. (4)
- Boundary test aligned to a bucket edge with the verified red/green sequence; a fixed-window mutant now fails it (checked before committing). (5)
- Tenant-key TTL test. (6)
- Real-client outage test (`server.connected = False`) alongside the mocked one. (7)
- Key namespace `ratelimit:tenant:{id}`. (8)
- `.strip()` in the env list parsing. (9)

21 tests pass. Run 3 (`../run-3-folded/`) is the panel's verdict on this state, which is the branch tip a reader will clone.
