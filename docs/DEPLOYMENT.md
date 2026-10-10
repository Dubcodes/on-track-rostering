# Deployment and recovery

This document applies only to the separate Docker/Portainer deployment server. Do not execute these Docker commands on the Windows development machine; local FastAPI, Alembic, tests, and tooling run directly under Windows/Python against PostgreSQL configured through `DATABASE_URL`.

## Portainer / Compose

Configure Portainer with the Git repository, branch `main`, and repository-relative Compose file. The repository supplies stack configuration only; GHCR supplies the application runtime image, Docker Hub supplies PostgreSQL, and persistent volumes supply application data. Portainer never builds On Track source.

Both the web app and scheduler run the same qualified image. The scheduler only changes the command to `python -m app.scheduler`; it is not a separate image. Do not commit Portainer credentials or production `.env` values, and do not mount or reference any Re-Deputy path or volume.

Set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `ONTRACK_SECRET_KEY`, `ONTRACK_CREDENTIAL_PEPPER`, `ONTRACK_COOKIE_SECURE=true`, `ONTRACK_ALLOWED_HOSTS`, `ONTRACK_TRUSTED_PROXY_CIDRS`, `ONTRACK_HOST_PORT`, and `ONTRACK_IMAGE` in production Portainer/environment configuration. `ONTRACK_IMAGE` is required and must be an immutable qualified image such as `ghcr.io/dubcodes/on-track-rostering:sha-<qualified-git-sha>`. The image embeds its source SHA in `/app/.ontrack-build-id`; runtime environment variables cannot override that identity.

The GHCR repository is `ghcr.io/dubcodes/on-track-rostering`. After its first CI publication, make the package public in the GitHub package settings so Portainer can pull it anonymously. Alternatively, keep it private and configure one read-only GHCR registry credential in Portainer. Never commit a PAT or GHCR credential to Compose or Git.

Production passkeys require `ONTRACK_WEBAUTHN_RP_ID` and the exact HTTPS `ONTRACK_WEBAUTHN_ORIGIN`; the user-facing RP name comes from persisted system branding. Changing RP identity can make existing credentials unusable, so validate it before enrolment. Set `ONTRACK_MFA_REQUIRED_ADMIN` and `ONTRACK_MFA_REQUIRED_MANAGER` according to deployment policy. Enabling either flag requires affected accounts to enrol a supported factor before relying on the role operationally.

Web Push is disabled until `ONTRACK_VAPID_PUBLIC_KEY` and `ONTRACK_VAPID_PRIVATE_KEY` contain real deployment values and `ONTRACK_VAPID_SUBJECT` identifies the deployment operator. Keep the private key in Portainer secrets/environment, never Git. The existing scheduler service automatically generates due two-day, night-before, and one-hour reminders plus periodic digests, then processes the notification outbox every five minutes. Missing VAPID keys produce a quiet disabled result. Generation and delivery are deterministic and events are claimed transactionally with expiring leases. `python -m app.cli deliver-notifications --limit 50` remains available for operator diagnostics or a deliberate manual run; no external notification cron is required.

Each scheduler iteration writes started/completed/failed state in independent database transactions. `/health/scheduler` returns success only for a recent completed iteration; Admin shows the same heartbeat and notification status. Use `python -m app.cli production-check` before promotion. It reports safe PASS/WARN/FAIL messages for deployment configuration, database reachability, build identity, and scheduler health without printing secret values.

The database has no host port. `db` readiness gates app startup; app startup runs Alembic before Gunicorn. Both services restart unless stopped. `TZ=Pacific/Auckland` controls operational display while stored timestamps are timezone-aware UTC.

Before an upgrade, take and verify a logical backup and review every new Alembic revision. Redeploy only a green `main`. The application runs forward migrations at startup; rolling application code back does not automatically downgrade the database. If application rollback is required, prefer code compatible with the migrated schema. Run an Alembic downgrade only as a deliberate maintenance action after validating its data impact and restore path.

