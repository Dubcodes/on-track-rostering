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

Never rehearse against production or the existing acceptance stack. Provision a separate restore-only project, hostname, database volume, app-data volume, credentials and network under a separately approved change. Its project and resource names must be deliberately distinct from both production and the observed `on-track-rostering` project. Before the rehearsal, back up the existing staging system itself.

1. Record the restore-only project's exact labels, full container IDs, image digest, empty volume mounts, private network and lack of public database/application ports. Stop if any existing On Track volume or shared database is attached.
2. Keep the restore-only app and scheduler stopped. Verify the selected dump checksum and `pg_restore --list` result again.
3. Recreate only the disposable database named by the restore-only database container, then restore the custom-format dump with `--no-owner --no-privileges`. This is destructive; an operator must substitute the reviewed full restore-only container ID rather than a guessed Compose project or service name.
4. Restore the app-data tar only into the reviewed empty restore-only `/app/data` volume, preserving numeric ownership and modes. Never copy over the existing staging app-data volume.
5. Start the restore-only app; its normal startup must bring Alembic to the selected head. Start the restore-only scheduler only if scheduler recovery is part of the approved rehearsal.
6. Confirm `/health/live`, `/health/ready`, `/health/scheduler` where applicable and `python -m app.cli production-check`; then verify the Admin readiness panel, one known published roster and immutable revision history, master-data counts, accounts, notification status, branding and representative Track maps.
7. Record the result without credentials, tokens, MFA material or private staff data. Remove the disposable restore project only under a separate cleanup approval after evidence and any required diagnostics are retained.

## Staging pre-upgrade backup

Use this shorter maintenance procedure before a staging change. It creates the same two-part recovery set without resetting or restoring anything. The 28 October 2026 observation found Portainer display name and Compose project label `on-track-rostering`, container names with an `on-track-rostering-staging_*` prefix, services `staging_app`, `staging_db` and `staging_scheduler`, volumes `ontrack_staging_postgres_data` and `ontrack_staging_app_data`, and network `ontrack_staging_internal`. The project label differs from the repository's declared project name. Treat every one of those facts as evidence to re-verify, not as an instruction to guess a Compose project or container name.

First use Portainer or read-only Docker inspection to identify exactly one container for each observed service label. Record each container name, full ID, image reference/ID/digest, project label, service label, mounts, networks and published ports. Stop if any label has zero or several matches, the app and scheduler images differ, a production/shared mount appears, or the observed resources do not match the intended isolated stack. Do not render the environment and do not copy configuration values into evidence.

The following discovery commands are read-only and deliberately do not invoke Compose. Review their output before assigning the exact IDs to the maintenance variables:

```sh
docker ps --filter label=com.docker.compose.project=on-track-rostering --format '{{.ID}} {{.Names}} {{.Image}} {{.Label "com.docker.compose.service"}}'
docker inspect --format '{{.Id}} name={{.Name}} project={{index .Config.Labels "com.docker.compose.project"}} service={{index .Config.Labels "com.docker.compose.service"}} image={{.Config.Image}} image_id={{.Image}} mounts={{range .Mounts}}{{.Name}}:{{.Destination}}:{{.Mode}} {{end}} ports={{json .NetworkSettings.Ports}} networks={{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' VERIFIED_CONTAINER_ID
```

Only after that review, set `APP_CONTAINER`, `DB_CONTAINER` and `SCHEDULER_CONTAINER` to the three verified full IDs and set `BACKUP_DIR` to an operator-verified absolute path with sufficient space. The example values below are deliberately invalid and must be replaced. Confirm the database version/Alembic head and application build before stopping writes; do not expose credentials while doing so.

