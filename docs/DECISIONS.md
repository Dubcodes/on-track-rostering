# Decisions

## 2026-09-08 — standalone authoritative service

On Track has no Deputy module, field, credential, database, volume, API, or deployment dependency. Re-Deputy was read only and used for behavioral study.

## PostgreSQL and explicit migrations

PostgreSQL 17 is the production database from the first migration. Alembic is the schema authority; application startup never calls `create_all()`. UUID application-generated keys keep identities stable and testable.

GitHub `main` may already be a Portainer deployment source, so committed migrations are append-only production artifacts. Do not rewrite an existing migration unless deployment history has first been established and the correction is explicitly safe. New schema changes receive a new revision, support blank-database upgrades, and preserve existing data. Downgrades are documented recovery tools, not an automatic production rollback strategy.

External racing data is planning evidence, not roster authority. Provider observations reconcile into one canonical event with field provenance. A stable identity from the same provider may correct programme facts, while cross-provider disagreement and date/Track movement remain review conflicts; partial data cannot downgrade a complete programme. Creating a roster from an event produces one private Workday draft transactionally. Later source updates may synchronize only that linked unpublished draft. Applying latest facts to an already-published Workday first creates a private draft. Only Preview → Publish changes the authoritative employee roster.

## Scheduled source refresh and persistent Track maps

The deployment runs a scheduler process from the same application image. One dedicated PostgreSQL advisory-lock connection owns a whole cycle so ORM commits cannot release singleton ownership. Discovery, programme detail, and map refreshes have separate cadence and bounded failure backoff. Disabling a racing provider suppresses that provider's automatic discovery and programme-detail requests; Track-map refresh remains independent. Official maps are accepted only from trusted Love Racing hosts after full file-signature, dimension, size and hash validation. A regional Manager's manual image wins until reset. Last-good automatic files survive upstream failure, and `/app/data` is a named volume backed up with PostgreSQL. Ordinary catalogue/Day availability checks use persisted metadata, actual file size and a bounded header rather than repeatedly reading complete images; refresh recovery still fully validates the retained automatic file.

## Isolated one-time transition import

Deputy transition capture is a manual, finite migration boundary rather than a runtime integration. Parsing accepts only named capture sections and allowlisted schedule fields. Apply is atomic and idempotent, creates rosterable People but never accounts, preserves existing On Track publications, skips unresolved locations, publishes without notification events, and retains only sanitized source-reference keys/hashes needed for replay safety. Authentication, cookies, credentials, payroll, pay rates, timesheets, approvals, and break data are ignored and never stored.

## Windows local development excludes Docker

FastAPI, Alembic, tests, and tooling run directly under Windows/Python. The required database engine is a native/local PostgreSQL installation or an explicitly configured remote development PostgreSQL instance supplied through `DATABASE_URL`. Docker and Docker Desktop are not run, started, repaired, installed, or configured on the development machine. Production and isolated-staging Compose files remain deployment artifacts for the separate Docker/Portainer server. Narrow SQLite unit tests never count as PostgreSQL integration qualification; unavailable PostgreSQL checks are reported as pending.

## Revision snapshots over mutable published rows

Employee consistency and recoverability matter more than storage minimisation. A stable workday points to immutable published snapshots. Track/person/position/vehicle display labels are snapshotted so later archive/rename operations do not corrupt historical presentation.

## Roles are policies, not hierarchy

The central policy layer uses explicit capability sets and regional scopes. This allows a Viewer to see more regions than a Manager while remaining unable to mutate.

## Auth elevation epoch

Trusted devices store the user's `auth_epoch` and trust class. A privileged role change must increment the user epoch; old devices then fail validation and fresh authentication creates a correctly classified 14-day device. This avoids silently upgrading a 90-day Employee trust token.

## CSRF and same-origin defense in depth

Mutations require a random per-device CSRF token whose hash is stored server-side, plus Origin/Sec-Fetch-Site checks. Session tokens are HttpOnly; production cookies default to Secure through deployment configuration.

## Offline cache stays deliberately small

Only purpose-built Month/Day JSON is cached and caches are namespaced by authenticated user ID. Management datasets are never put in the offline cache. Logout sends `Clear-Site-Data`. Remote revocation cannot erase a device that stays physically offline, so payloads remain minimal.

## Pending privilege grants and fresh authentication

Manager-created Sub-Manager grants remain `PENDING` until the account owner completes a primary authentication. The login that activates a grant does not inherit it: activation rotates `auth_epoch`, invalidates older devices, and requires a new login. Sensitive account and authenticator changes require primary authentication within the preceding 15 minutes. Active privilege removal also rotates the epoch and revokes sessions, and the final active Admin cannot be removed.

## WebAuthn and optional TOTP

