# Deployment and recovery

This document applies only to the separate Docker/Portainer deployment server. Do not execute these Docker commands on the Windows development machine; local FastAPI, Alembic, tests, and tooling run directly under Windows/Python against PostgreSQL configured through `DATABASE_URL`.

## Portainer / Compose

Configure Portainer with the Git repository, branch `main`, and repository-relative `compose.yaml`. A green `main` is the production candidate: development → tests/CI → reviewed merge or push to `main` → Portainer pull/redeploy. Do not commit Portainer credentials or production `.env` values.

Set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `ONTRACK_SECRET_KEY`, `ONTRACK_CREDENTIAL_PEPPER`, `ONTRACK_COOKIE_SECURE=true`, `ONTRACK_ALLOWED_HOSTS`, `ONTRACK_TRUSTED_PROXY_CIDRS`, `ONTRACK_HOST_PORT`, and `ONTRACK_BUILD_ID` in Portainer/environment configuration. Use the deployed Git SHA or release identifier for `ONTRACK_BUILD_ID`. Do not mount or reference any Re-Deputy path or volume.

Production passkeys require `ONTRACK_WEBAUTHN_RP_ID` and the exact HTTPS `ONTRACK_WEBAUTHN_ORIGIN`; the user-facing RP name comes from persisted system branding. Changing RP identity can make existing credentials unusable, so validate it before enrolment. Set `ONTRACK_MFA_REQUIRED_ADMIN` and `ONTRACK_MFA_REQUIRED_MANAGER` according to deployment policy. Enabling either flag requires affected accounts to enrol a supported factor before relying on the role operationally.

Web Push is disabled until all of `ONTRACK_VAPID_PUBLIC_KEY`, `ONTRACK_VAPID_PRIVATE_KEY`, and `ONTRACK_VAPID_SUBJECT` contain real deployment values. Keep the private key in Portainer secrets/environment, never Git. Run `python -m app.cli deliver-notifications --limit 50` as an independent recurring server-side job. Each invocation generates due two-day, night-before, and one-hour reminder events before delivery. Multiple invocations are safe because events/deliveries are deterministic and events are claimed transactionally with expiring leases; monitor retries and persistent failures. The worker must be invoked by a durable external scheduler.

The database has no host port. `db` readiness gates app startup; app startup runs Alembic before Gunicorn. Both services restart unless stopped. `TZ=Pacific/Auckland` controls operational display while stored timestamps are timezone-aware UTC.

Before an upgrade, take and verify a logical backup and review every new Alembic revision. Redeploy only a green `main`. The application runs forward migrations at startup; rolling application code back does not automatically downgrade the database. If application rollback is required, prefer code compatible with the migrated schema. Run an Alembic downgrade only as a deliberate maintenance action after validating its data impact and restore path.

After first startup:

```sh
docker compose exec app python -m app.cli create-admin
docker compose exec app python -m app.cli regions
```

## PostgreSQL backup

Use a deployment-owned backup location outside the public app and database volume. A supported logical backup is:

```sh
pg_dump --format=custom --dbname="$DATABASE_URL" --file="ontrack-YYYYMMDD-HHMM.dump"
pg_restore --list "ontrack-YYYYMMDD-HHMM.dump"
```

Record size, SHA-256, PostgreSQL version, creation time, and application revision. Periodically restore into a disposable PostgreSQL database, run migrations, query seed/master/history counts, and open a known published day. A backup is not considered verified merely because `pg_dump` exited zero.

Restore is an offline operator action: stop app writes, create a fresh target database, run `pg_restore --clean --if-exists` only against that explicitly verified disposable/new target, migrate, validate, then switch connection configuration. Never test restore against production.

## Private staging

The isolated Portainer stack named `on-track-rostering-staging` uses `compose.staging.yaml`. During active development it may deliberately track GitHub `main`: change → local checks → normal push → exact-head GitHub gate → manual staging redeploy → real staging test. A passing workflow does not redeploy or qualify the live stack by itself. Keep the staging stack, database, volume, network, credentials, and data separate from production.

Copy the variable names from `.env.staging.example` into Portainer and replace every example value. Staging requires its own PostgreSQL credentials, application secret, credential pepper, HTTPS hostname/origin, WebAuthn RP ID, and trusted-proxy allowlist. The staging Compose project uses distinct `staging_app`/`staging_db` services, `ontrack_staging_postgres_data` volume, and `ontrack_staging_internal` network; neither service publishes a host port. The reverse-proxy container reaches `staging_app:8000` directly over `ontrack_staging_internal`, while the app healthcheck continues to use `127.0.0.1:8000` inside the app container. Do not copy production data, secrets, VAPID keys, volumes, or credentials. Leave Web Push disabled unless deliberately qualifying staging-specific VAPID credentials.

Set `STAGING_ONTRACK_BUILD_ID` to the exact deployed Git SHA. Terminate TLS at the private staging reverse proxy and retain `ONTRACK_COOKIE_SECURE=true`. The proxy hostname must equal `STAGING_ONTRACK_ALLOWED_HOSTS`; passkey testing additionally requires the exact HTTPS origin and matching staging RP ID.

`STAGING_ONTRACK_ALLOWED_HOSTS` and `STAGING_ONTRACK_TRUSTED_PROXY_CIDRS` use comma-separated values, including when only one value is configured. Trust only the narrow proxy network that can connect directly to the app. A trusted proxy must send one valid `X-Forwarded-For` and `X-Forwarded-Proto`; it may send one `X-Forwarded-Host`, otherwise the app uses the single public `Host` header preserved by the proxy. Duplicate, comma-joined, malformed, or port-conflicting forwarding values are rejected. Never expose the app directly through a network included in the proxy allowlist.

On first deployment, confirm the app startup migration reaches Alembic head, restart/redeploy once to prove the second upgrade is clean, then check `/health/live` and `/health/ready`. Create demo-only accounts with `python -m app.cli create-admin`; do not import staff data. Complete the documented role, invitation, branding, roster publish/republish, stale-write, decline, offline, authentication-throttle, and log-review smoke matrix before any production promotion request.

## Promotion recommendation

Active staging tracking `main` is not a production release policy. Before production promotion, freeze a specific exact SHA only after its automated gate and real staging matrix pass, then create an immutable release tag and/or deploy an immutable image tagged by that SHA. The long-term model is build once, test that artifact, then deploy the same artifact. Do not promote merely because `main` moved, and never reuse or move a release tag.
