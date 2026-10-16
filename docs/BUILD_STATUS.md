# Build status — 2026-10-15

## Application-behavior qualification baseline and deployment boundary

The latest checkpoint that changed application behavior is exact SHA `8180be5fcfcc28e01c4da04ca210fbb93cb84832` (release-gate run `38697891867`): **307 PostgreSQL-backed tests** and **68 Playwright tests** passed, the dependency/security gate passed, production and staging Compose contracts passed, and the qualified production image was built and published as immutable tag `ghcr.io/dubcodes/on-track-rostering:sha-8180be5fcfcc28e01c4da04ca210fbb93cb84832` with digest `sha256:5989096bfadb2604d4cc311541d9a1ccac63f867abd33d604d8e2ebac548fc73`. The sole Alembic head is `e8b5d1f2a740`.

The immediately subsequent documentation-only SHA `f653f82d3c3e42ae199f8777b0a82b4b27f5a9a5` also passed exact-head release-gate run `38785163018` with **307 PostgreSQL-backed tests** and **68 Playwright tests**. Its immutable image `ghcr.io/dubcodes/on-track-rostering:sha-f653f82d3c3e42ae199f8777b0a82b4b27f5a9a5` was published at digest `sha256:3998fc94439cc239e1523378868107d218cacff87d8ab641e17d0ef9cf00d59e`. This documentation qualification does not change the application-behavior baseline, and neither recorded SHA is asserted to remain `origin/main`.

An operational deployment candidate is identified by all three of the following, resolved when the candidate is deliberately selected:

- the exact selected commit SHA;
- a successful exact-head GitHub release gate for that SHA; and
- the immutable `ghcr.io/dubcodes/on-track-rostering:sha-<commit>` image published by that gate.

CI qualification is neither a staging deployment nor a production deployment. No deployment is implied by this checkpoint.

Local development and qualification remain Docker-free. PostgreSQL integration, Playwright, Compose validation, and production image construction are authoritative only when executed by the exact-head GitHub release gate unless a native or explicitly configured remote development PostgreSQL instance and local browser environment are available.

## Implemented

- Authoritative Workdays with private optimistic-versioned drafts, immutable publications, Preview → Publish, stable assignment slots, redacted human history, Open applications, direct immutable decline, cancellation and abandonment lifecycles, and safe deletion of never-published drafts.
- Explicit Workday rescheduling as an immutable replacement chain: the source becomes ABANDONED, a linked replacement is created from the source's current published roster, and the replacement receives a fresh immutable publication. Generated travel is rebuilt; external-event identity, applications, personal responses, Operation identity, generated-travel identity, and programme/source provenance are not inherited.
- Per-replacement employee and Contractor responses stored by Workday and Person. Assigned people can accept or decline; no response remains Pending, and a decline signals Manager action without automatically removing the assignment.
- Unified Builder with Day identity, native time controls, Notes before collapsed Travel, live Position/Person/Vehicle search, standard Travel/hotel planning, assignment details, and HTML validation recovery.
- A Person may hold multiple Positions on one Workday, including through Open Position selection. The Builder presents Move, Keep both, or Cancel immediately; Position timing and notes remain independent while one authoritative person-level travel plan is shared across the Person's rows.
- Person/day Hours, reminder timing, personal timing, and generated Travel aggregate multiple Position rows once using earliest effective start and latest effective finish.
- Persisted system-wide fortnight anchor, personal and Team Hours fortnight totals, and a compact 14-day per-Person matrix with name, Crew-group and Region filters, Track colour, leave state, no weekend-specific treatment, and mobile duration-label hiding. Detailed roster rows remain available.
- Regional editable Position presets for Thoroughbred, Harness, and Trials. Presets contain active Base Positions only; they never carry people, travel, or notes. Removing an override restores the built-in fallback.
- Persistent Base Position display order with deterministic legacy backfill/fallback and configured operational picker order.
- Region-owned reusable Vehicle management for Admin and authorized Managers, including rental-style records, lifecycle, audit, immutable assignment name snapshots, and Builder selection of active borrowed Vehicles without changing ownership.
- Admin-global and Manager-region-scoped Audit workspace with Region/date/actor/action/text filtering and redacted structured detail. Sub-Manager and read-only roles have no audit access.
- Inclusive whole-date Person leave/unavailability with scoped Manager/Admin administration, preserved cancellation history, existing-roster conflict links, advisory Builder/Open-application warnings, deliberate roster override, Preview warnings, and management-only notes.
- Passkeys, optional TOTP, single-display SHA-256 recovery codes for TOTP, trusted-device/fresh-auth protections, and bounded policy-scoped primary credential reset: Admin globally, or scoped Manager for an ordinary controllable Employee/Contractor/Sub-manager account. Primary reset revokes sessions and challenges while preserving authenticators, recovery codes, roles, Person linkage, and pending grants; active Manager/Admin security remains Admin-controlled.
- Consolidated Master Data, Crew, Accounts, Hours, Audit, Settings, external calendar/import/source management, configurable branding, provider-neutral racing evidence, Operations, TravelLegs, accommodation, notification delivery, and personal offline read models.
- The management/identity consolidation makes Settings personal, `/manage` the operational hub, and Crew a compact Person-centric directory/detail with continuous save, one searchable capability Position picker, scoped account/security controls, and bounded batch onboarding metadata. Accounts remains an advanced recovery surface.
- Personal Month shows own authoritative work through the Primary Region's published roster horizon, plus applicable Open Positions; source-calendar potential work is shown only after that Region horizon. Crew view remains the authorized regional all-crew published calendar.
- Published assigned work records raw `WORKED` history. Automatically sourced history establishes inferred eligibility only after two distinct current-published scheduled Workdays for the canonical Base Position; aliases combine and multiple slots on one day count once. Explicit Manager/Employee decisions retain precedence, and trusted legacy/imported `WORKED` history remains preserved.
- Durable user error reports store only description, User/Person IDs, build, safe path and timestamps. Operational notices support relational multi-Region targets and an explicit Admin override distinct from default Manager/Sub-manager grant scope.
- Stable racing programme detail parsing and correction, private-draft synchronization/application, trusted persistent Track maps with scoped manual overrides, regional account linking/invitations, and an atomic allowlisted Deputy capture transition importer.
- Love Racing Race/Start and Trial Heat/Time programme detail, bounded refresh and last-good evidence; same-day Race/Trial presentation association keeps source records independent and leaves manual rostering authoritative.
- One authoritative Race Day timing rule: first-race quarter-hour floor, Region setup lead, snapshotted Track travel, stable code-level `last race + 10 minutes` clearance before quarter-hour ceiling, Pack-up and Return travel. New Race Days also support a 15-minute Person-effective truck start without moving normal Workday Start.
- Operational Track/Region/accommodation addresses with immutable publication snapshots, Track-specific map identity/discovery, and explicit Cambridge Harness/Synthetic separation.
- Paste-based operational leave Preview/atomic Apply, exact-range idempotency, Team Hours leave state, Track-colour fourteen-day presentation, and contained mobile behavior without weekend-specific treatment.
- Nationwide imported-People onboarding through Person UUID worksheets, metadata-only Preview/Apply, fresh-auth batch setup links, absolute trusted-origin fragment URLs, and client-side one-time CSV creation without automatic email.
- Shared searchable-picker behavior that opens the complete valid list on focus and restores the authoritative selected label/ID on cancellation, plus built-in and optional Admin PNG favicon/manifest branding.
- Builder Timing now keeps the truck control aligned inside Timing, with new Race Days defaulting it on. Help includes roster legends, Android/iOS notification setup, and Report error guidance. Normal pages no longer expose build/version footer diagnostics; Administration owns build/version/SHA information. Current theme/readability cleanup is included in responsive qualification.
- The same application image supplies a PostgreSQL-advisory-locked scheduler for racing discovery/programme/map refresh, Contractor expiry, and notification generation/delivery. Durable scheduler heartbeat plus live, ready, and scheduler health endpoints provide an operations foundation; readiness remains database-only.
- Current roster-operations refinement supports edited-row-only Person onboarding with nullable email/Primary Region, strict positive Dice eligibility, date/Track-later unsaved Builder setup, distinct-Person Vehicle capacity, explicit truck-driver qualification/designation, quarterly Track-map refresh with retained-cache permission repair, and exact-item return after Track/source work.
- `CASUAL` is a normal linked working account classification for roster participation, capability preferences, Open Positions and notifications, but is deliberately excluded from Dice. Sub-Managers may assist existing ordinary in-scope accounts after fresh authentication; account creation, deletion, privileged role changes and Manager/Admin account control remain unavailable.

