# Operational retention plan

This hardening pass deliberately does not add automatic deletion. Retention needs operator-approved periods, a dry-run report, bounded batches, and production backup/restore rehearsal before it is safe to enable.

A future `python -m app.cli housekeeping --dry-run` command may remove only unequivocally terminal operational rows: expired or consumed WebAuthn challenges, throttle rows after their block/window has elapsed, expired or revoked trusted devices, consumed/revoked/expired invitations after an agreed support period, and old processed notification events with terminal deliveries. It must never delete users, people, Workdays, revisions, assignments, audit events, or human-change history.

The command should report counts by table, require an explicit non-dry-run flag, delete in bounded transactions, and preserve notification rows that are pending, retryable, processing, or referenced by non-terminal deliveries. Retention periods remain an operations-policy decision and are not silently inferred here.
