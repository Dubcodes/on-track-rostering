# Deployment and recovery

This document applies only to the separate Docker/Portainer deployment server. Do not execute these Docker commands on the Windows development machine; local FastAPI, Alembic, tests, and tooling run directly under Windows/Python against PostgreSQL configured through `DATABASE_URL`.

## Portainer / Compose

Configure Portainer with the Git repository, branch `main`, and repository-relative `compose.yaml`. A green `main` is the production candidate: development → tests/CI → reviewed merge or push to `main` → Portainer pull/redeploy. Do not commit Portainer credentials or production `.env` values.

Set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `ONTRACK_SECRET_KEY`, `ONTRACK_CREDENTIAL_PEPPER`, `ONTRACK_COOKIE_SECURE=true`, `ONTRACK_ALLOWED_HOSTS`, `ONTRACK_TRUSTED_PROXY_CIDRS`, `ONTRACK_HOST_PORT`, and `ONTRACK_BUILD_ID` in Portainer/environment configuration. Use the deployed Git SHA or release identifier for `ONTRACK_BUILD_ID`. Do not mount or reference any Re-Deputy path or volume.

Production passkeys also require `ONTRACK_WEBAUTHN_RP_ID`, `ONTRACK_WEBAUTHN_RP_NAME`, and the exact HTTPS `ONTRACK_WEBAUTHN_ORIGIN`. Changing RP identity can make existing credentials unusable, so validate it before enrolment. Set `ONTRACK_MFA_REQUIRED_ADMIN` and `ONTRACK_MFA_REQUIRED_MANAGER` according to deployment policy. Enabling either flag requires affected accounts to enrol a supported factor before relying on the role operationally.

Web Push is disabled until all of `ONTRACK_VAPID_PUBLIC_KEY`, `ONTRACK_VAPID_PRIVATE_KEY`, and `ONTRACK_VAPID_SUBJECT` contain real deployment values. Keep the private key in Portainer secrets/environment, never Git. Run `python -m app.cli deliver-notifications --limit 50` as an independent recurring server-side job. Multiple invocations are safe because delivery rows are unique and claimed transactionally; monitor retries and persistent failures. The worker is not a substitute for a durable external scheduler, and scheduled reminder-event production is not implemented.

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
