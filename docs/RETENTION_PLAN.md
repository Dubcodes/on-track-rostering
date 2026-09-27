# Operational retention plan

`ONTRACK_RETENTION_DAYS` controls the terminal-record cutoff and defaults to 365. `python -m app.cli housekeeping --dry-run` reports eligible rows without writing; `--apply` is the explicit deletion mode.

Housekeeping removes only unequivocally terminal operational rows: expired WebAuthn challenges, old throttle rows, expired or long-revoked trusted devices, expired invitations, and old processed notification events whose deliveries are all terminal. It never deletes users, people, Workdays, revisions, assignments, audit events, or human-change history.

Administration → Backup & recovery shows the effective cutoff and offers a manual ZIP of safe business/history CSVs. The export excludes authentication secrets and raw provider payloads and is not a PostgreSQL recovery backup. Monthly automated CSV export remains deferred; verified `pg_dump`/restore remains authoritative disaster recovery.
