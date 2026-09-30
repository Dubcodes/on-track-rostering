# Build status — 2026-09-29

## Current source and deployment boundary

This checkpoint is based on exact qualified `main` SHA `d4373aee6273de6f8d09167d3da8567df735ce25` (release-gate run `36612942208`): 204 PostgreSQL-backed tests and 27 Playwright tests passed with the complete release gate. No staging or production deployment is part of this pass; the previously observed private staging deployment must not be inferred to contain newer source.

Local development and qualification remain Docker-free. PostgreSQL integration, Playwright, Compose validation, and production image construction are authoritative only when executed by the exact-head GitHub release gate unless a native/remote development PostgreSQL URL and local browser environment are explicitly available.

## Implemented

- Authoritative Workdays with private optimistic-versioned drafts, immutable publications, Preview → Publish, stable assignment slots, redacted human history, Open applications, direct immutable decline, cancellation lifecycle, and safe deletion of never-published drafts.
- Unified Builder with Day identity, native time controls, Notes before collapsed Travel, live Position/Person/Vehicle search, standard Travel/hotel planning, assignment details, and HTML validation recovery.
- A Person may hold multiple Positions on one Workday, including through Open Position selection. The Builder presents Move, Keep both, or Cancel immediately; Position timing/notes remain independent while one authoritative person-level travel plan is shared across the Person's rows.
- Person/day Hours, reminder timing, personal timing, and generated Travel aggregate multiple Position rows once using earliest effective start and latest effective finish.
- Persistent Base Position display order with deterministic legacy backfill/fallback and configured operational picker order.
- Region-owned reusable Vehicle management for Admin and authorized Managers, including rental-style records, lifecycle, audit, and immutable assignment name snapshots.
- Admin-global and Manager-region-scoped Audit workspace with Region/date/actor/action/text filtering and redacted structured detail. Sub-Manager and read-only roles have no audit access.
- Inclusive whole-date Person leave/unavailability with scoped Manager/Admin administration, preserved cancellation history, existing-roster conflict links, advisory Builder/Open-application warnings, deliberate roster override, Preview warnings, and management-only notes.
- Consolidated Master Data, Crew, Accounts, Hours, Audit, Settings, external calendar/import/source management, configurable branding, provider-neutral racing evidence, Operations, TravelLegs, accommodation, notification delivery, and personal offline read models.

## Qualification baseline

The Leave checkpoint at exact SHA `1cff0b50c0cb4044214dde646c0956e8e6df5029` is **QUALIFIED** by release-gate run `36652200053`: **208 PostgreSQL-backed tests**, **31 Playwright tests**, production/staging Compose validation, and production image build passed. This qualified checkpoint includes flexible multiple Positions, Move/Keep both/Cancel, one person-level travel plan, one Person/day Hours aggregation, Open Position multi-Position consistency, Vehicle master data, Position ordering/history, Audit, native Builder time inputs, Builder HTML error recovery, and the complete advisory Leave workflow.

## Outstanding product backlog

- Contractor Manager invitation workflow and temporary-account expiry based on latest future assignment, with inactivity extension.
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
