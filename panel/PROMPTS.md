# Lens briefs used for the logged runs

These are the briefs each lens agent received, verbatim except for paths. The skill's `SKILL.md`
supplied the procedure, the return contract, and the four absence checks; the brief supplied the
lens, the scope, and the files. Substitute your own checkout path.

Common preamble (all four lenses, both runs):

> You are the <LENS> lens of a pre-PR review panel, run exactly as the public skill describes.
> Read the procedure first: `<skill checkout>/skills/multi-persona-review-panel/SKILL.md`
> (step 3: return contract + four absence checks). Do NOT read that repo's examples/ directory.
>
> Under review: the Django branch checked out at `<demo checkout>`. Scope:
> `git diff main...HEAD`. Read every touched file and what it depends on. Read CONTRACT.md.
> You may run the tests (`python -m pytest -q`) and write probe scripts using Django's test client
> (`DJANGO_SETTINGS_MODULE=acme_metrics.settings`; `tests/conftest.py` shows how the fake Redis
> is wired).
>
> Run the four absence checks. For each candidate BLOCK or SIGN-WITH-CHANGE, try to refute it
> against the actual code before keeping it and say what you re-checked. If the change is sound,
> SIGN and say why, naming what you verified. Return: concrete findings (issue, file:line, why it
> matters, proposed fix), then ONE verdict: SIGN / SIGN-WITH-CHANGE (named changes) / BLOCK
> (named blocker). Rigorous and independent; no rubber-stamping, no invented problems. Do not
> edit any files.

Lens-specific paragraph:

**Architecture.** The Architecture lens checks the change against the contract's decomposition
and DO-NOT list, module boundaries, and sources of truth.

**Security.** Your lens: trust boundaries (where tenant_id comes from; what happens on public
paths and when it is missing), injection surfaces, denial-of-service shapes (can one tenant affect
another, can a customer class be throttled that must not be, can a caller bypass the limit), and
whether the planned wave-2 admin endpoint introduces a gap this diff should anticipate. A clean
SIGN with reasons is legitimate if the change is sound.

**Quality.** Your lens: acceptance criteria versus what the tests actually establish; edge cases
(window boundary, tenant with no tier row, unlimited-tier tenant, NULL rate_limit, concurrent
requests, crash between two Redis calls, public paths); whether each contract Evidence item has a
test; whether any existing test asserts a proxy rather than its named predicate. Be precise about
Redis semantics (INCR is atomic).

**Performance.** Your lens: per-request cost on the hot path versus main (database queries and
Redis round trips per authenticated request; count real queries with Django's
CaptureQueriesContext in a probe), what happens to rejected requests, resource growth over time
(keys without TTL, unbounded structures), behavior under a burst at a window boundary, and what
the test double hides. Be exact about Redis semantics (INCR atomic; EXPIRE separate; a key without
TTL persists).

For run 2 (the folded branch) the Quality brief added: do the tests bite (would each go red if the
behaviour it guards were reverted), and is the fake Redis's Lua execution faithful enough for the
sliding-window script; the Performance brief added: the cost of the sorted set per tenant, cache
TTL versus admin invalidation, and the Redis-outage path.
