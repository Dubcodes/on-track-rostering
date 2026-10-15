# Clean staging reset and re-import runbook

This is a destructive operator runbook for a future, deliberate reset of the **private isolated staging** instance. It documents the process; it does not authorize or execute it. Its purpose is to prove the current migrations, bootstrap, neutral master import, one-time transition capture and qualification matrix from a clean installation.

## Hard safety boundary

Obtain explicit destructive authorization immediately before starting. Resolve and verify every target by its exact name:

- Portainer stack display name: `on-track-rostering`
- Compose project/resource prefix: `on-track-rostering-staging`
- expected containers: `on-track-rostering-staging_app-1`, `on-track-rostering-staging_db-1`, `on-track-rostering-staging_scheduler-1`
- services: `staging_app`, `staging_scheduler`, `staging_db`
- network: `ontrack_staging_internal`
- database volume: `ontrack_staging_postgres_data`
- application-data volume: `ontrack_staging_app_data`

Never touch production volumes `ontrack_postgres_data` or `ontrack_app_data`. Never touch any Re-Deputy database, container, network, volume, secret or file. Open the Portainer stack displayed as `on-track-rostering`, then stop if its Compose project, container, service, network or volume names differ from the staging-only names above. Do not infer targets from partial names, globs or environment variables.

## Select the qualified candidate

Resolve the candidate immediately before this destructive workflow. Record:

- `SELECTED_QUALIFIED_SHA=<exact commit chosen immediately before reset>`
- `SELECTED_RELEASE_GATE=<successful exact-head release-gate run>`
- `SELECTED_QUALIFIED_IMAGE=ghcr.io/dubcodes/on-track-rostering:sha-<SELECTED_QUALIFIED_SHA>`
- `SELECTED_DIGEST=sha256:<published digest>`
- `ALEMBIC_HEAD=<migration head from the selected source>`

Before any destructive step, verify all of the following:

1. `SELECTED_QUALIFIED_SHA` is still the intended candidate.
2. A successful exact-head release-gate exists whose `head_sha` is exactly `SELECTED_QUALIFIED_SHA`.
3. That gate successfully published the immutable `sha-<SELECTED_QUALIFIED_SHA>` GHCR image.
4. `SELECTED_DIGEST` records the digest published for that immutable image.
5. `STAGING_ONTRACK_IMAGE` is pinned to `SELECTED_QUALIFIED_IMAGE`.

Never use the mutable `:staging` tag for this destructive qualification cycle.

## Before reset

1. Obtain explicit authorization naming this staging-only destructive operation.
2. Record the currently running staging build from **Administration → Application status**. Do not rely on a former global footer.
3. Take a logical PostgreSQL backup of `staging_db`, validate its manifest, checksum it, and store it outside all On Track volumes.
4. Preserve or archive the contents of `ontrack_staging_app_data` before considering its deletion. This contains branding and Track-map files.
5. Record only useful non-secret counts and evidence: migrations, Regions, Tracks, Base Positions, Crew Groups, Vehicles, People, Workdays, publications and map/cache status. Do not record credentials, tokens, private payloads or staff details.
6. Verify again that the Portainer stack displayed as `on-track-rostering` contains only the intended `on-track-rostering-staging` Compose project resources, not production or Re-Deputy.
7. Reconfirm that `STAGING_ONTRACK_IMAGE` is pinned to the recorded `SELECTED_QUALIFIED_IMAGE`, never to `:staging`.
8. Confirm the staging-only database password, application secret, credential pepper, hostname, trusted-proxy configuration, WebAuthn identity and optional staging VAPID values remain available separately from the volumes being reset. Never copy production secrets.

## Reset phase

Perform these steps only after the preceding authorization and target verification:

1. Stop `staging_scheduler` and `staging_app` so no application writes continue.
2. Destroy and recreate only the intended `staging_db` database state in `ontrack_staging_postgres_data`.
3. For a genuinely clean installation, recreate only `ontrack_staging_app_data` if that separate destructive choice was explicitly authorized. Otherwise restore/preserve its contents as planned.
4. Leave production and every Re-Deputy resource untouched.
5. Redeploy the selected immutable staging image to `staging_app` and `staging_scheduler`.
6. Verify startup migration reaches sole Alembic head `e8b5d1f2a740` from a blank database.
7. Restart the application a second time and verify the repeated migration/startup is clean and idempotent.

