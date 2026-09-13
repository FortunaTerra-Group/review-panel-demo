# review-panel-demo

A small, fictional Django API ("Acme Metrics API": tenants push metric points with an API key
and read summaries back) that exists so the
[multi-persona-review-panel](https://github.com/FortunaTerra-Group/multi-persona-review-panel-skill)
skill has a real diff to run against. It is the code behind the skill's worked example and the
walkthrough video.

- `main`: the service before the feature. `ApiKeyAuthMiddleware` resolves `request.tenant_id`
  from the `api_keys` table; a coarse global limiter covers unauthenticated routes; Redis is
  provisioned and used for that counter; `tenant_tiers` already exists with a nullable
  `rate_limit` column.
- `feat/per-tenant-rate-limit`: the spine PR from the goal contract in `CONTRACT.md`. Tests pass.
  Its defects were seeded so the skill has something to find; the list is deliberately not in
  this README, so that running the panel yourself is a fair test. The skill repository's
  worked example has the full transcript, including what the panel found that was not seeded.
- `feat/per-tenant-rate-limit-folded`: the same branch after the panel's findings were folded,
  three times (the panel was rerun after each fold; see `panel/`).

`panel/` holds the full output of every lens from all three runs, the briefs they were given, and
a synthesis per run. Every file there is Claude Opus 5 output from 2026-09-12 and says so.

Run the panel yourself:

```bash
git checkout feat/per-tenant-rate-limit
claude plugin marketplace add FortunaTerra-Group/claude-plugins
claude plugin install multi-persona-review-panel@fortunaterra
claude   # then: /multi-persona-review-panel  (scope: git diff main...HEAD)
```

Run it:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q          # sqlite + fakeredis, no services needed
```

Everything here is invented: no real tenants, keys, or traffic. Apache-2.0. Copyright 2026
FortunaTerra Technologies Inc.
