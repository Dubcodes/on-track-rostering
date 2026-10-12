# Build status — 2026-10-13

## Current source and deployment boundary

The current qualified `main` is exact SHA `6930da44c4b98ff12f8c14593787d44a0bfe80a4` (release-gate run `38235415281`): **271 PostgreSQL-backed tests** and **56 Playwright tests** passed, the dependency/security gate passed, production and staging Compose contracts passed, and the qualified production image was built and published as immutable tag `ghcr.io/dubcodes/on-track-rostering:sha-6930da44c4b98ff12f8c14593787d44a0bfe80a4` with digest `sha256:594bf3f9b6d084783acdee9ff61171206e1ec25f6b41c348a7c02826105ee5ff`. Staging references that same digest.

CI qualification is neither a staging deployment nor a production deployment. No deployment is implied by this checkpoint.

Local development and qualification remain Docker-free. PostgreSQL integration, Playwright, Compose validation, and production image construction are authoritative only when executed by the exact-head GitHub release gate unless a native or explicitly configured remote development PostgreSQL instance and local browser environment are available.

## Implemented

- Authoritative Workdays with private optimistic-versioned drafts, immutable publications, Preview → Publish, stable assignment slots, redacted human history, Open applications, direct immutable decline, cancellation and abandonment lifecycles, and safe deletion of never-published drafts.
- Explicit Workday rescheduling as an immutable replacement chain: the source becomes ABANDONED, a linked replacement is created from the source's current published roster, and the replacement receives a fresh immutable publication. Generated travel is rebuilt; external-event identity, applications, personal responses, Operation identity, generated-travel identity, and programme/source provenance are not inherited.
- Per-replacement employee and Contractor responses stored by Workday and Person. Assigned people can accept or decline; no response remains Pending, and a decline signals Manager action without automatically removing the assignment.
- Unified Builder with Day identity, native time controls, Notes before collapsed Travel, live Position/Person/Vehicle search, standard Travel/hotel planning, assignment details, and HTML validation recovery.
- A Person may hold multiple Positions on one Workday, including through Open Position selection. The Builder presents Move, Keep both, or Cancel immediately; Position timing and notes remain independent while one authoritative person-level travel plan is shared across the Person's rows.
- Person/day Hours, reminder timing, personal timing, and generated Travel aggregate multiple Position rows once using earliest effective start and latest effective finish.
- Persisted system-wide fortnight anchor, personal and Team Hours fortnight totals, and a 14-day per-Person workload distribution with defined intensity bands. Detailed roster rows remain available.
- Regional editable Position presets for Thoroughbred, Harness, and Trials. Presets contain active Base Positions only; they never carry people, travel, or notes. Removing an override restores the built-in fallback.
- Persistent Base Position display order with deterministic legacy backfill/fallback and configured operational picker order.
- Region-owned reusable Vehicle management for Admin and authorized Managers, including rental-style records, lifecycle, audit, and immutable assignment name snapshots.
- Admin-global and Manager-region-scoped Audit workspace with Region/date/actor/action/text filtering and redacted structured detail. Sub-Manager and read-only roles have no audit access.
- Inclusive whole-date Person leave/unavailability with scoped Manager/Admin administration, preserved cancellation history, existing-roster conflict links, advisory Builder/Open-application warnings, deliberate roster override, Preview warnings, and management-only notes.
- Passkeys, optional TOTP, single-display SHA-256 recovery codes for TOTP, trusted-device/fresh-auth protections, and bounded administrator-assisted primary credential reset. Primary reset revokes sessions and challenges while preserving authenticators, recovery codes, roles, Person linkage, and pending grants.
- Consolidated Master Data, Crew, Accounts, Hours, Audit, Settings, external calendar/import/source management, configurable branding, provider-neutral racing evidence, Operations, TravelLegs, accommodation, notification delivery, and personal offline read models.
- Stable racing programme detail parsing and correction, private-draft synchronization/application, trusted persistent Track maps with scoped manual overrides, regional account linking/invitations, and an atomic allowlisted Deputy capture transition importer.
- The same application image supplies a PostgreSQL-advisory-locked scheduler for racing discovery/programme/map refresh, Contractor expiry, and notification generation/delivery. Durable scheduler heartbeat plus live, ready, and scheduler health endpoints provide an operations foundation; readiness remains database-only.

## Qualification history

The Leave checkpoint at exact SHA `1cff0b50c0cb4044214dde646c0956e8e6df5029` was **QUALIFIED** by release-gate run `36652200053`: **208 PostgreSQL-backed tests**, **31 Playwright tests**, production/staging Compose validation, and production image build passed. It established flexible multiple Positions, one person-level travel plan, Vehicle master data, Position ordering/history, Audit, native Builder inputs, and the complete advisory Leave workflow.

The documentation/timing checkpoint at `2cd94d1649c84dd3f090c04c0f4558be2b2fc658` was **QUALIFIED** by release-gate run `36917382750`. Race timing is category-scoped: Trials and non-racing Workdays retain their manual timing behavior.

These are historical checkpoints, not the current qualification baseline.

## Outstanding product and operational backlog

- Physical-device and operator staging qualification, including real passkey ceremonies, TOTP, and Web Push with deployment-owned VAPID configuration.
- Complete role/direct-request/privacy/offline/restart/log qualification matrix.
- Monitoring and alerting above the durable scheduler-heartbeat and health-endpoint foundation.
- Backup and restore rehearsal for PostgreSQL and the application-data volume.
- Immutable production image promotion, production secrets and WebAuthn RP identity freeze/rotation, and operational governance.
- Optional historical Position suggestions beyond the implemented simple regional presets.
- Configurable Open Position auto-accept; current applications remain Manager-approved.

## Known architecture constraint

Each Workday owns one shared `current_draft_revision_id`; independent per-Manager branches are not represented. Row locking plus exact optimistic versions prevent silent last-writer overwrites. Stale drafts detached by authoritative decline remain preserved for recovery.

## Release rule

Every source checkpoint requires a successful exact-head GitHub release gate. Deployment is separate and explicit: no green CI run proves a staging redeploy or production promotion.
