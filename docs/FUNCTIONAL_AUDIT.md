# Functional audit and traceability

This is the maintained cross-feature audit completed against the qualified `4a460c4…` baseline. The code remains authoritative. Historical proposals are evidence only where they do not conflict with `DOMAIN_RULES.md`, `DECISIONS.md` or `SECURITY.md`.

Status vocabulary: **Implemented and verified**, **Implemented but insufficiently tested**, **Partially implemented**, **Confirmed defect**, **Not implemented**, **Explicitly deferred**, and **Requires product decision**.

## Traceability matrix

| Workflow / function | Authority and data boundary | Implementation and validation | Transaction / concurrency boundary | Test evidence | Status / remaining edge |
|---|---|---|---|---|---|
| Person-first onboarding and account setup | Person and User are separate; Region-scoped account administration is enforced by `app/auth/policy.py` | Crew directory/detail, invitation and exceptional Advanced Accounts routes; normalized email/link guards; fresh authentication for sensitive controls | Account/link/grant services serialize relevant rows and audit mutations | Account, Crew, Contractor, recovery, invitation, browser and PostgreSQL concurrency suites | Implemented and verified; real invitation email/authenticator delivery remains staging evidence |
| Draft → Preview → Publish | Workday points at one private draft and one immutable published revision | Rostering routes/service, stable slot keys, structured diff, human history and notification outbox | Optimistic Workday version plus PostgreSQL operation advisory lock; publish revalidates before atomic pointer change | Foundation, routes, notification, browser and PostgreSQL suites | Implemented and verified |
| Reschedule a published Workday | Source publication is immutable; replacement is a new linked Workday | Destination uses the authoritative interval conflict checker; readable redacted review and explicit conflict acceptance | Source abandonment, replacement, generated Travel, publication, history and notification changes share one transaction; reviewed conflict fingerprint must still match | Route rollback/state-change regression and concurrent PostgreSQL reschedule regression | Implemented and locally verified; exact-head PostgreSQL CI and staging acceptance pending |
| Builder early roster hints | Current publications only; Preview is authoritative | Same-date hint filters SCHEDULED Workdays and ASSIGNED rows and is explicitly labelled informational | Read-only | Picker/domain regressions | Implemented and verified; legacy duplicate warning removed |
| Revision cloning | Revision-owned data is immutable | Assignments, programme evidence, allowance indicators and IV equipment snapshots clone with fresh row identities and stable semantic keys | Clone and Workday draft pointer update are one transaction | Ordinary draft, decline, reschedule and equipment snapshot tests | Implemented and verified. Reschedule deliberately omits programme/source evidence because it belongs to the original meeting/date |
| Effective timing and Hours | Full effective Person/day span; no payroll or break deductions | Central travel/participation helpers cover ordinary, personal, hotel/truck, return and overnight timing | Published revision read only; personal entry write is separate and scoped | Category, overnight, multi-Position, Hours and boundary tests | Implemented and verified. Supported persisted offset envelope is 0–1440 minutes: one day early and at most three days late |
| Person/Vehicle conflicts | Current publication plus active draft, actual full datetimes, inactive history excluded | `app/rostering/conflicts.py`; Region details redacted outside authority | Operation advisory lock closes cross-Workday check/write race | Scheduling suite and PostgreSQL concurrent-publish/reschedule tests | Implemented and verified |
| Position capability and Dice | Explicit Manager block, Employee preference and trusted history precedence; manual Manager selection remains possible | Canonical aliases; two distinct scheduled current-published Workdays for inferred history; Dice adds active employment, local Region, group and track familiarity rules | Publication records raw WORKED idempotently | Signal precedence, canonical history, recommendation contract, cross-Region and browser tests | Implemented and verified |
| Manager end-to-end roster | Region-scoped Manager/Sub-Manager capability; whole Workday publication | Calendar create, Builder, presets, People/Vehicles, travel, Preview, Publish, response, republish/history, cancellation and reschedule | Shared draft rejects stale writes; publication atomic | Route and responsive browser workflows plus PostgreSQL concurrency | Implemented and verified in automation; real concurrent browsers and operational acceptance pending |
| Employee end-to-end experience | Linked Person plus active role; cross-Region assignment grants only event visibility | Invitation activation, login, Month/List/Day, Open Positions, responses, Hours, preferences, offline namespace and device controls | Self-service is Person-bound and future-effective-time bounded | Auth, employee, privacy, PWA, browser and route suites | Implemented and verified in automation; real MFA, Push, offline and physical-device acceptance pending |
| Role/privacy matrix | Capabilities plus independent Region visibility, not a hierarchy | Central policy checks; private note/accommodation filtering; Contractor fail-closed behavior | Security-sensitive changes invalidate/reclassify sessions and use fresh auth | Security, role, privacy, Contractor and PostgreSQL anti-replay suites | Implemented and verified in code; public-host matrix pending |
| Reusable operational places | Region base remains `Region.base_*`; Tracks remain Tracks; catalogue adds only accommodation/other destinations | Region-owned active/archive catalogue, scoped audit, optimistic version, searchable HTML suggestions and unrestricted one-off text; name/address copied into draft/published snapshots | Place row locked and version checked; published rows never refer to live text | Domain and route snapshot/archive/one-off regressions; browser interaction added | Implemented and locally verified; staging usability pending |
| Shared travel and accommodation | Operation/TravelLeg is shared; Person-level rows hold exceptions | One standard overnight plan updates all participating People; multi-Position travel consistency, opt-out, individual hotel/address/destination/duration and multiple assignment vehicles remain supported | Save rebuilds generated legs and Travel draft atomically; publish includes parent and generated Travel | Existing fidelity/route/browser tests plus place snapshot coverage | Partially implemented. Arbitrary parties per leg, vehicle continuity and room allocation require decisions |
| IV equipment setup | Region-owned reusable configuration; no stock/asset system | Named setup with notes, ordered items and quantities, duplicate/archive lifecycle; Builder copies adjustable revision-owned items; Preview/Day/diff/export show snapshots | Catalogue updates lock/version check; Workday save replaces only current draft rows | Model/service, route and browser regressions | Implemented and locally verified as the approved neutral foundation |
| Personal Month open positions | Published schedule and current capability are authoritative | Date-bounded query with bulk capability evaluation | Read-only | Synthetic 25-open-position measurement remains at six SQL statements or fewer | Implemented and verified; generated-Travel-heavy representative staging data should still be profiled |
| External racing and Track maps | External facts are optional evidence; manual rostering always works | Provider-neutral observations, bounded refresh/backoff, trusted image policy and last-good cache | Scheduler advisory lock and bounded transactions | Adapter, map security/cache and scheduler tests | Implemented and verified in code; live provider availability remains pending |
| Backup/recovery, live Push, public proxy/WebAuthn, real devices | Deployment environment is authoritative | Documented contracts only | Requires isolated staging operations | CI cannot prove these | Implemented but insufficiently tested or operationally pending |

