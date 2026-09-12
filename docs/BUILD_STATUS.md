# Build status — 2026-09-12

## Current result

The Foundation production-readiness hardening pass is complete. Implementation commit `8f988dc00c9c10299476b526ecfc597d843030c3`, browser-gate fixes `eae21f1e0b375e38421342cd960e791d754ae939` and `510266c090e3bb392c03a6b47c6a882be0eb451b`, and isolated staging-configuration commit `aefa1a86a3b1395aaa43fc5dc6d68741a1fc85cf` preserve the settled roster architecture while closing the reviewed security, data-integrity, notification, offline, settings, privacy, calendar, and builder-performance issues. Exact-head GitHub release-gate run `34663670073` passed PostgreSQL 17 migrations, 71 PostgreSQL-backed tests with no skips, six Playwright cases, dependency audit, production and staging Compose validation, and production image build. Local PostgreSQL execution remains pending because no disposable `ONTRACK_TEST_DATABASE_URL` or `DATABASE_URL` is configured. Docker was not invoked locally. No production or staging deployment is claimed by this build evidence.

## Implemented

- Standalone FastAPI/Jinja/SQLAlchemy application, append-only Alembic chain, PostgreSQL 17 production target, retained production Compose stack, and no Re-Deputy runtime dependency.
- Users and separately rosterable People, scoped capability roles, immutable published Workday snapshots, one optimistic-versioned shared Manager draft, PostgreSQL row locks, stale-mutation conflicts, structured Preview, and atomic publication.
- Invitation secrets now use `/invite#token=...`, are removed from browser history, reach a fixed activation endpoint only in the POST body, remain hash-only in storage, and are revealed once without redirect/query leakage.
- Central self-decline policy permits linked Employee/Contractor self-service only before the effective Pacific/Auckland assignment start; Viewer/Manager-only, started, unknown-start-today, historical, and stale-publication mutations are denied.
- Viewer write actions are separated from management-detail reads, while directly assigned event-only/Contractor users receive no global Human Change history.
- Reminder delivery revalidates the current publication, active user link, and current assignment; superseded, removed, or more-than-15-minutes-late reminders terminate without delivery. PostgreSQL conflict-safe reminder insertion and one-at-a-time skip-locked notification claims preserve multi-worker safety without holding transactions during Web Push.
- Per-user offline storage renders a specific cached personal Day with category, race timings, Day note, own assignments/times/notes, and actual device `cached_at`; it exposes no forms, unrelated crew, management data, or global history.
- Settings reauthentication and current-credential verification reuse isolated account/IP throttling with success reset.
- Builder eligibility hints bulk-load capability signals once and preserve Manager-block, Employee-opt-out, explicit allow, worked-history, then unknown precedence; manual Manager assignment remains available.
- Persisted singleton `system_settings.public_signup_enabled` is Admin-only, CSRF-protected, audited, defaults false, and takes effect on the next request. The environment toggle was removed.
- `system_branding` remains exclusively product identity; no competing operational-settings source was introduced. WebAuthn RP ID/origin remain deployment security identities, while the stale RP display-name environment option was removed.
- Month weekly totals cover every displayed grid day, empty regional holiday cells use the linked Person's home-region geography, and non-Race-Day builder/Day wording no longer displays race-only fields or warnings.
- Conservative retention work is documented in `docs/RETENTION_PLAN.md`; no automatic or broad data deletion was introduced.
- `compose.staging.yaml` and `.env.staging.example` define a separately named private stack, PostgreSQL database/volume/network, host port, secrets namespace, HTTPS origin, and WebAuthn RP identity. CI validates this configuration, but no staging target or credentials are assumed.

## Qualification evidence

- Ruff and Python compile: passed for application, migrations, scripts, and tests.
- Local deterministic pytest: **60 passed**. Local PostgreSQL suite: **11 skipped/pending** solely because neither PostgreSQL test URL is configured; SQLite was not presented as PostgreSQL qualification.
- Local Playwright: **6 passed**—responsive coverage at 1280, 430, 375, and 320 pixels plus physically offline personal-Day rendering at 1280 and 430 against a disposable narrow browser fixture.
- JavaScript syntax: passed for application, invitation, passkey, notification, and service-worker scripts.
- Alembic PostgreSQL-dialect SQL generation through `e72a19c5f40b`: passed. Local `pip check`, `pip-audit --local`, `git diff --check`, and secret/stale-config scans passed.
- Exact-head GitHub Actions for `aefa1a86a3b1395aaa43fc5dc6d68741a1fc85cf`: release-gate run `34663670073` passed. PostgreSQL 17 applied the full migration chain through `e72a19c5f40b`, and the second `alembic upgrade head` completed cleanly. The full PostgreSQL-backed suite reported **71 passed, 0 skipped**, including simultaneous first-draft creation, stale detail/assignment mutation, stale editor after Publish, exact `lock_version` conflict behavior, concurrent reminder generation, and concurrent notification claiming.
- The same exact-head run reported **6 Playwright tests passed**, including responsive widths 1280/430/375/320 and real offline Day cases at 1280/430. `pip check`, `pip-audit --local`, JavaScript syntax, production Compose validation, isolated staging Compose validation, and production image build all passed.

## Explicitly pending or deferred

- Private staging deployment and smoke/log qualification are pending because no separate Portainer stack, host/domain, or staging credentials were discoverable. The minimum isolated Compose/environment configuration is prepared; production deployment is not authorized.
- Local real-PostgreSQL execution is pending; no native/local or explicitly configured remote instance was available. The application was not switched to SQLite.
- Real passkey hardware/browser ceremony, real Web Push provider delivery, and recovery codes remain pending. Production reminder delivery depends on staging/production VAPID and a durable scheduler.
- Operations/Travel, Vehicles, Accommodation, racing-provider ingest, explicit reschedule/abandonment workflow, management-only crew notes, authorized credential reset, data export/retention administration UI, and configurable logo remain deferred.
- Production backup/restore rehearsal and production monitoring remain deployment-operator work.

## Known architecture constraint

Each Workday owns one shared `current_draft_revision_id`; independent per-Manager draft branches are not represented. Simultaneous first-edit creation resolves through the locked Workday row, and every shared-draft mutation rejects a stale `lock_version`, so this deliberate constraint does not permit silent last-writer overwrites. Stale drafts detached by an authoritative decline remain preserved for recovery.

## Release rule

A releasable revision requires a clean local gate (with unavailable PostgreSQL checks explicitly pending), a successful exact-head GitHub PostgreSQL/browser/Compose/image workflow, no committed credentials or private keys, and no force push. A green CI run does not itself prove a successful Portainer deployment or private-staging qualification.
