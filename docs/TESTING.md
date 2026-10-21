# Testing

Create the Windows development environment and run directly under Python:

```powershell
.\.venv\Scripts\python.exe scripts\release_gate.py
```

The deterministic gate compiles Python, lints app/migrations/scripts/tests, runs pytest, checks installed dependencies, audits them for known vulnerabilities, checks every top-level static JavaScript file, and renders every Jinja template with autoescape enabled. Set `ONTRACK_SKIP_DEPENDENCY_AUDIT=1` only when advisory network access is unavailable; the result is then explicitly pending rather than passed.

The unit suite covers configurable global branding defaults, Aurora theme fallback without overwriting valid preferences, persistence, authorization, escaping, manifest/offline/notification propagation, credential minimums and Admin elevation, safe redirects, pending-grant isolation/activation/revocation, final-Admin safety, workflow-local fresh authentication, direct-account defaults/validation recovery/linking/duplicate prevention, invitation status and return context, trusted-proxy/origin authority, WebAuthn challenge expiry/binding/replay, encrypted TOTP setup/login/replay, signup Person scope, base-position eligibility and independent preference clearing, structured publication diff/redaction, shared-draft stale-write rejection and route conflicts, Viewer read/write policy, scoped account directories, Contractor/Employee online and personal offline Day privacy, home-region cross-region semantics, regional catalogue authority, canonical source-to-Track Region inheritance and duplicate prevention, trusted map fallback/selection/cache/Day rendering, Month/Crew/Day reads, multi-assignment person/day spans, Open applications and decline snapshots, encrypted/idempotent push delivery with retry/permanent failures, audience union, fixed-time reminders, fortnight boundaries, NZ-local dates, regional/observed holidays, CSP track colours, cross-user cache deletion, exact today-plus-three cross-month upcoming work, and template escaping. SQLite is used only for these narrow deterministic tests. It is not an application runtime, integration-test substitute, or PostgreSQL qualification.

The PostgreSQL-only suite additionally exercises simultaneous first-draft creation with two sessions, normalized Person-email creation serialization, stale detail and assignment mutations, Publish invalidation of an open editor version, concurrent publication, rollback atomicity, database constraints, immutable decline behavior, and skip-locked notification claims.

For real PostgreSQL migration checks, point the gate at a blank disposable database:

```powershell
$env:ONTRACK_TEST_DATABASE_URL='postgresql+psycopg://ontrack:test@localhost:5432/ontrack_test'
.\.venv\Scripts\python.exe scripts\release_gate.py
```

`ONTRACK_TEST_DATABASE_URL` must identify a blank or disposable PostgreSQL test database, never a production database. The runner always generates the full PostgreSQL-dialect migration SQL, then when the URL is present passes it to the application as `DATABASE_URL` and upgrades twice before tests. If the variable is absent, the gate reports PostgreSQL integration as pending and does not fall back to SQLite.

Additional local qualification:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\pip-audit.exe --local
```

With the disposable PostgreSQL URL configured, run `alembic upgrade head` twice, verify exactly five regions/four groups/no people/no workdays, bootstrap an Admin, execute the Manager → Publish → Employee route flow, restart the Windows application process, and verify persistence and `/health/ready`.

Docker and Docker Desktop must not be used, started, repaired, installed, or configured on the Windows development machine. Compose artifacts remain maintained for qualification and deployment on the separate Docker/Portainer server.

GitHub Actions is the intended real-PostgreSQL and Docker qualification environment. Its release gate provisions PostgreSQL 17, applies Alembic twice, runs the Windows-equivalent code checks, audits dependencies, statically validates Compose, and builds the production image. A green CI run is still not evidence of an actual Portainer rollout.

Responsive manual/browser qualification targets widths 1280, 430, 375, and 320 for login, Month, Day, Administration, Builder, Preview, Management, Crew directory, Person-first creation/detail/account setup, bulk onboarding, Advanced Accounts, Contractor lifecycle, Help, Master Data and scoped Vehicle controls, Team Hours filters, Audit, and Settings. It includes combined immediate filters and debounced search with shareable URLs, Back/Forward and no history growth; direct-account context and safe errors; expired fresh-auth invitation creation without automatic action replay; one-time copy behavior; changing branding; permanent overflow diagnostics; and repeated Online Sources/Track saves with expanded-state, feedback, scroll-continuity and browser-history assertions. Confirm no horizontal overflow; calendar/list switching and swipe navigation; keyboard selection; note visibility; public-holiday focus labels; and draft invisibility. Exact-head release-gate run `39550789725` passed **79 Playwright tests** and **347 PostgreSQL-backed tests** at application-behavior SHA `f31799ded4c0fce42a552b5c23e88ef8247627d7`. Neither result claims physical-device manual testing or private-staging qualification.

When native Playwright tests and browser binaries are installed, set `ONTRACK_RUN_BROWSER_TESTS=1`; the gate runs `tests/browser`. If the installed executable intentionally differs from Playwright's bundled revision, set `ONTRACK_PLAYWRIGHT_CHROMIUM_PATH` to that verified local executable. Without browser opt-in, the gate reports browser qualification pending. A disposable SQLite database may be used only for this narrow visual test and never as PostgreSQL qualification.
