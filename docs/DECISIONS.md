# Decisions

## 2026-09-08 — standalone authoritative service

On Track has no Deputy module, field, credential, database, volume, API, or deployment dependency. Re-Deputy was read only and used for behavioral study.

## PostgreSQL and explicit migrations

PostgreSQL 17 is the production database from the first migration. Alembic is the schema authority; application startup never calls `create_all()`. UUID application-generated keys keep identities stable and testable.

GitHub `main` may already be a Portainer deployment source, so committed migrations are append-only production artifacts. Do not rewrite an existing migration unless deployment history has first been established and the correction is explicitly safe. New schema changes receive a new revision, support blank-database upgrades, and preserve existing data. Downgrades are documented recovery tools, not an automatic production rollback strategy.

## Windows local development excludes Docker

FastAPI, Alembic, tests, and tooling run directly under Windows/Python. The required database engine is a native/local PostgreSQL installation or an explicitly configured remote development PostgreSQL instance supplied through `DATABASE_URL`. Docker and Docker Desktop are not run, started, repaired, installed, or configured on the development machine. Compose files remain production deployment artifacts for the separate Docker/Portainer server. Narrow SQLite unit tests never count as PostgreSQL integration qualification; unavailable PostgreSQL checks are reported as pending.

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

## Passkey and push boundaries

The schema stores WebAuthn public credentials and encrypted push-subscription payloads. Browser ceremonies and push delivery require a focused security pass and are not falsely presented as complete in this build.

## Open applications and direct decline publication

Applications bind to a published revision plus stable slot key so stale decisions are detectable. Selecting an applicant changes only a Manager draft; Publish remains the authority transition. Decline is deliberately different: after explicit confirmation, it locks the workday and creates a complete new immutable publication atomically according to the region policy. Any older Manager draft is detached rather than silently rebased.

## Notification event outbox

Roster publication and decline record deterministic event keys in the same database transaction as the authoritative change. This prevents duplicate event creation and isolates future optional push failures from roster integrity. Recipient expansion, preference enforcement, encryption, delivery, and retries remain a separate incomplete worker.
