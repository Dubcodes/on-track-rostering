# Build status — 2026-09-08

## Completed

- Standalone FastAPI/Jinja/SQLAlchemy/PostgreSQL 17 repository with independent Compose network and named volume.
- Alembic initial schema with five editable seeded regions and four seeded crew groups; no sample people/workdays.
- Separate Users, People, one-to-one links, scoped role grants, lifecycle fields, trusted devices, pending signup, invitations, passkey public-key storage, and push preference/subscription extension points.
- Argon2id credential rules, generic login failures, account+IP throttling, opaque hash-only sessions, role-sensitive sliding trust, device cap, CSRF/origin checks, security headers, safe redirects, and local CLI Admin bootstrap.
- Admin foundation UI for regions, tracks/colours, base positions, people, direct linked accounts, and one-time invitations.
- Region-aware central policy for Contractor, Employee, Sub-Manager, Manager, Viewer, Admin.
- Stable Workday plus DRAFT/PUBLISHED snapshot revisions, stable assignment slot keys, ASSIGNED/OPEN/TBC/MANAGER_ACTION_REQUIRED, private notes, optimistic base-revision check, row lock, atomic publish, worked-history learning, audit, and human publication history.
- Capability provenance for worked, employee allow/opt-out, Manager allow/block with correct precedence at base-position level.
- Responsive Month calendar/list and Day views, public-holiday marker, no-deduction/overnight span calculation, context Help, Settings foundation, PWA shell, user-namespaced read-only JSON cache, and sequential three-day prefetch.
- Provider-neutral programme source/manual override, Operation, travel leg, vehicle, accommodation snapshot, and allowance-indicator extension schemas.
- Windows local-development policy is now explicit: no Docker/Docker Desktop execution, direct Python tooling, and required native/local or explicitly configured remote PostgreSQL through `DATABASE_URL`. The legacy `ONTRACK_DATABASE_URL` alias remains only for the production Compose configuration.
- Added migration `8b31d0a7c9e2` with explicit Open-position applications and an idempotent notification-event outbox; the original production migration was not rewritten.
- Added eligible Open-position discovery and explicit employee application. Managers select an applicant into a draft; Publish accepts the matching selection and closes competing applications.
- Added confirmed employee decline as a new atomic immutable publication for both `OPEN_IMMEDIATELY` and `MANAGER_REVIEW`. Older publications remain unchanged and an obsolete Manager draft is detached.
- Added regional Crew View with normal day notes and server-filtered private notes, plus scoped crew search, creation, archive/restore, multi-group membership, and capability controls.
- Added employee base-position allow/opt-out controls. Opposing self-service or Manager preference signals are replaced while worked history remains intact.
- Added Admin account activation/disable, device revocation, invitation revocation, and pending-signup rejection. Account status changes rotate `auth_epoch` and revoke active sessions.
- Added an application/build marker, `docs/AI_TASK_TEMPLATE.md`, append-only migration policy, Portainer-from-`main` guidance, and GitHub CI for real PostgreSQL plus production artifact qualification.

## Incomplete / deferred

- WebAuthn tables exist but registration/authentication ceremonies are not implemented; TOTP is deferred.
- Push tables/preferences and idempotent authoritative events exist, but recipient expansion, VAPID, subscription APIs, delivery/retry, and reminder scheduling are deferred.
- Public signup records deduplicated pending requests and is account/address throttled; Admin can reject requests, but safe existing-person linking and approval are deferred.
- Crew/capability workflows are usable foundations, but additional-region membership and richer account/invite status need expansion. Management hours, travel/hotel/vehicle editors, track maps, racing ingest, IV, audit explorer, and branded configuration remain deferred.
- Admin account creation/disable/device revocation are available; complete role/scope mutation and elevation must enforce credential policy, epoch rotation, and fresh reauthentication.
- Change history currently records publication-level human summaries; field-level prose is shown in Preview but not persisted as individual human events.
- Browser responsive automation is not yet present; responsive CSS exists and manual targets are documented.

## Tests actually run