## Confirmed defects resolved in this pass

1. Reschedule treated the generic move acknowledgement as conflict acceptance. It now requires a separate review and stable conflict-state confirmation, and rolls back the entire attempted move on failure.
2. A legacy nominal-date query could contradict interval conflicts. It was removed from Preview; the Builder retains only a clearly informational active/assigned same-date hint.
3. Ordinary draft cloning dropped programme and allowance child rows. One clone helper now preserves all revision-owned children; each intentional exception is explicit.
4. Reusable hotel/location and IV configuration foundations were absent. Additive regional catalogues and immutable revision snapshots now cover the approved neutral behavior.
5. Personal Month evaluated Open Position eligibility per row. A 25-row synthetic measurement exposed the scaling shape; eligibility is now bulk-loaded with a bounded query count.

## Historical requirements reconciliation

| Earlier idea | Current state |
|---|---|
| Engineering Manager across Regions | Requires product decision. Current safest behavior is explicit regional grants or Admin; no implicit cross-Region authority. |
| Crew Group Manager / discipline publishing | Requires product decision. Crew Groups classify people/positions; they grant no management authority. Publication remains whole-Workday. |
| Global Viewer | Requires product decision. Viewer remains explicitly Region-scoped. |
| Employment classification as authority | Requires product decision. RoleGrant remains access authority; Dice uses known active employment roles only as an eligibility input. |
| Category timing defaults | Race Day derivation and Track travel defaults are implemented. Other category defaults need an approved rule before expansion. |
| Open Position auto-accept | Explicitly deferred. Manager selection and Publish remain required. |
| Cross-Workday move/replace assistance | Partially implemented through conflict review and immutable reschedule; automatic person/slot moves are not approved. |
| Truck driver enforcement timing | Requires product decision. Current service validates exactly one qualified designated driver when a used truck is saved. |
| Advanced vehicle allocation | Partially implemented: reusable/borrowed vehicles, capacity and driver designation exist. Automatic routing/continuity is not approved. |
| Manager personal calendar | Already implemented differently: personal Month is assignment-led; management does not broaden it. Crew calendar is the management view. |
| Month/List/Day and mobile fidelity | Implemented in automation; physical-device acceptance remains pending. |
| Notices, cross-Region assignments and position preferences | Implemented and verified in code. |
| Hotels/locations | Implemented in this pass without duplicating Region bases or Tracks. |
| Travel groups/shared vehicles | Partially implemented through the shared standard plan and Person exceptions; arbitrary per-leg parties/continuity require decisions. |
| IV setups | Neutral reusable/snapshot foundation implemented; inventory and asset allocation explicitly excluded. |
| Overnight Travel and programme evidence | Implemented. Programme evidence is retained when editing/declining, but deliberately not copied to a different-date reschedule. |
| TAB source | Explicitly deferred until a demonstrated gap and approved trusted adapter exist. |
| PWA/offline and Push | Implemented, live qualification pending. |
| Backup/restore and real-data migration | Operationally pending / separately authorized. No live Deputy dependency is permitted. |