Passkeys use the maintained `webauthn` library, discoverable credentials, required user verification, one-use server challenges, and deployment-bound RP ID/origin verification. Public-key credentials remain server-readable by design; TOTP secrets and push endpoint payloads are encrypted at rest with a key derived from the application secret and a domain separator. TOTP is optional unless the deployment enables the Admin or Manager requirement flags. Accepted TOTP counters are stored so a valid code cannot be replayed inside its time window. TOTP confirmation issues ten recovery codes exactly once in plaintext. Only SHA-256 hashes are stored; consumption is user-scoped, row-locked, atomic, and single-use. Regeneration invalidates every older unused code, and disabling TOTP removes its recovery codes. These codes recover the optional TOTP factor only; they are not primary-credential reset tokens.

## Primary credential recovery is policy-scoped and bounded

On Track deliberately has no public emailed “forgot password” flow. After fresh authentication, an Admin may create a dedicated reset globally. A scoped Manager may do so only for an ordinary Employee, Contractor, or Sub-manager account that the existing account-administration policy allows them to control; active Manager/Admin security contexts remain Admin-only. The random token is stored only as a hash, expires after 60 minutes, invalidates an earlier unused reset, is handed off in an absolute configured-trusted-origin URL fragment, and is consumed once under a row lock. The replacement credential must satisfy the target account's current policy, including the stricter Admin rule whether that role is pending or active.

Successful reset increments `auth_epoch`, revokes trusted devices, and invalidates user-bound authentication challenges. It does not remove passkeys, TOTP, TOTP recovery codes, roles, Person linkage, or pending grants, and it does not activate a pending grant. The local `python -m app.cli reset-credential --email user@example.com` path uses `getpass` and follows the same boundary.

## Open applications and direct decline publication

Applications bind to a published revision plus stable slot key so stale decisions are detectable. Selecting an applicant changes only a Manager draft; Publish remains the authority transition. Decline is deliberately different: after explicit confirmation, it locks the workday and creates a complete new immutable publication atomically according to the region policy. Any older Manager draft is detached rather than silently rebased.

## Workday rescheduling creates a replacement instead of mutating history

A published Workday's date is historical identity and is never changed in place. Rescheduling marks the source ABANDONED and creates a new Workday linked by `rescheduled_from_workday_id`, cloning the source's current PUBLISHED roster into a fresh immutable publication. This preserves an auditable A → B → C chain and prevents a past roster snapshot from silently acquiring a new date. A predecessor that already has a replacement cannot be reinstated.

Generated Travel is abandoned with the source and freshly generated for the replacement. The replacement does not reuse or inherit external-event identity, Open Position applications, personal responses, Operation identity, generated-Travel identity, or programme/source provenance. Assigned Employees and Contractors may record ACCEPTED or DECLINED against the replacement Workday and Person; absence is Pending. Decline is a Manager-action signal and never automatically removes the assignment.

## Notification event outbox

Roster publication and decline record deterministic event keys in the same database transaction as the authoritative change. This prevents duplicate event creation and isolates optional push failures from roster integrity. Recipient expansion, preference enforcement, encrypted subscriptions, per-endpoint idempotency, VAPID delivery, permanent endpoint deactivation, and bounded exponential retry run in the independent `deliver-notifications` worker. The worker uses PostgreSQL row locks with skip-locked claims and expiring leases, so workers do not concurrently own the same event and abandoned work can recover. It also creates deterministic two-day, night-before, and one-hour reminder events from the current published person/day effective start. Authoritative roster transactions never wait for the push provider.

## Regional administration boundary

Manager and Admin may administer regional tracks and scoped accounts. Region policy/lifecycle and the current global Crew Group/Base Position catalogues require Admin. Sub-Manager retains practical roster mutation but has no broad account or catalogue administration. Managers may administer ordinary linked Employee, Contractor and Sub-manager access in their Regions after fresh authentication, but active Manager/Admin security remains Admin-controlled. Historical workday snapshots are never rewritten by catalogue rename, colour, or lifecycle changes.

## Hours are derived from publication snapshots

Fortnight views calculate from the configured anchor and current published assignment spans. They do not infer breaks or deduct them, handle overnight spans, and show holiday and allowance markers without treating allowances as time. Employee access is personal; management access follows explicit regional visibility policy.

## Flexible multi-Position participation

A Person may deliberately hold multiple Positions on one Workday, whether assigned directly or selected for an Open Position. The Builder warns at selection time instead of prohibiting the save and requires an explicit Move, Keep both, or Cancel decision. Stable assignment slots retain independent timing and notes, while all rows for one Person share a single authoritative travel plan. Person/day participation is aggregated once from the earliest effective start to latest effective finish. Hours, reminders, personal timing, and generated Travel therefore never sum duplicate Position rows as separate worked days.

## Catalogue ordering and regional operational records

Base Position display order is editable catalogue data, not business identity. A nullable numeric order controls current operational selectors; the historical name-based sequence remains only a deterministic fallback and migration backfill. Vehicles are reusable regional catalogue records administered by Admin or the responsible regional Manager, while assignment snapshots protect historical presentation.

## Audit reads remain redacted and scoped