```sh
BACKUP_DIR=/replace-with-verified-absolute-backup-path/YYYYMMDD-HHMM
APP_CONTAINER=replace-with-verified-full-app-container-id
DB_CONTAINER=replace-with-verified-full-db-container-id
SCHEDULER_CONTAINER=replace-with-verified-full-scheduler-container-id
case "$BACKUP_DIR" in
  /replace-with-verified-absolute-backup-path/*) echo "Replace BACKUP_DIR before continuing" >&2; exit 1 ;;
  /*) ;;
  *) echo "BACKUP_DIR must be absolute" >&2; exit 1 ;;
esac
mkdir -p "$BACKUP_DIR"

docker inspect "$APP_CONTAINER" "$DB_CONTAINER" "$SCHEDULER_CONTAINER" >/dev/null
docker exec "$APP_CONTAINER" sh -c 'cat /app/.ontrack-build-id'
docker exec "$DB_CONTAINER" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version;"'
docker exec "$DB_CONTAINER" sh -c 'postgres --version'

docker stop "$SCHEDULER_CONTAINER" "$APP_CONTAINER"

docker exec "$DB_CONTAINER" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$BACKUP_DIR/ontrack-staging.dump"
docker exec -i "$DB_CONTAINER" sh -c 'pg_restore --list -' < "$BACKUP_DIR/ontrack-staging.dump" > "$BACKUP_DIR/ontrack-staging.dump.list"

APP_IMAGE_ID="$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")"
docker run --rm --network none --volumes-from "$APP_CONTAINER":ro --entrypoint tar "$APP_IMAGE_ID" --numeric-owner -C /app/data -cf - . > "$BACKUP_DIR/ontrack-staging-app-data.tar"
tar -tf "$BACKUP_DIR/ontrack-staging-app-data.tar" > "$BACKUP_DIR/ontrack-staging-app-data.list"

sha256sum "$BACKUP_DIR/ontrack-staging.dump" "$BACKUP_DIR/ontrack-staging-app-data.tar" > "$BACKUP_DIR/SHA256SUMS"
wc -c "$BACKUP_DIR/ontrack-staging.dump" "$BACKUP_DIR/ontrack-staging-app-data.tar"
cat "$BACKUP_DIR/SHA256SUMS"
```

Before proceeding, verify that both archive listings are readable and plausible, both files are non-zero, the checksums were recorded, file ownership/mode information is retained by the numeric-owner tar archive, access to the backup directory is restricted, and a second encrypted copy exists independently of the deployment host and its On Track volumes. Record the current build SHA, running image IDs/digests, PostgreSQL version, Alembic version, byte sizes and creation time alongside the backup without recording secrets. This validates archive structure, not a restore; schedule an isolated two-part restore rehearsal and verify a known published roster plus immutable revision history.

If backup creation or verification fails, do not redeploy. Restart the unchanged, exact verified containers with `docker start "$APP_CONTAINER" "$SCHEDULER_CONTAINER"`, confirm all three health/readiness checks, and investigate the backup failure. If the backup succeeds, keep the services stopped only for a separately authorized maintenance action; otherwise restart them promptly. A later update must not delete either staging volume or recreate the bootstrap Admin.

## Emergency production restore

Declare a maintenance window and preserve the failed state before changing it. Confirm the exact production Compose project, `db`, `app`, and `scheduler` services and both named volumes. Stop `scheduler` and `app` to prevent writes, take a best-effort current backup, verify the selected backup checksum and manifest, and obtain explicit incident authority for the restore.

Restore PostgreSQL and `/app/data` with the same commands as the rehearsal, using `compose.yaml` and production service names. The database recreation is destructive and must target only the confirmed production database. Start `app` first and confirm migration to Alembic head and `/health/ready`; then start `scheduler` and confirm `/health/scheduler`. Run `production-check` and validate Admin readiness, known rosters/history, master data, accounts, Track maps, and scheduler/notification state before ending maintenance.

Do not run an Alembic downgrade as an automatic rollback. Prefer application code compatible with the restored/migrated schema, and perform any downgrade only as a separately reviewed data-changing procedure.
