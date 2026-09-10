# Build status — 2026-09-10

## Current result

The focused Foundation repair pass is implemented locally on top of qualified baseline `d4570dbdca1db18b13df2afd3d3fb864d34deaa7`. It hardens the single shared draft against stale writes and simultaneous creation, limits event-only and offline Day rows to personal data, bases cross-region display on Person home region, and makes today-plus-three upcoming-work selection server-authoritative. Exact-head GitHub qualification evidence will be recorded after the repair commit is pushed. Local PostgreSQL execution remains pending because no disposable `ONTRACK_TEST_DATABASE_URL` is configured. Docker was not invoked locally. No production deployment is claimed.

## Implemented

- Standalone FastAPI/Jinja/SQLAlchemy application, append-only Alembic chain, PostgreSQL 17 production target, retained production Compose stack, and no Re-Deputy runtime dependency.
- Users and separately rosterable People, one-to-one linking, scoped active/pending/revoked role grants, signup approval with explicit person creation/linking, invitation/account lifecycle, final-Admin protection, epoch-based revocation, and 15-minute fresh authentication.
- Argon2id PINs, opaque hash-only sessions, throttling, CSRF and same-origin checks, secure-cookie configuration, passkey registration/login/removal with bound one-use challenges, and encrypted replay-resistant optional TOTP.
- Stable workdays and immutable published snapshots, one optimistic-versioned shared Manager draft, row-lock draft creation/mutation/publication, stale-submission conflicts, one structured Preview/history diff, safe human summaries, Open applications, Manager selection, and policy-driven atomic employee decline.
- Region-aware Month, Day, Crew, Open, employee/management Hours, persistent themes, a searchable hint-bearing builder, scoped accounts, regional track administration, and Admin lifecycle/policy administration.
- Shared multi-assignment person/day spans, fortnight totals, overnight handling, no implicit break deductions, NZ-local date boundaries, national/regional observed holidays, and current-publication-only reads.
- Event-only Contractor Day rows restricted to self, authorized regional Crew View retained online, personal-only Day JSON/offline caches, home-region-based cross-region display, CSRF-free generated offline HTML, cross-user cache deletion, cache-backed freshness, and sequential server-selected cross-month prefetch of today when rostered plus the next three actual workdays.
- Encrypted push subscriptions, prior/new publication audience union, deterministic two-day/night/one-hour reminders, PostgreSQL skip-locked claims with expiring leases, per-delivery idempotency, endpoint retirement, and bounded retry.
- Regional signup/person linking, Admin credential-floor enforcement across primary/passkey login, shared trusted-proxy origin/client-IP resolution, broad read-only scoped Viewer semantics, account-directory scoping, and nonce-based CSP track colours.

## Qualification evidence

- Ruff: passed for `app`, migrations, scripts, and tests.
- Python compile: passed for application, migrations, scripts, and tests.
- Pytest local deterministic suite: **49 passed, 10 PostgreSQL-only tests skipped** because `ONTRACK_TEST_DATABASE_URL` is not configured. SQLite is used only by narrow unit/route tests and is not PostgreSQL qualification.
- Node syntax: passed for passkey, notification, application, and service-worker scripts.
- Full Alembic PostgreSQL-dialect SQL generation through `c8e451d10a77`: passed during development; the final release gate repeats it. This checks SQL generation, not execution.
- Dependency consistency: local `pip check` and `pip-audit --local` passed with no known vulnerabilities.
- Responsive Playwright: **4 local tests passed** at 1280, 430, 375, and 320 pixels against an isolated visual-test SQLite database, covering Employee, Manager, Viewer, and Admin routes, overflow, theme, track colour, picker, responsive list behavior, write denial, and browser console/page errors. This is browser qualification, not PostgreSQL qualification.
- Exact-head GitHub Actions PostgreSQL/browser/Compose/image qualification is pending the repair commit and normal push.

## Explicitly pending or deferred

- Local real-PostgreSQL execution is pending; no native/local or explicitly configured remote instance was available. The application was not switched to SQLite.
- Real passkey hardware/browser ceremony and real Web Push provider delivery require deployment-origin/VAPID validation. Automated tests cover server ceremony binding and injected delivery outcomes.
- Recovery codes are not implemented. Production reminder delivery still depends on configuring VAPID and invoking the worker from a durable scheduler.
- Travel, accommodation, vehicle, allowance, programme source, and override schemas exist, but complete management editors and external racing ingest are outside this finish pass.
- Structured field-level Preview changes produce safe Human Change rows; private note text and free-form publication reasons are not broadly shown.
- Actual Portainer rollout, persistence restart, backup restore rehearsal, and production monitoring remain deployment-operator work.

## Known architecture constraint

Each workday owns one shared `current_draft_revision_id`; independent per-Manager draft branches are not represented. Simultaneous first-edit creation resolves through the locked Workday row, and every shared-draft mutation now rejects a stale `lock_version`, so this deliberate constraint does not permit silent last-writer overwrites. Stale drafts detached by an authoritative decline remain preserved for recovery.

## Release rule

A releasable `main` requires a clean local gate (with unavailable PostgreSQL/dependency-advisory checks explicitly pending), a successful GitHub PostgreSQL/browser/image workflow, no committed credentials or private keys, and no force push. A green CI run does not itself prove a successful Portainer deployment.