After first startup:

```sh
docker compose exec app python -m app.cli create-admin
docker compose exec app python -m app.cli regions
docker compose exec app python -m app.cli production-check
```

## Backup and restore

Back up both PostgreSQL and `/app/data`; the Admin data export is not a disaster-recovery backup. Follow [BACKUP_RESTORE.md](BACKUP_RESTORE.md) for exact production backup commands, an isolated staging restore rehearsal, and the emergency production procedure.

## Private staging

The isolated Portainer stack named `on-track-rostering-staging` uses `compose.staging.yaml`. After a push to `main` passes its exact-head release gate, CI publishes `ghcr.io/dubcodes/on-track-rostering:sha-<commit>` and, only while that commit is still `origin/main`, updates `ghcr.io/dubcodes/on-track-rostering:staging`. The latter is a convenience tag for staging only; it is never a production promotion mechanism.

Routine staging update: in Portainer open the staging stack, choose **Update/Redeploy**, enable **Pull latest images**, and redeploy. `staging_app` and `staging_scheduler` both pull the same `:staging` image; no source build or scheduler-specific image is involved. A passing workflow does not redeploy or qualify the live stack by itself. Keep the staging stack, database, volume, network, credentials, and data separate from production.

Copy the required variable names from `.env.staging.example` into Portainer and replace their example values. `STAGING_ONTRACK_IMAGE` is optional because Compose defaults it to the qualified `:staging` image. Staging requires its own PostgreSQL credentials, application secret, credential pepper, HTTPS hostname/origin, WebAuthn RP ID, and trusted-proxy allowlist. Staging-specific Web Push may be configured through the three `STAGING_ONTRACK_VAPID_*` variables; leave both key values empty to keep Push deliberately disabled, and never reuse the production VAPID private key in staging. The staging Compose project uses distinct `staging_app`/`staging_db` services, `ontrack_staging_postgres_data` and `ontrack_staging_app_data` volumes, and `ontrack_staging_internal` network; neither service publishes a host port. The reverse proxy reaches `staging_app:8000` directly over that network, while the app healthcheck continues to use `127.0.0.1:8000` inside the container. Do not copy production data, secrets, VAPID keys, volumes, or credentials. Leave Web Push disabled unless deliberately qualifying staging-specific VAPID credentials.

`STAGING_ONTRACK_ALLOWED_HOSTS` and `STAGING_ONTRACK_TRUSTED_PROXY_CIDRS` use comma-separated values, including when only one value is configured. Trust only the narrow proxy network that can connect directly to the app. A trusted proxy must send one valid `X-Forwarded-For` and `X-Forwarded-Proto`; it may send one `X-Forwarded-Host`, otherwise the app uses the single public `Host` header preserved by the proxy. Duplicate, comma-joined, malformed, or port-conflicting forwarding values are rejected. Never expose the app directly through a network included in the proxy allowlist.

On first deployment, confirm the app startup migration reaches Alembic head, restart/redeploy once to prove the second upgrade is clean, then check `/health/live` and `/health/ready`. Create demo-only accounts with `python -m app.cli create-admin`; do not import staff data. Complete the documented role, invitation, branding, roster publish/republish, stale-write, decline, offline, authentication-throttle, and log-review smoke matrix before any production promotion request.

## Production promotion

Active staging tracking `:staging` is not a production release policy. Before production promotion, select the exact qualified SHA only after its automated gate and real staging matrix pass. Set `ONTRACK_IMAGE=ghcr.io/dubcodes/on-track-rostering:sha-<selected-sha>` in Portainer and redeploy with that immutable image. Do not use `:staging`, `:main`, or `:latest` in production, and do not assume a Git checkout update changes the production runtime. Changing application images does not affect `ontrack_postgres_data` or `ontrack_app_data`; the latter retains Track maps and no database data enters GHCR.