## Qualification history

The Leave checkpoint at exact SHA `1cff0b50c0cb4044214dde646c0956e8e6df5029` was **QUALIFIED** by release-gate run `36652200053`: **208 PostgreSQL-backed tests**, **31 Playwright tests**, production/staging Compose validation, and production image build passed. It established flexible multiple Positions, one person-level travel plan, Vehicle master data, Position ordering/history, Audit, native Builder inputs, and the complete advisory Leave workflow.

The documentation/timing checkpoint at `2cd94d1649c84dd3f090c04c0f4558be2b2fc658` was **QUALIFIED** by release-gate run `36917382750`. Race timing is category-scoped: Trials and non-racing Workdays retain their manual timing behavior.

These are historical checkpoints, not the current qualification baseline.

## Outstanding product and operational backlog

- **Next immediate milestone:** deliberate clean isolated-staging reset → migrate from a blank database → bootstrap → clean master/transition import → inspect resulting canonical data → execute the staging qualification matrix. Follow `STAGING_RESET_REIMPORT.md`; it requires separate explicit destructive authorization and is not performed by this checkpoint.
- Physical-device and operator staging qualification, including real passkey ceremonies, TOTP, and Web Push with deployment-owned VAPID configuration.
- Complete role/direct-request/privacy/offline/restart/log qualification matrix.
- Monitoring and alerting above the durable scheduler-heartbeat and health-endpoint foundation.
- Backup and restore rehearsal for PostgreSQL and the application-data volume.
- Immutable production image promotion, production secrets and WebAuthn RP identity freeze/rotation, and operational governance.
- More advanced historical/predictive Position suggestions beyond the implemented two-published-Workday eligibility rule and simple regional presets.
- Exceptional Crew-Group-scoped Manager write authority remains unresolved. Existing Region authority stays authoritative: no group grant currently implies mixed-Workday, publication, Person, or account control outside an administered Region.
- Configurable Open Position auto-accept; current applications remain Manager-approved.
- IV configuration / IV setup domain; the original operational design expected it, but no current implementation exists.

## Known architecture constraint

Each Workday owns one shared `current_draft_revision_id`; independent per-Manager branches are not represented. Row locking plus exact optimistic versions prevent silent last-writer overwrites. Stale drafts detached by authoritative decline remain preserved for recovery.

## Release rule

Every source checkpoint requires a successful exact-head GitHub release gate. Deployment is separate and explicit: no green CI run proves a staging redeploy or production promotion.
