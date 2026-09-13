# Goal contract: add per-tenant rate limiting to the public API

Written before any code. Reviewed by one person who did not implement it.

## Goal
Ship configurable per-tenant rate limiting on the public REST API so that one tenant's traffic
burst cannot degrade response times for other tenants. Must not change behavior for existing
unlimited-tier customers, and must ship without adding a new datastore.

## Context
- A global limiter already protects unauthenticated endpoints and stays in place.
- Auth middleware already resolves `request.tenant_id` before business logic runs.
- Redis is provisioned and used for caching; reuse it for counters.
- Unlimited-tier customers are a fixed, known list (`settings.UNLIMITED_TIER_TENANTS`).

## Decomposition
- **Spine:** a tenant-aware limiter middleware that reads a per-tenant limit and enforces it
  with a Redis-backed counter.
- **Wave 1 (a):** Redis-backed **sliding-window** counter keyed by tenant.
- **Wave 1 (b):** tenant tier to limit lookup from the existing tier table, no schema change.
- **Wave 2 (c):** admin API endpoint to adjust a tenant's limit without a deploy.
- **Wave 2 (d):** read-only dashboard panel showing each tenant's usage against their limit.

## Do not
- Do not change the existing global unauthenticated-endpoint limiter.
- Do not introduce a datastore beyond the provisioned Redis instance.
- Do not apply limits to tenants on the unlimited-tier list; check that list before enforcing.
- Do not let the per-request limit check add a synchronous database round-trip to the request
  hot path. The config lookup must be cached, not queried live.

## Evidence
- A test that fires requests across a window boundary and asserts the accepted rate over any
  span of `WINDOW_SECONDS`.
- A test that a tenant on the unlimited-tier list is never throttled.
- A test that the hot path issues zero database queries after warm-up.
