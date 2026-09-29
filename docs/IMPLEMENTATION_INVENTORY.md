# Implementation inventory

“Implemented” means code and deterministic tests exist. Deployment-dependent behavior remains separately qualified.

| Area | Delivered behavior | Primary implementation | Evidence / boundary |
|---|---|---|---|
| Identity/security | Separate Users and People, scoped roles, invitations/signup review, trusted devices, fresh auth, passkeys and optional TOTP | `app/identity`, `app/accounts`, `app/auth` | Security/route tests; real authenticator and recovery operations remain staging work |
| Roster authority | Stable Workday, one shared private draft, exact optimistic locking, immutable publication, Preview/diff/history, Open applications, decline and cancellation | `app/rostering`, `app/open_positions` | Route/domain tests plus PostgreSQL concurrency suite |
| Builder | Unified identity/timing/notes/travel/assignments form; native time controls; collapsed secondary sections; live Position, Person and Vehicle search; human HTML validation errors | Builder template/JS and rostering routes/service | Focused route and Playwright behavior/geometry coverage |
| Multi-Position Person | Same Person may hold multiple stable slots, including via Open Position selection; immediate Move/Keep both/Cancel dialog; timing/notes remain per Position while person-level travel is synchronized | `app/static/builder.js`, `app/rostering/service.py`, `app/open_positions` | Save/publish/domain and responsive browser coverage; inconsistent travel POSTs are rejected |
| Person/day participation | Multiple Position spans combine once as earliest effective start/latest effective finish; personal timing applies once; generated Travel has one row per Person | `app/rostering/participation.py`, `app/rostering/travel.py`, `app/hours` | Multi-role, personal timing, Travel and fortnight tests; never sums roles as separate shifts |
| Position catalog | Base Position capabilities, persistent numeric display order, deterministic legacy backfill/fallback | `app/catalog`, `app/positions/ordering.py`, migration `e48c7ad291f0` | Catalog/Builder order and migration qualification |
| Position history | Stable-slot Position rename/change becomes one label-to-label human change without UUIDs | `app/rostering/diff.py` | Structured diff regression |
| Vehicles | Reusable regional company/rental records, Admin/all and Manager/scoped mutation, archive lifecycle, local-first Builder picker, immutable name snapshots | `app/catalog`, Builder read/template | Regional authority, audit, picker and snapshot tests |
| Audit | Redacted write service plus Admin-global/Manager-region-scoped filtered read workspace | `app/audit` | Authorization, regional visibility and redaction tests |
| Leave/unavailability | Inclusive whole-date advisory ranges, overlap rejection, cancellation history, scoped Manager/Admin page, roster conflict links, Builder/Preview/Open-application warning and deliberate override | `app/unavailability`, Builder read/template/JS | Service/route/privacy and responsive conflict-dialog coverage; no payroll, balances or employee requests |
| Crew/management | Consolidated Crew, Accounts, Master Data, primary Region editing, live search, capabilities, draft deletion | `app/crew`, `app/accounts`, `app/catalog` | Route and responsive browser tests |
| Operations/travel | Operations, explicit TravelLegs, standard previous-day Travel generation, accommodation and personal Making own way/timing | rostering models/services/read models | Domain, route, publication, hours and browser tests |
| External racing | Provider-neutral observations/canonical events, field provenance, Track mapping, import preview/apply, source adoption and display preferences | `app/external_calendar` | Adapter/reconciliation/import/browser tests; source availability never blocks manual rostering |
| Hours | Personal and Team fortnight totals from current publication, combined Person/day participation, overnight and holidays, no break inference | `app/hours`, participation service | Fixed multi-role/overnight/personal/date tests |
| Offline/notifications | User-namespaced read-only personal cache; transactional notification outbox, preferences, retries and reminders | service worker, employee JSON, `app/notifications` | Contract/delivery tests; real Web Push remains environment qualification |
| Branding/settings | Persisted global product branding and operational signup policy | branding/system settings modules | Authorization, persistence, escaping and responsive tests |
| Schema/deployment | Append-only PostgreSQL/Alembic chain; retained production/staging Compose; migration-before-app startup | `migrations/versions`, deployment artifacts | GitHub PostgreSQL/repeated-upgrade/Compose/image gate; no local Docker |
| Responsive UI | Employee and management paths at 1280/430/375/320 with behavior, console and overflow assertions | templates/static CSS/JS, `tests/browser` | Previous exact-head result: 27 Playwright passed; this checkpoint requires its own exact-head run |

## PostgreSQL-only assertions

CI covers simultaneous first-draft creation, stale details/assignments/editor-after-Publish, exact lock-version conflicts, concurrent Publish/outbox, constraints, decline immutability, notification claims, and the full migration chain. SQLite unit tests are never reported as PostgreSQL qualification.

## Current operational validation boundary

The last qualified baseline is `d4373aee6273de6f8d09167d3da8567df735ce25`, release-gate `36612942208`, with 204 PostgreSQL-backed tests and 27 Playwright tests. The exact result for this checkpoint is recorded after its final push. No deployment is implied. Remaining staging/recovery/product work is listed in `docs/BUILD_STATUS.md`.
