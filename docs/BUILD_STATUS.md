# Build status — 2026-10-03

## Current source and deployment boundary

The current qualified `main` is exact SHA `c221fe75bd86c255c5c3313d0ca999e018b9d78c` (release-gate run `37113924431`): 222 PostgreSQL-backed tests and 35 Playwright tests passed, dependency audit was clean, production/staging Compose validation and the staging build-ID guard passed, and production image construction passed. CI is not a staging redeploy or production deployment; no deployment is implied by this checkpoint.

This qualified source includes racing-programme parsing/correction and provenance, the advisory-locked scheduler, persistent Track maps and manual overrides, regional Account linking/invitations, role-independent personal-fortnight Hours, and the isolated allowlisted Deputy transition importer.

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
- Stable racing programme detail parsing and correction, private-draft synchronization/application, an advisory-locked refresh scheduler, trusted persistent Track maps with scoped manual overrides, regional account linking/invitations, role-independent personal fortnight totals, and an atomic allowlisted Deputy capture transition importer.

## Qualification baseline

The historical Leave checkpoint at exact SHA `1cff0b50c0cb4044214dde646c0956e8e6df5029` was **QUALIFIED** by release-gate run `36652200053`: **208 PostgreSQL-backed tests**, **31 Playwright tests**, production/staging Compose validation, and production image build passed. It established flexible multiple Positions, Move/Keep both/Cancel, one person-level travel plan, one Person/day Hours aggregation, Open Position multi-Position consistency, Vehicle master data, Position ordering/history, Audit, native Builder time inputs, Builder HTML error recovery, and the complete advisory Leave workflow.

The earlier documentation/timing checkpoint at `2cd94d1649c84dd3f090c04c0f4558be2b2fc658` was **QUALIFIED** by release-gate run `36917382750`. Race timing is category-scoped: Trials and non-racing Workdays retain their manual timing behavior.

## Outstanding product backlog

- Configurable fortnight anchor in application settings and Team Hours visual distribution bars.
- Editable simple roster Position presets; later optional historical suggestions without a complex prediction rules matrix.
- Full abandoned/rescheduled Workday workflow and employee availability response after moved or abandoned days.
- Remaining responsive/layout polish and full physical-device/operator staging qualification.
- Real Web Push delivery with deployment-owned VAPID/scheduler configuration; real passkey/TOTP ceremonies; complete role/direct-request/privacy/offline/restart/log matrix.
- Recovery codes, scheduler operations monitoring/alerting, immutable release promotion, production secrets/RP identity, and backup/restore rehearsal for both PostgreSQL and the application-data volume.

## Known architecture constraint

Each Workday owns one shared `current_draft_revision_id`; independent per-Manager branches are not represented. Row locking plus exact optimistic versions prevent silent last-writer overwrites. Stale drafts detached by authoritative decline remain preserved for recovery.

## Release rule

Every source checkpoint requires a successful exact-head GitHub release gate. Deployment is separate and explicit: no green CI run proves a staging redeploy or production promotion.