## Bootstrap

1. Create the bootstrap staging Admin once using the supported CLI.
2. Verify login on the configured staging HTTPS origin.
3. Verify **Administration → Application status** reports the selected exact build SHA.
4. Verify `/health/live`, `/health/ready` and `/health/scheduler`; allow the scheduler enough time to complete a normal iteration.
5. Configure the intended staging-only branding and operational settings. Do not copy production secrets or credentials.

## Clean master and transition import

Keep actual input files outside Git. Do not put real employee names, emails or transition payloads in this repository.

1. Preview the neutral structural/master import.
2. Verify the intended canonical Regions, Tracks, Base Positions, Crew Groups and Vehicles. Confirm Base Position identity is global across Crew Groups.
3. Apply only after the complete structural Preview is understood.
4. Refresh and map external racing identities as needed; source evidence remains non-authoritative.
5. Preview the bounded one-time transition capture.
6. Inspect skipped Workdays, skipped assignments, unresolved Tracks, People, Position resolution, OPEN/TBC state and assignment counts.
7. Apply only after every Preview outcome is understood. Existing application authority, not the transition source, governs the result.

## Critical clean-import assertion

Immediately after the clean import, run **Position convergence Preview only**.

Expected result: **no new canonical duplicates created by the current import path**.

If Preview reports duplicates, stop the reset qualification. Do not apply convergence and do not normalize the duplicates away. Preserve the evidence and treat it as a defect in the current importer or canonical Position write path.

## Verify master data

- Cambridge Harness and Cambridge Synthetic remain separate exact Track identities.
- Track addresses, Region operational bases and configured Track travel defaults are correct.
- Vehicles have the correct owning Regions and truck classification; borrowed selection does not transfer ownership.
- Region-specific Position presets are correct.
- Trusted Track maps or deliberate manual overrides resolve to the exact Track.
- No accidental stored colour identity exists outside the palette-slot presentation system.

## Verify People and capability history

- No fake Deputy TBC People exist.
- Import created no Users automatically.
- Person UUIDs were generated by On Track; email remains metadata/login identifier rather than roster identity.
- Primary Region and Crew-group membership are correct.
- Raw capability/history facts are present as intended.
- One published Workday records raw history but does not create history-only eligibility; two distinct current-published scheduled Workdays for the canonical Position do. Two slots on one Workday still count once.

## Verify roster

For representative imported Workdays, verify assignment counts, OPEN, TBC, canonical Position names, individual assignment times, travel, automatic/manual timing, published immutable state and absence of source-event duplicates. Manual entry must remain available regardless of racing-source health.

## Accounts and setup

Use **Management → Crew** for metadata completion, the UUID worksheet where needed, setup links and account activation. Do not create Users during metadata import, and do not send automatic email. Copyable setup/reset links must use the configured trusted origin and fragment tokens.

## Final qualification

Execute the complete [private staging qualification matrix](STAGING_QUALIFICATION.md). Migration and import completion alone do not make the clean reset successful. Record direct, non-sensitive staging evidence separately from exact-head CI evidence, including role boundaries, immutable roster workflows, capability threshold behavior, notices, Team Hours, Builder/Vehicles, Help/UI, responsive surfaces, WebAuthn/TOTP/Web Push status, logs and persistence.

## Rollback

If the clean rehearsal exposes a blocking defect:

1. Stop only `staging_scheduler` and `staging_app`.
2. Preserve the failed staging state and non-secret evidence needed for diagnosis.
3. Re-verify the exact staging-only service and volume names.
4. Restore the pre-reset staging PostgreSQL logical backup to `staging_db` only.
5. Restore the preserved `ontrack_staging_app_data` contents if that volume was recreated.
6. Start `staging_app`, verify migration/readiness, then start `staging_scheduler` and verify its heartbeat.
7. Confirm the pre-reset staging build/data state. Production and Re-Deputy remain untouched throughout.

Do not commit raw Deputy captures, invitation links, credentials, PINs, tokens, staging secrets, production secrets, or real Person details solely for this rehearsal.
