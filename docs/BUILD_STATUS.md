# Build status — 2026-09-08

## Current result

The requested foundation, privilege lifecycle, employee delivery, WebAuthn/TOTP, notification worker, hours, and offline-cache hardening are implemented. Local Windows qualification is green for every check that does not require PostgreSQL or an installed Playwright browser. Docker was not invoked locally. GitHub Actions supplied and passed the real PostgreSQL, Chromium, migration execution, Compose, and production-image checks.

## Implemented

- Standalone FastAPI/Jinja/SQLAlchemy application, append-only Alembic chain, PostgreSQL 17 production target, retained production Compose stack, and no Re-Deputy runtime dependency.
- Users and separately rosterable People, one-to-one linking, scoped active/pending/revoked role grants, signup approval with explicit person creation/linking, invitation/account lifecycle, final-Admin protection, epoch-based revocation, and 15-minute fresh authentication.
- Argon2id PINs, opaque hash-only sessions, throttling, CSRF and same-origin checks, secure-cookie configuration, passkey registration/login/removal with bound one-use challenges, and encrypted replay-resistant optional TOTP.
- Stable workdays and immutable published snapshots, private drafts, optimistic/row-lock publication, human history/audit, Open applications, Manager selection, and policy-driven atomic employee decline.
- Region-aware Month, Day, Crew, Open, employee Hours, management Hours, settings, builder, accounts, and Admin paths with scoped private-note and capability behavior.
- Fortnight anchor calculations, overnight spans, no implicit break deductions, holiday/allowance markers, and current-publication-only hours.
- User-isolated offline Month/Day JSON, CSRF-free cached HTML, cross-user cache deletion, and sequential prefetch of the next three actual workdays.
- Encrypted push subscriptions, preference-aware audience expansion, idempotent event/subscription deliveries, VAPID delivery, permanent endpoint retirement, and bounded retry in an independent worker.

## Qualification evidence

- Ruff: passed for `app`, migrations, scripts, and tests.
- Pytest local deterministic suite: **33 passed**; **5 PostgreSQL tests skipped** because `ONTRACK_TEST_DATABASE_URL` is not configured. SQLite is used only by narrow unit/route tests and is not PostgreSQL qualification.
- Node syntax: passed for passkey, notification, application, and service-worker scripts.
- Full Alembic PostgreSQL-dialect SQL generation through revisions `7a91d36e5b20` and `ab24e50d17c4`: passed. This checks SQL generation, not execution.
- Dependency consistency: `pip check` passed. `pip-audit --local` passed with no known vulnerabilities after an approved network retry; the release gate can explicitly mark the audit pending when advisory access is unavailable.
- Responsive Playwright coverage exists for 1280, 430, 375, and 320 pixel widths. Local execution is pending because there is no configured PostgreSQL test database and no project-installed browser binary.
- GitHub Actions run [34175150433](https://github.com/Dubcodes/on-track-rostering/actions/runs/34175150433) passed for implementation commit `2a01531`: PostgreSQL 17 migrations upgraded twice; **38 tests passed** including the five PostgreSQL-only tests; **4 Chromium tests passed** across all target widths; `pip-audit` found no known vulnerabilities; Compose validation and the production image build passed.

## Explicitly pending or deferred

- Local real-PostgreSQL execution is pending; no native/local or explicitly configured remote instance was available. The application was not switched to SQLite.
- Real passkey hardware/browser ceremony and real Web Push provider delivery require deployment-origin/VAPID validation. Automated tests cover server ceremony binding and injected delivery outcomes.
- Recovery codes and scheduled production of reminder events are not implemented.
- Travel, accommodation, vehicle, allowance, programme source, and override schemas exist, but complete management editors and external racing ingest are outside this finish pass.
- Field-level prose is shown in Preview but is not persisted as a separate human event per field.
- Actual Portainer rollout, persistence restart, backup restore rehearsal, and production monitoring remain deployment-operator work.

## Known architecture constraint

Each workday currently owns one shared `current_draft_revision_id`. Competing publication is safely rejected and stale drafts are preserved/detached, but independent simultaneous per-Manager draft branches are not represented. This is documented for a future architecture review rather than silently redesigned in a qualification pass.

## Release rule

A releasable `main` requires a clean local gate (with unavailable PostgreSQL/browser checks explicitly pending), a successful GitHub PostgreSQL/browser/image workflow, no committed credentials or private keys, and no force push. A green CI run does not itself prove a successful Portainer deployment.
