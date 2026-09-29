# Build status — 2026-09-29

## Current source and deployment boundary

This checkpoint is based on exact qualified `main` SHA `031a9b144da564ce7c2ac1a9688f59c6df7e658c` (release-gate run `36503512199`). The final SHA and exact-head workflow for the Builder flexibility/operational-management checkpoint are recorded in the completion report after GitHub qualification. No staging or production deployment is part of this pass; the previously observed private staging deployment must not be inferred to contain newer source.

Local development and qualification remain Docker-free. PostgreSQL integration, Playwright, Compose validation, and production image construction are authoritative only when executed by the exact-head GitHub release gate unless a native/remote development PostgreSQL URL and local browser environment are explicitly available.

## Implemented

- Authoritative Workdays with private optimistic-versioned drafts, immutable publications, Preview → Publish, stable assignment slots, redacted human history, Open applications, direct immutable decline, cancellation lifecycle, and safe deletion of never-published drafts.
- Unified Builder with Day identity, native time controls, Notes before collapsed Travel, live Position/Person/Vehicle search, standard Travel/hotel planning, assignment details, and HTML validation recovery.
- A Person may hold multiple Positions on one Workday. The Builder presents Move, Keep both, or Cancel immediately; ordinary same-Workday duplication is no longer a server error.
- Person/day Hours, reminder timing, personal timing, and generated Travel aggregate multiple Position rows once using earliest effective start and latest effective finish.
- Persistent Base Position display order with deterministic legacy backfill/fallback and configured operational picker order.
- Region-owned reusable Vehicle management for Admin and authorized Managers, including rental-style records, lifecycle, audit, and immutable assignment name snapshots.
- Admin-global and Manager-region-scoped Audit workspace with Region/date/actor/action/text filtering and redacted structured detail. Sub-Manager and read-only roles have no audit access.
- Consolidated Master Data, Crew, Accounts, Hours, Audit, Settings, external calendar/import/source management, configurable branding, provider-neutral racing evidence, Operations, TravelLegs, accommodation, notification delivery, and personal offline read models.

## Qualification baseline

The preceding exact-head gate at `031a9b1` passed PostgreSQL migration/repeated-upgrade qualification, **202 PostgreSQL-backed tests**, **27 Playwright tests** at 1280/430/375/320, compile, Ruff, Alembic PostgreSQL SQL generation, dependency integrity/audit, JavaScript syntax, production and staging Compose validation, and the production image build. This checkpoint must receive its own exact-head result before those claims transfer.

## Outstanding product backlog

- Leave / Person unavailability with a Manager override warning integrated into the shared conflict dialog.
- Contractor Manager invitation workflow and temporary-account expiry based on latest future assignment, with inactivity extension.
- Automatic Race Day timing derivation: first-race floor-to-quarter, setup lead, on-track, Vehicle travel call time, last-race ceil-to-quarter, pack-up, and return travel.
- Configurable fortnight anchor in application settings and Team Hours visual distribution bars.
- Editable simple roster Position presets; later optional historical suggestions without a complex prediction rules matrix.
- Full abandoned/rescheduled Workday workflow and employee availability response after moved or abandoned days.
- Remaining responsive/layout polish and full physical-device/operator staging qualification.
- Real Web Push delivery with deployment-owned VAPID/scheduler configuration; real passkey/TOTP ceremonies; complete role/direct-request/privacy/offline/restart/log matrix.
- Recovery codes, monitored production scheduler, immutable release promotion, production secrets/RP identity, and backup/restore rehearsal.

## Known architecture constraint

Each Workday owns one shared `current_draft_revision_id`; independent per-Manager branches are not represented. Row locking plus exact optimistic versions prevent silent last-writer overwrites. Stale drafts detached by authoritative decline remain preserved for recovery.

## Release rule

Every source checkpoint requires a successful exact-head GitHub release gate. Deployment is separate and explicit: no green CI run proves a staging redeploy or production promotion.