`AuditEvent` is security-redacted at write time. Admin may read the global audit stream; Managers may read only Regions they administer. Sub-Managers and read-only roles do not gain administrative audit access, and the UI presents human summaries with structured redacted detail rather than raw event JSON.

## Leave is an operational warning

Person leave/unavailability is an inclusive whole-date scheduling signal, not an HR, payroll, entitlement, or approval system. It never rewrites an existing roster and never makes a Person ineligible. Authorized Managers may deliberately roster someone on leave after an explicit shared-dialog warning. The date range is visible to permitted roster builders; arbitrary management note text remains confined to scoped Admin/Manager leave administration and is excluded from audit detail and employee-facing data.

## Race Day timing is derived, snapshotted, and explicitly overridable

Race Day operational timing is derived by default from source programme times, Region setup lead, the Workday's Track-travel snapshot, Pack-up, and Return travel. Managers remain authoritative: deliberate Start, On track, Track travel, and Finish overrides are explicitly tracked and survive revision cloning. Published history never consults the current Track master default. Client calculation is feedback only; the save service recalculates non-overridden values from validated database policy and draft inputs.

The final-race source value is a start time. On Track adds the single code-level domain constant of 10 minutes before applying the quarter-hour ceiling. It is deliberately not runtime, environment, Admin, SystemSettings, request, or Workday configuration; a deliberate code correction requires normal release requalification. The historical Re-Deputy +3-minute value is not used. Truck early-start policy is a Person-effective offset only: new Race Days default to 15 minutes, while migrated historical revisions remain zero.

Race and Trial source records are never merged. A read model may present same-Track, same-date, same-discipline activity together when known first times differ by at most three hours. Related Trial timing is context and does not change the Race Day calculation automatically.

Operational place addresses are stored without routing or geocoding. Track/base values are snapshotted into revisions, accommodation address stays assignment-scoped, and Cambridge Harness is never treated as an alias of Cambridge Synthetic.

External HR leave remains a paste-driven operational intake, not payroll. People rollout uses Person UUID worksheets, metadata-only Preview/Apply, and separately generated one-time Employee setup links. There is no automatic onboarding email and no Deputy identity dependency.

## Management UX boundaries are settled

Settings is a personal account/preferences surface. A permission-driven `/manage` hub owns everyday operational navigation and notices; Administration remains system configuration/recovery. Crew is the Person-centric directory/detail UI over the intentionally separate Person and User models, while Accounts remains available as an advanced repair/reconciliation surface. The conservative leave-paste grammar remains fixed until a real copied source sample exists; it must not grow through guessed or fuzzy parsing.

Normal pages do not expose a global build footer or development/migration diagnostics. Build identity belongs in Administration, and Help is practical, contextual and authority-aware.

Personal Month is independent of role authority: it contains the linked Person's own current publication assignments and applicable Open Positions. Primary-Region source events appear only after the Region-wide latest current published SCHEDULED horizon (or today when no horizon exists). Crew is the distinct authorized regional all-crew calendar.

Active Base Position identity is global and canonical across Crew Groups. Ordinary create, rename and import paths share the alias/numbered-position resolver; known historical duplicates are not constrained or silently converged. Notice targets and Admin notice-publisher overrides use append-only relational tables, with override-row presence distinguishing default derivation from explicit empty. Error reports are deliberately small first-party records rather than tickets or arbitrary request captures.

## Dice, transport capacity and account assistance

Dice is a lightweight suggestion over positive facts, never authority: an active linked User with Employee/Sub-Manager/Manager working access, matching Primary Region, active Crew Group membership and canonical capability/history. Contractor, Casual, disabled, expired and unlinked People remain manually selectable but never enter Dice. Same-Track/Position history outranks same-Track related Crew Group history; random choice remains inside the strongest available tier.

Vehicle capacity counts distinct People including the driver. Capacity is nullable for legacy Vehicles and a suggested four for new ordinary records. The Builder compares current and proposed final occupancy: a one-for-one replacement does not prompt unless the replaced Person remains in that Vehicle through another Position; a genuine additional occupant requires Kick last, Keep, or Cancel. Over-capacity is a deliberate Builder decision and remains visible at Preview. A truck requires exactly one explicitly designated Person whose separately maintained truck-driving qualification is true; Position, Crew Group and account role never imply that qualification.

Sub-Manager account assistance is limited to existing ordinary linked People in an operationally managed Region and never authorizes account creation/deletion, role escalation, or control of an active Manager/Admin. An exceptional Crew-Group Manager model is not inferred from Crew Group membership. Until its write boundaries for mixed Workdays, publication, People and accounts are explicitly decided, the existing Region boundary remains the restrictive authority.

## Persistent Track-map ownership

The container entrypoint runs briefly with image root authority only to create and repair ownership of `/app/data/track_maps`, including retained named-volume files, then permanently execs the app or scheduler as `ontrack`. It does not recursively weaken unrelated paths. Automatic map checks are due after 90 days; refresh failures retain the last valid local cache, while manual overrides remain authoritative until explicit reversion.