## Decision register

| Decision required | Recommended alternatives | Safest current behavior |
|---|---|---|
| Cross-Region Engineering Manager | Explicit multi-Region grants; or a narrowly defined global engineering capability | Explicit grants only |
| Crew Group-specific management | Add an independently scoped capability with whole-Day conflict rules; or keep Region management only | Crew Group grants no authority |
| Global Viewer | Explicit global read-only grant; or continue Region grants | Region-scoped Viewer |
| Discipline-specific edits/publication | Whole-Day ownership; or introduce a coordinated section-lock/publication model | Whole-Workday publication |
| Employment classification | Separate Person metadata; derive from active role; or reporting-only field | Do not alter RoleGrant semantics |
| Truck driver enforcement point | Block Save; block Publish; or warning plus explicit override | Current validated Save behavior |
| Travel parties and vehicle continuity | Explicit named parties per leg; or one shared plan plus exceptions | Existing standard plan plus explicit Person/vehicle choices |
| Accommodation allocation | Shared named stay only; or explicit room/occupancy records | Shared default name/address plus Person override; no inferred rooms |
| Equipment categories/inventory | Free-text templates; controlled categories; or separate inventory service | Free-text configuration snapshots only |

## Deployment boundary

CI can qualify migrations, PostgreSQL semantics, code, security regressions and Playwright behavior. It cannot establish public-host proxy identity, WebAuthn RP/origin, real Push delivery, device/browser behavior, backup restoration, persisted restart behavior, provider availability, or representative-volume latency. Those remain `PENDING` in `STAGING_QUALIFICATION.md` until observed on an explicitly authorized deployment.