- `.\\.venv\\Scripts\\python.exe scripts\\release_gate.py` — passed after the continuation build: compile, Ruff, **14 pytest tests**, `pip check`, `pip-audit` (no known vulnerabilities), and both JavaScript syntax checks. PostgreSQL integration is **pending** because `ONTRACK_TEST_DATABASE_URL` is not configured; no SQLite result is treated as PostgreSQL qualification. Browser qualification was explicitly reported pending.
- `DATABASE_URL=postgresql+psycopg://alias-check:...` with `Settings().database_url` — passed configuration-only alias verification without making a database connection.
- The suite covers the HTTP Manager create → assign → Preview → Publish → Employee Month/Day/Crew journey, management/Admin denial, Viewer private-note and decline denial, draft non-leakage, Admin disable/session invalidation, Open application deduplication/selection/publication, and both decline policies. Its database is still SQLite and is not PostgreSQL locking/constraint proof.
- `.\\.venv\\Scripts\\ruff.exe check app migrations scripts tests` — passed after formatting/fixes.
- `.\\.venv\\Scripts\\python.exe -m compileall -q app migrations scripts tests` — passed.
- `.\\.venv\\Scripts\\python.exe -c "import app.models, app.main; ..."` — imports passed; 29 mapped tables.
- `node --check app\\static\\app.js` and `node --check app\\static\\service-worker.js` — passed through the release gate.
- `.\\.venv\\Scripts\\python.exe -m pip check` — passed: no broken requirements.
- `.\\.venv\\Scripts\\python.exe -m pip_audit --local` — the sandboxed attempt was blocked by its loopback proxy; the approved network retry passed with **no known vulnerabilities found**.
- A Compose configuration check was performed earlier, before the Windows no-Docker boundary was clarified. It is historical evidence only and is not part of local qualification. No further Docker commands should be run on this machine.
- `DATABASE_URL=postgresql+psycopg://... .\\.venv\\Scripts\\python.exe -m alembic upgrade head --sql` — passed for both revisions and emitted 445 lines of PostgreSQL-dialect SQL. This validates SQL generation, not execution.
- Alembic was generated against an empty disposable metadata snapshot. A deliberate SQLite migration rehearsal was rejected by the PostgreSQL-style cyclic pointer constraints; it is not counted as a migration pass because SQLite cannot add those constraints.
- No usable native/local or explicitly configured remote PostgreSQL test instance was available. Real PostgreSQL migration and route integration checks remain pending; SQLite results must not be treated as PostgreSQL qualification.
- No Docker command was invoked after the Windows no-Docker boundary was established.
- Playwright is not installed/configured locally, so responsive browser automation is pending. GitHub CI and production Compose/image changes were reviewed statically but were not executed locally.

## Assumptions

- Direct Admin account creation is acceptable for the initial vertical proof; invitations remain the preferred crew onboarding path.
- Region Manager/Sub-Manager invitation administration and role mutation are intentionally narrowed until elevation reauthentication has a complete UI.
- National NZ holidays are enabled now; statutory anniversary geography remains a separate future configuration from operational regions.

## Known issues / risks

- The migrations have not yet executed on a real PostgreSQL server in this environment. This is the highest-priority qualification gap.
- This directory has no `.git`, branch, history, or remote. It is structured for Portainer-from-`main`, but cannot be pulled until connected to the intended GitHub repository and committed through the user's chosen workflow.
- The app is a functional foundation, not yet production-ready, until passkeys/elevation flow, PostgreSQL route integration, responsive browser tests, and separate-server deployment/persistence tests pass.
- A completely offline browser cannot receive remote cache revocation; cached payload scope is intentionally minimal and logout clears site data.

## Next recommended build

First run both migrations and the operational route suite against disposable real PostgreSQL, including concurrent application/publish/decline cases, then connect the directory to the intended GitHub `main` workflow and observe CI. The next substantial product build should complete privilege-safe role/scope mutation, signup approval/person linking, and WebAuthn, followed by notification recipient expansion/push delivery and Playwright coverage for Open/decline/mobile/cache behavior.
