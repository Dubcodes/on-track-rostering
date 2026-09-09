# Build status — 2026-09-10

## Current result

The Foundation Corrections and Re-Deputy UX Convergence implementation is complete and qualified on GitHub `main` at `4d900803d8f4353757b3705cb49ed7acb56000fa`. It adds the security/P1 corrections, shared person-day and publication-diff models, regional administration, reminder/worker hardening, and employee/offline UX described below. Local PostgreSQL execution remains pending because no disposable `ONTRACK_TEST_DATABASE_URL` is configured. Docker was not invoked locally. No production deployment is claimed.

## Implemented

- Standalone FastAPI/Jinja/SQLAlchemy application, append-only Alembic chain, PostgreSQL 17 production target, retained production Compose stack, and no Re-Deputy runtime dependency.
- Users and separately rosterable People, one-to-one linking, scoped active/pending/revoked role grants, signup approval with explicit person creation/linking, invitation/account lifecycle, final-Admin protection, epoch-based revocation, and 15-minute fresh authentication.
- Argon2id PINs, opaque hash-only sessions, throttling, CSRF and same-origin checks, secure-cookie configuration, passkey registration/login/removal with bound one-use challenges, and encrypted replay-resistant optional TOTP.
- Stable workdays and immutable published snapshots, private drafts, one structured Preview/history diff, row-lock publication, safe human summaries, Open applications, Manager selection, and policy-driven atomic employee decline.
- Region-aware Month, Day, Crew, Open, employee/management Hours, persistent themes, a searchable hint-bearing builder, scoped accounts, regional track administration, and Admin lifecycle/policy administration.
- Shared multi-assignment person/day spans, fortnight totals, overnight handling, no implicit break deductions, NZ-local date boundaries, national/regional observed holidays, and current-publication-only reads.
- User-isolated upcoming-work/own-Day caches, CSRF-free generated offline HTML, cross-user cache deletion, cache-backed freshness, and sequential cross-month prefetch of today plus the next three actual workdays.
- Encrypted push subscriptions, prior/new publication audience union, deterministic two-day/night/one-hour reminders, PostgreSQL skip-locked claims with expiring leases, per-delivery idempotency, endpoint retirement, and bounded retry.
- Regional signup/person linking, Admin credential-floor enforcement across primary/passkey login, shared trusted-proxy origin/client-IP resolution, broad read-only scoped Viewer semantics, account-directory scoping, and nonce-based CSP track colours.

## Qualification evidence

- Ruff: passed for `app`, migrations, scripts, and tests.
- Python compile: passed for application, migrations, scripts, and tests.
- Pytest local deterministic suite: **44 passed, 6 PostgreSQL-only tests skipped** because `ONTRACK_TEST_DATABASE_URL` is not configured. SQLite is used only by narrow unit/route tests and is not PostgreSQL qualification.
- Node syntax: passed for passkey, notification, application, and service-worker scripts.
- Full Alembic PostgreSQL-dialect SQL generation through `c8e451d10a77`: passed during development; the final release gate repeats it. This checks SQL generation, not execution.
- Dependency consistency: local `pip check` passed. The exact-head GitHub gate also passed `pip check` and `pip-audit --local` with no known vulnerabilities; the local advisory check remains pending because advisory access was unavailable in the sandbox.
- Responsive Playwright: the exact-head GitHub gate passed **4 tests** at 1280, 430, 375, and 320 pixels, covering Employee, Manager, Viewer, and Admin routes, overflow, theme, track colour, picker, responsive list behavior, write denial, and browser console/page errors.
- GitHub Actions run [34395787093](https://github.com/Dubcodes/on-track-rostering/actions/runs/34395787093) passed on exact commit `4d900803d8f4353757b3705cb49ed7acb56000fa`: PostgreSQL-dialect SQL generation, real PostgreSQL migration execution, **50 PostgreSQL-backed tests**, dependency audit, JavaScript syntax, all four browser widths, `docker compose config --quiet`, and the production Docker image build.

## Explicitly pending or deferred

- Local real-PostgreSQL execution is pending; no native/local or explicitly configured remote instance was available. The application was not switched to SQLite.
- Real passkey hardware/browser ceremony and real Web Push provider delivery require deployment-origin/VAPID validation. Automated tests cover server ceremony binding and injected delivery outcomes.
- Recovery codes are not implemented. Production reminder delivery still depends on configuring VAPID and invoking the worker from a durable scheduler.
- Travel, accommodation, vehicle, allowance, programme source, and override schemas exist, but complete management editors and external racing ingest are outside this finish pass.
- Structured field-level Preview changes produce safe Human Change rows; private note text and free-form publication reasons are not broadly shown.
- Actual Portainer rollout, persistence restart, backup restore rehearsal, and production monitoring remain deployment-operator work.

## Known architecture constraint

Each workday currently owns one shared `current_draft_revision_id`. Competing publication is safely rejected and stale drafts are preserved/detached, but independent simultaneous per-Manager draft branches are not represented. This is documented for a future architecture review rather than silently redesigned in a qualification pass.

## Release rule

A releasable `main` requires a clean local gate (with unavailable PostgreSQL/dependency-advisory checks explicitly pending), a successful GitHub PostgreSQL/browser/image workflow, no committed credentials or private keys, and no force push. A green CI run does not itself prove a successful Portainer deployment.
