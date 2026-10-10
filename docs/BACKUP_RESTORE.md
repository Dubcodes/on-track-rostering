# Backup and restore runbook

This runbook is for the separate Docker/Portainer host only. Never run these Docker commands on the Windows development machine. A complete On Track backup has two independently stored parts: a PostgreSQL custom-format dump and the application-data volume (`/app/data`, which contains cached and overridden Track map files). The Admin **Download data** export is a convenience export, not a database or disaster-recovery backup.

Use a deployment-owned directory outside the Compose project and all On Track volumes. Substitute an actual timestamp for `YYYYMMDD-HHMM`; do not paste passwords into commands or logs.

## Production backup

Run from the directory containing `compose.yaml`:

```sh
mkdir -p /srv/backups/ontrack/YYYYMMDD-HHMM/app-data
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > /srv/backups/ontrack/YYYYMMDD-HHMM/ontrack.dump
docker compose exec -T db sh -c 'pg_restore --list -' < /srv/backups/ontrack/YYYYMMDD-HHMM/ontrack.dump
docker compose cp app:/app/data/. /srv/backups/ontrack/YYYYMMDD-HHMM/app-data/
sha256sum /srv/backups/ontrack/YYYYMMDD-HHMM/ontrack.dump > /srv/backups/ontrack/YYYYMMDD-HHMM/SHA256SUMS
```

Record the deployed image SHA, PostgreSQL version, creation time, dump size, checksum, and result of `pg_restore --list`. Store a second encrypted copy independently from the server and its On Track volumes. A zero exit from `pg_dump` alone is not restore verification.

## Isolated staging rehearsal

Never rehearse against production. Use the isolated staging project, database, app-data volume, credentials, and hostname. Before the rehearsal, back up staging itself. With `compose.staging.yaml`, replace the production service names above with `staging_db` and `staging_app`.

1. Confirm the active Compose project and target services with `docker compose -f compose.staging.yaml ps`.
2. Stop application writes with `docker compose -f compose.staging.yaml stop staging_scheduler staging_app`.
3. Restore the dump only to the explicitly verified staging database:

   ```sh
   docker compose -f compose.staging.yaml exec -T staging_db sh -c 'dropdb --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   docker compose -f compose.staging.yaml exec -T staging_db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' < /srv/backups/ontrack/YYYYMMDD-HHMM/ontrack.dump
   ```

4. Replace staging `/app/data` only after separately preserving its current contents:

   ```sh
   docker compose -f compose.staging.yaml cp /srv/backups/ontrack/YYYYMMDD-HHMM/app-data/. staging_app:/app/data/
   ```

5. Start `staging_app`; its normal startup must bring Alembic to head. Then start `staging_scheduler`.
6. Confirm `/health/live`, `/health/ready`, and `/health/scheduler`; run `docker compose -f compose.staging.yaml exec -T staging_app python -m app.cli production-check`; then verify the Admin readiness panel, one known published roster and history, master-data counts, accounts, notification status, and representative Track maps.
7. Record the result without credentials, tokens, MFA material, or private staff data.

## Emergency production restore

Declare a maintenance window and preserve the failed state before changing it. Confirm the exact production Compose project, `db`, `app`, and `scheduler` services and both named volumes. Stop `scheduler` and `app` to prevent writes, take a best-effort current backup, verify the selected backup checksum and manifest, and obtain explicit incident authority for the restore.

Restore PostgreSQL and `/app/data` with the same commands as the rehearsal, using `compose.yaml` and production service names. The database recreation is destructive and must target only the confirmed production database. Start `app` first and confirm migration to Alembic head and `/health/ready`; then start `scheduler` and confirm `/health/scheduler`. Run `production-check` and validate Admin readiness, known rosters/history, master data, accounts, Track maps, and scheduler/notification state before ending maintenance.

Do not run an Alembic downgrade as an automatic rollback. Prefer application code compatible with the restored/migrated schema, and perform any downgrade only as a separately reviewed data-changing procedure.
