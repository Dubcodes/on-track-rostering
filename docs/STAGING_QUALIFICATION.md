# Real private staging qualification

Deployment under test: `https://ontrackrostering.dubcodesmedia.com`

This is an operator record for the real isolated staging system. Automated tests show implementation behavior; they do not replace interaction with the deployed application. Never enter credentials, raw invitation tokens, session values, MFA secrets, passkey challenges, push keys, or private staff data into this document.

For the separately authorized destructive blank-database rehearsal, complete [STAGING_RESET_REIMPORT.md](STAGING_RESET_REIMPORT.md) first, then execute this matrix against the resulting isolated staging state.

## Status rules

- **PASS** — observed against the real deployment, with non-sensitive evidence recorded.
- **FAIL** — observed incorrect behavior; record reproduction, time, deployed SHA, and issue reference.
- **PENDING** — not yet exercised against the real deployment, or evidence is incomplete.
- **NOT IN CURRENT SCOPE** — deliberately deferred and not a blocker for active staging development.

When executing a row, record date/time, the build SHA shown in Administration, role/browser/device, outcome, and a redacted screenshot or log reference where useful. Change a status only from direct staging evidence.

## Known deployment evidence

| Check | Status | Evidence |
|---|---|---|
| HTTPS staging is reachable | PASS | Direct public GET observed 27 October 2026, rechecked 28 October and again 6 November 2026. Cloudflare served the HTTPS application and login surface. |
| Public hostname is `ontrackrostering.dubcodesmedia.com` | PASS | Directly observed at the configured public URL on 27 October 2026. |
| Current deployed build identity | PASS | The 27 October public observation of `c5b2e4a…` is historical. On 28 October 2026 the operator confirmed both application and scheduler were using qualified image `5dd5b77a52552ab1c5265302d8ebf81539cf5079`; the public login reported that full build again on 6 November 2026. Re-observe the exact container image ID/digest at the start of the acceptance cycle. |
| Qualification of the currently deployed build | PASS | Exact-head release-gate `40425690832` for `5dd5b77a52552ab1c5265302d8ebf81539cf5079` passed 406 PostgreSQL-backed tests, 82 Playwright tests and the dependency/security, Compose and image checks; published digest `sha256:e285b88d21226eda5374df04e3a57e479c890b3e7f5eac924015bf51773eebcf`. This proves image qualification, not live workflow acceptance. |
| Public security-header smoke | PASS | Login/health responses returned HSTS (`max-age=31536000; includeSubDomains`), CSP, `X-Content-Type-Options`, same-origin Referrer Policy and restrictive Permissions Policy through Cloudflare on 27 October 2026 and again on 28 October. Direct-origin denial and proxy-network correctness remain unobserved. |
| Public authentication entry points | PASS | PIN-first and separate password pages rendered. Passkey entry was offered. Public signup returned the configured closed-request message. No credential, throttle, session or account mutation was attempted. |
| GitHub `main` branch protection | FAIL | Read-only GitHub API reported `main` unprotected on 27 October 2026 and again on 6 November 2026. This blocks a production-readiness claim until required release checks, force-push/deletion restrictions and review policy are configured and re-observed. |
| Historical footer visibly reported `ee8af0d5dcd215c6df02f113ef8c8bf43ee936f2` | PASS | Historical user observation from before the global footer was removed; it is not evidence for the current candidate. |
| PostgreSQL initialized | PASS | Real staging database initialization completed. |
| Bootstrap Admin creation | PASS | `python -m app.cli create-admin` completed once in staging. Do not recreate it. |
| Admin password/PIN login through Cloudflare Tunnel | PASS | User successfully authenticated after `ee8af0d`. |
| Authenticated session persists across navigation | PASS | User navigated authenticated pages without losing the session. |
| Month page renders | PASS | `/month` rendered in the real deployment. |
| Settings page renders | PASS | `/settings` rendered in the real deployment. |
| Exact-head gate for deployed `ee8af0d` | PASS | GitHub run `34715787713`: PostgreSQL 17.6, 82 tests, 6 Playwright tests, audits, Compose, and image build passed. |

No other row is pre-qualified.

## Qualification-cycle candidate record

At the start of each staging qualification cycle, select a qualified candidate deliberately and record:

- `SELECTED_SHA` — the exact commit chosen for this cycle;
- `SELECTED_RELEASE_GATE` — a successful exact-head GitHub release-gate run whose `head_sha` is `SELECTED_SHA`;
- `SELECTED_IMAGE` — the immutable `ghcr.io/dubcodes/on-track-rostering:sha-<SELECTED_SHA>` image;
- `SELECTED_DIGEST` — the digest published for `SELECTED_IMAGE`; and
- `ALEMBIC_HEAD` — the migration head expected from that selected source.

All candidate-specific checks below refer to that selected qualified SHA. The deployment/readiness facts explicitly recorded as PASS were observed on 28 October 2026; every workflow row remains **PENDING** until separately exercised against the real deployment.

**Selected candidate for the next qualification cycle:** source SHA `5dd5b77a52552ab1c5265302d8ebf81539cf5079`, exact-head release-gate `40425690832`, immutable image `ghcr.io/dubcodes/on-track-rostering:sha-5dd5b77a52552ab1c5265302d8ebf81539cf5079`, digest `sha256:e285b88d21226eda5374df04e3a57e479c890b3e7f5eac924015bf51773eebcf`, and Alembic head `b4e9d2a7f130`. The gate passed **406 PostgreSQL-backed tests**, **82 Playwright tests**, dependency/security audit, both Compose contracts and embedded-build verification.

The local cross-region oversight worktree is a later uncommitted release candidate, not the selected staging candidate. It introduces additive index-only revision `c5a7e2d9f410`; do not expect that head in staging or deploy it until an exact GitHub SHA has passed the full PostgreSQL and Playwright gate and is separately selected.

On 6 November 2026 an anonymous read-only GHCR manifest request reconfirmed that the immutable selected tag resolves with HTTP 200 to `sha256:e285b88d21226eda5374df04e3a57e479c890b3e7f5eac924015bf51773eebcf` as a Docker v2 manifest. GitHub `origin/main` and release-gate `40425690832` still both resolved to exact SHA `5dd5b77a52552ab1c5265302d8ebf81539cf5079`; no newer source was substituted.

Direct public observation on 27 October 2026 showed staging still running `c5b2e4a…`; that observation was superseded by the 28 October operator checks. Those checks confirmed application and scheduler on `5dd5b77…`, PostgreSQL at `b4e9d2a7f130`, and application, database and scheduler readiness passing. The newer workflows are therefore present but **not live-accepted**. Re-resolve the image, migration and readiness facts at the beginning of the next acceptance cycle; do not redeploy merely to repeat the completed upgrade.

### Migration delta for the selected candidate

Source inspection establishes one schema revision between the deployed source line and the selected candidate: `a3d8c1f4e720 → b4e9d2a7f130`. It creates `operational_places`, `equipment_setups`, `equipment_setup_items` and `workday_equipment_items`, including Region/revision foreign keys, lifecycle and quantity checks, stable item uniqueness and case-insensitive Region/name indexes. It also adds `workday_revisions.default_hotel_address_snapshot` as non-null with an empty-string server default so existing rows remain valid. There is no data backfill, existing-row rewrite, table/column removal or new staging environment variable, and the staging Compose/configuration contract is unchanged. PostgreSQL transactional DDL and Alembic version tracking make a completed revision repeat-safe, but the operator must still confirm the live starting version and startup result; no automatic downgrade is approved.

## Qualification execution order

Use the detailed rows below as the evidence record. Execute them in this order so prerequisites are established once and failures do not contaminate later evidence.

1. **Read-only preflight:** record current build, app/scheduler image IDs and digests, Compose project, services, volumes, network, tunnel target, health, migration head, readiness panel, `production-check`, scheduler heartbeat and redacted logs. Stop if app and scheduler differ, production resources are attached, or backups cannot be made.
2. **Backup qualification:** create and validate the PostgreSQL plus `/app/data` recovery set under a separately approved maintenance window. Do not combine this with a reset, restore or import.
3. **Baseline smoke:** re-prove exact build `5dd5b77…`, migration `b4e9d2a7f130`, app/database/scheduler health, retained Admin access, retained Track-map data, clean logs and matching app/scheduler images without redeploying.
4. **Disposable identity/security:** create dedicated demo identities, then exercise login modes, invitation/reset, devices, fresh authentication, TOTP/recovery, optional passkey and final-Admin safeguards. Perform privacy/authorization checks with independent browser profiles.
5. **Master-data foundations:** qualify regional authority, canonical Position behavior, Vehicles, reusable operational places, IV setups, map lifecycle and immutable address/equipment snapshots.
6. **Authoritative roster workflow:** run Manager create → Builder → Preview → Publish → Employee read → edit/republish/history, then incomplete Workdays, generated Travel, capacity/driver behavior, leave, Open Positions, decline and notifications.
7. **Conflict and reschedule scenarios:** cover Person/Vehicle same-day and adjacent-day overlaps, exact boundaries, cross-Region redaction, generated Travel, concurrent revalidation and destination-conflict rescheduling. A failed or declined reschedule confirmation must leave the source and generated Travel intact.
8. **Employee/privacy/device matrix:** exercise every role, Month/List/Day, Hours, offline namespace, logout/account switching, responsive widths and physical devices. Do not infer server authorization from hidden controls.
9. **Providers and background work:** observe one complete scheduler cycle, component-isolated provider failures, Contractor maintenance, reminders/outbox, deliberate Push-disabled behavior or staging-only real delivery, and last-good programme/map evidence.
10. **Restart and recovery window:** only under separate operational authority, prove app/scheduler/full-stack restart persistence and rehearse isolated restore of both PostgreSQL and `/app/data`.

Before any real employee access, phases 1–8 are release-critical. Real Push delivery may remain deliberately disabled if the deployment records that choice; destructive restore rehearsal, representative-volume performance and provider availability may remain pending only with an explicit operational risk decision.

## Actual staging identity and future change controls

The selected candidate upgrade has already occurred. These controls apply to read-only verification now and to any separately authorized future change; they do not authorize a redeploy.

1. In Portainer, open only the stack displayed as `on-track-rostering`. The observed Compose project label is also `on-track-rostering`, while container names use the `on-track-rostering-staging_*` prefix and the repository declares `on-track-rostering-staging`. Do not use the display name, container prefix or repository declaration as a substitute for inspecting the live labels.
2. Record the current image ID and repo digest for both app and scheduler. Confirm they match one another and record the current application build from Administration or the public login `data-build-id`.
3. Confirm the attached volumes are exactly `ontrack_staging_postgres_data` and `ontrack_staging_app_data`, the network is `ontrack_staging_internal`, neither app nor database publishes a host port, and the Cloudflare/reverse proxy reaches `staging_app:8000` only through the intended private network. Do not proceed if any production/shared resource appears.
4. Preserve the existing staging database credentials, `ONTRACK_SECRET_KEY`, `ONTRACK_CREDENTIAL_PEPPER`, allowed-host/trusted-proxy settings, exact WebAuthn origin/RP ID, VAPID choice and all MFA enrollment state. Verify variable presence and identity without copying values into evidence. Never regenerate or silently replace them during an image update.
5. Create a timestamped PostgreSQL custom-format dump and a separate copy of the entire staging `/app/data` volume as described in `BACKUP_RESTORE.md`. Verify `pg_restore --list`, dump size, SHA-256 checksum and readable app-data contents; record PostgreSQL version, deployed SHA and creation time. Keep an encrypted copy outside the host and all On Track volumes. The Admin CSV export is not a backup.
6. Confirm rollback choices before changing the stack: the verified two-part backup, the current qualified image/digest, and the selected qualified image/digest. Migration `b4e9d2a7f130` is additive: it creates place/equipment tables and adds a defaulted snapshot column. Do not plan an automatic Alembic downgrade; restore both backup parts if schema/data rollback is genuinely required.
7. For any future update, pin `STAGING_ONTRACK_IMAGE` to the chosen immutable `sha-<commit>` image. The Portainer stack is Git-backed and was observed performing periodic GitOps checks; a mutable `:staging` reference can change runtime content on a later pull/reconcile and cannot support stable acceptance evidence.
8. Only with separate explicit update authorization, redeploy without deleting/recreating volumes, changing environment values, running `create-admin`, or enabling Push.
9. After any future update, confirm both app and scheduler run the chosen image and digest. Verify the exact Alembic head independently; do not infer it from readiness alone. Check `/health/live`, `/health/ready`, authenticated `/health/scheduler`, Administration build/readiness, `production-check`, and redacted startup/scheduler logs.
10. For the present acceptance cycle, confirm the existing Admin can still authenticate, existing MFA/passkeys are retained, one known database record remains, retained Track-map files are readable, and an authorized map write remains possible. Then begin the ordered acceptance phases above.

If post-update validation fails, preserve the failed container/log/database state and stop acceptance. Do not reset, downgrade or restore automatically. First establish whether `b4e9d2a7f130` committed and whether the failure is image, configuration, schema or data related. The previous qualified image may be re-pinned only after confirming compatibility with the additive migrated schema; otherwise recovery means a separately authorized two-part restore of the verified PostgreSQL and `/app/data` backup. Never restore only one part when Track-map/database consistency matters.

The deployment operator can collect the required preflight facts on the Portainer host without rendering environment values or changing state. Resolve and inspect live containers by the observed labels, and stop if the result is not exactly one intended container per service:

```sh
docker ps --filter label=com.docker.compose.project=on-track-rostering --format '{{.ID}} {{.Names}} {{.Image}} {{.Label "com.docker.compose.service"}}'
docker inspect --format '{{.Name}} project={{index .Config.Labels "com.docker.compose.project"}} service={{index .Config.Labels "com.docker.compose.service"}} image={{.Config.Image}} image_id={{.Image}} mounts={{range .Mounts}}{{.Name}}:{{.Destination}} {{end}} ports={{json .NetworkSettings.Ports}} networks={{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' VERIFIED_CONTAINER_ID
docker exec VERIFIED_APP_CONTAINER_ID sh -c 'cat /app/.ontrack-build-id'
docker exec VERIFIED_DB_CONTAINER_ID sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version;"'
```

Do not use `docker compose config` without a narrow output option because a fully rendered configuration can expose secret environment values. Record only service/image IDs, volume/network/port identity, the build SHA, migration version and safe health statuses.

## Infrastructure and persistence

| Check | Status | Operator evidence / procedure |
|---|---|---|
| `GET /health/live` returns healthy | PASS | Direct unauthenticated GET returned 200 on 27/28 October and again on 6 November 2026; the public login then reported build `5dd5b77…`. Recheck at acceptance start. |
| `GET /health/ready` reaches PostgreSQL | PASS | Direct unauthenticated GET returned 200 on 27/28 October and again on 6 November 2026. The 28 October post-upgrade operator check separately reported database readiness with Alembic `b4e9d2a7f130`. Recheck at acceptance start. |
| `GET /health/scheduler` reports a recent completed tick | PASS | Operator check on 28 October 2026 reported scheduler readiness passing. Re-observe the completion timestamp at the start of the acceptance cycle. |
| Admin deployment-readiness panel and `production-check` agree | PENDING | Application/database/scheduler readiness was reported passing on 28 October, but agreement between both intentional diagnostic surfaces was not separately evidenced. Record statuses only; never record configured values. |
| App container reports healthy | PASS | Operator check on 28 October 2026 reported application readiness passing on `5dd5b77…`. Re-observe container health and image identity together. |
| PostgreSQL container reports healthy | PASS | Operator check on 28 October 2026 reported database readiness passing. |
| Startup migration reaches Alembic head | PASS | Operator check on 28 October 2026 confirmed PostgreSQL at `b4e9d2a7f130`, the selected source head. |
| Repeated app restart keeps Alembic idempotent | PENDING | Restart only the app safely; confirm no migration error. |
| Session and database state survive app restart | PENDING | Create demo state, restart app, re-check it. |
| Scheduler state survives/restarts correctly | PENDING | Record heartbeat before and after a safe scheduler restart; confirm due-state resumes without a duplicate concurrent owner. |
| Correct new build SHA appears after redeploy | PASS | Operator check on 28 October 2026 confirmed both application and scheduler on selected qualified image `5dd5b77…`; re-observe the full ID/digest for the acceptance record. |
| Startup/runtime logs have no unexpected errors | PENDING | Review app logs after redeploy and smoke flow. |
| Scheduler logs have no unexpected errors | PENDING | Review one complete tick; source, notification and Contractor maintenance failures remain isolated and intelligible. |
| Source refresh logs are clean | PENDING | Confirm no unexpected Love Racing programme/map refresh errors; retain evidence without private payloads. |
| No production/shared database volume is attached | PASS | Operator observed staging volumes `ontrack_staging_postgres_data` and `ontrack_staging_app_data` on 28 October 2026. Re-inspect mounts before maintenance; names alone do not prove ownership. |
| Staging app has no host-published port | PASS | Operator observed no host-published application or database ports and network `ontrack_staging_internal` on 28 October 2026. Direct-origin denial and tunnel routing still require separate verification. |

## Authentication and account security

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Valid login | PASS | Bootstrap Admin login observed through Cloudflare. |
| Invalid login is rejected | PENDING | Use a deliberately wrong demo credential. |
| Failure response is generic | PENDING | Confirm it does not reveal account existence. |
| Login throttles after repeated failures | PENDING | Use a demo account; record timing without recording credentials. |
| Login succeeds after throttle expiry/reset | PENDING | Wait/reset only through supported behavior. |
| Logout ends the authenticated session | PENDING | Confirm protected navigation redirects to login. |
| Secure cookie persists appropriately | PENDING | Inspect browser flags: Secure, HttpOnly where applicable, SameSite. |
| Fresh reauthentication gates sensitive actions | PENDING | Allow the fresh window to expire, then test a sensitive action. |
| Trusted-device lifetime/label behavior | PENDING | Check Settings and subsequent use. |
| Credential change succeeds | PENDING | Demo account only. |
| Policy-scoped credential reset link | PENDING | Admin may issue globally; a scoped Manager may assist only an ordinary Employee/Contractor/Sub-manager account allowed by policy. Privately copy the trusted-origin fragment link and never record it as evidence. |
| Higher-privilege security boundary | PENDING | Manager cannot reset, disable or revoke devices for an active Manager/Admin account. |
| Credential reset is single-use and expires | PENDING | Complete one reset, confirm reuse fails generically, and allow a separate demo reset to pass 60 minutes. |
| Credential reset invalidates old login state | PENDING | Confirm the old primary credential and existing trusted devices fail while the new credential works and configured MFA is still required. |
| Local reset CLI | PENDING | Use a disposable demo account; enter the secret only through the two getpass prompts and confirm terminal/audit output contains no secret. |
| Credential change invalidates other sessions | PENDING | Use two demo browser profiles. |
| Role elevation rejects/reclassifies stale sessions | PENDING | Use a demo Employee promoted by Admin. |
| Disabling an account invalidates its sessions | PENDING | Use a non-final-Admin demo account. |

## Passkeys

Configured staging origin must be exactly `https://ontrackrostering.dubcodesmedia.com`; the RP ID must be `ontrackrostering.dubcodesmedia.com`.

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Windows Hello/security-key registration | PENDING | Register against a demo account on the real HTTPS origin. |
| Registered passkey appears in Settings | PENDING | Record label only. |
| Logout after registration | PENDING | Confirm session ends. |
| Username-less passkey login | PENDING | Authenticate from the login page. |
| Successful use updates passkey state | PENDING | Check last-used display/state if exposed. |
| Remove passkey | PENDING | Requires fresh authentication. |
| Removed passkey no longer authenticates | PENDING | Attempt login with the removed credential. |
| Wrong RP/origin is rejected | PENDING | Use safe browser-origin validation; do not weaken configuration. |

## TOTP

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Begin real TOTP setup | PENDING | Demo account only. Do not capture the secret. |
| QR and manual key are generated | PENDING | Confirm visually without recording either value. |
| Confirmation code enables factor | PENDING | Confirm Settings state. |
| Password/PIN login leads to TOTP challenge | PENDING | Logout, then log in normally. |
| Invalid TOTP code is rejected | PENDING | Do not trigger uncontrolled lockout. |
| Accepted code cannot be replayed | PENDING | Reuse the same timestep code where practical. |
| Disable factor | PENDING | Confirm fresh-auth requirement and subsequent login. |
| Ten recovery codes appear once after confirmation | PENDING | Store them safely; do not capture them in evidence. |
| Recovery-code login and single-use rejection | PENDING | Use a demo code, then confirm reuse fails generically. |
| Regeneration invalidates unused predecessors | PENDING | Demo account only; do not record either set. |
| Disabling TOTP removes recovery codes | PENDING | Confirm subsequent recovery login is unavailable. |

## Branding and global settings

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Change product name | PENDING | Use a realistic temporary staging name. |
| Navigation branding changes | PENDING | Check authenticated chrome. |
| Login branding changes | PENDING | Log out and check login. |
| Browser page title changes | PENDING | Check multiple pages. |
| Dynamic manifest changes | PENDING | Fetch `/manifest.webmanifest`. |
| Built-in favicon loads | PENDING | Check a clean browser profile and direct favicon request. |
| Custom Admin PNG favicon and reset | PENDING | Upload a safe demo PNG, verify browser/manifest use after refresh, then reset to built-in. |
| Favicon cache/installed-app behavior | PENDING | Verify build/version refresh and installed-app icon where practical; record device/browser, not image content. |
| Settings/help copy changes where relevant | PENDING | Check product-name surfaces. |
| Passkey/TOTP RP-facing display name changes where appropriate | PENDING | Do not change RP ID/origin. |
| Restore desired product name | PENDING | Record restored value, not credentials. |
| Toggle public Create account/signup | PENDING | Confirm effect on next request. |
| Signup creates no authority | PENDING | Inspect pending request and access denial. |

## Invitations, signup, and account lifecycle

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Create demo Person | PENDING | Use non-production identity. |
| Create standalone demo account | PENDING | Confirm separate User/Person semantics. |
| Link User to existing Person | PENDING | Confirm one-to-one linkage. |
| Create one-time invitation | PENDING | Never paste raw token into evidence. |
| Invitation secret is held in URL fragment | PENDING | Confirm token is absent from the HTTP request URL. |
| Activate invitation | PENDING | Confirm POST-body handoff and account creation. |
| Raw invitation token absent from access logs | PENDING | Review redacted server/proxy logs. |
| Invitation is single-use | PENDING | Second activation must fail generically. |
| Revoked invitation fails | PENDING | Create and revoke a separate invitation. |
| Public signup remains pending | PENDING | Confirm no role, Person link, or roster access. |
| Approve/link signup | PENDING | Use Manager/Admin within correct region. |
| Reject signup | PENDING | Confirm terminal state. |
| Disable non-final-Admin user | PENDING | Confirm access/session removal. |
| Reactivate user | PENDING | Confirm supported status transition. |
| Revoke trusted devices | PENDING | Confirm sessions end. |
| Final active Admin protection | PENDING | Never risk the bootstrap Admin; create two demo Admins first. |
| Duplicate email/identity is controlled | PENDING | Confirm friendly 400/409, never 500. |

## Imported-People onboarding

Use demo-only People and never record raw setup tokens.

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Needs Region state | PENDING | Create/import a regionless demo Person and confirm Admin-only nationwide visibility. |
| Needs Email state | PENDING | Use an in-scope demo Person with Region but no email. |
| Ready to Invite state | PENDING | Supply valid unique email and Primary Region through the worksheet workflow. |
| Invitation Pending state | PENDING | Generate one setup link and confirm the Person is no longer selectable as Ready. |
| Account Linked state | PENDING | Complete activation and confirm linkage to the exact pre-existing Person UUID. |
| UUID worksheet export | PENDING | Confirm `person_id` is the On Track UUID and no Deputy employee ID is required or exposed. |
| Metadata Preview is read-only | PENDING | Preview email/Region changes and confirm no Person/User mutation before Apply. |
| Metadata Apply is atomic and metadata-only | PENDING | Apply valid demo rows; confirm Person email/Region update and no User, credential or invitation is created. |
| Invalid/duplicate metadata is blocked | PENDING | Exercise invalid/unknown UUID, duplicate row/email and unknown/out-of-scope Region without partial writes. |
| Batch setup links require CSRF and fresh auth | PENDING | Allow fresh-auth expiry, verify denial, reauthenticate and retry. |
| Absolute trusted-origin fragment link | PENDING | Confirm public staging origin and `#token=...`; never record the token and confirm it is absent from the HTTP URL/log. |
| One-time no-store result and client CSV | PENDING | Confirm response is no-store, copy/download occurs locally, and no raw-link POST follows. |
| Recipient chooses credential | PENDING | Complete one demo activation and verify Employee grant plus intended Person linkage; no automatic email is sent. |

## Crew management

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Compact Crew directory | PENDING | Verify dense directory readability, search, Region context and setup/account summaries at desktop and mobile widths. |
| Person detail | PENDING | Verify separate Person/User identity, profile, Crew groups, capability history, account status, devices and lifecycle without exposing internal diagnostics. |
| Continuous edits | PENDING | Change profile/group metadata and confirm save/discard behavior does not relocate the page or scroll unexpectedly. |
| One capability picker | PENDING | Open Add capability decision; verify one searchable Position control, complete list on focus, keyboard/touch choice, Escape and authoritative hidden ID. |
| Setup/reissue/reset | PENDING | Verify exact-Person UUID-bound setup, reissue/revoke, and policy-scoped credential reset with trusted-origin fragment URLs. |
| Trusted devices | PENDING | Verify scoped revoke behavior and that Manager cannot control active Manager/Admin security. |
| Manager privilege boundary | PENDING | Manager can administer ordinary Employee/Contractor/Sub-manager accounts only in administered Regions and cannot grant Manager/Admin. |

## Roles and authorization

Create demo-only Admin, Manager, SubManager, Employee, Contractor, and Viewer accounts. For every role, test normal UI visibility and direct crafted requests; hidden controls alone are not authorization proof.

| Check | Status | Expected result |
|---|---|---|
| Admin authority | PENDING | Global administration and explicit scoped operations. |
| Manager authority | PENDING | Regional rostering and permitted regional account/catalog administration. |
| SubManager authority | PENDING | Regional roster mutation; no broad security/catalog administration. |
| Employee authority | PENDING | Own roster, scoped Crew View, and permitted self-service only. |
| Contractor authority | PENDING | Own/event-specific access unless separately granted scope. |
| Viewer authority | PENDING | Broad scoped read-only access; every mutation denied. |
| Cross-region direct requests | PENDING | Authority remains independently scoped. |

## Operational notices

| Check | Status | Expected result |
|---|---|---|
| Composer visibility | PENDING | Composer is expanded by default in Management. |
| Scope presentation | PENDING | Admin global/all-crew choice is inside Scope; a new regional notice selects one sensible default Region, not every allowed Region. |
| Authorized multi-Region target | PENDING | Manager/Sub-manager may deliberately select additional Regions only within their configured publishing scope. |
| Explicit empty override | PENDING | Admin-configured empty publishing scope remains distinct from role-derived defaults and prevents regional publishing. |
| Forged out-of-scope target | PENDING | Server rejects an unauthorized Region even when crafted directly. |
| Deduplicated audience | PENDING | One User relevant through multiple selected Regions receives one notice event/delivery. |

## Master data and input validation

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Regions create/update/archive | PENDING | Include invalid and duplicate names. |
| Tracks create/update/archive and colours | PENDING | Include duplicate within region and invalid colour. |
| Crew groups create/update/archive | PENDING | Include duplicate name. |
| Base positions create/update/archive | PENDING | Canonical active Base Position identity is global across Crew Groups; include alias and numbered-label conflicts. |
| Clean-import canonical assertion | PENDING | Immediately after fresh import, run Position convergence Preview. It must report no new canonical duplicates. If it reports any, stop and investigate the importer; do not apply convergence. |
| Position presets persist and remain region-scoped | PENDING | Create, edit, apply, and reload a demo preset. |
| People create/update/archive | PENDING | Include invalid region and linked lifecycle behavior. |
| Invalid UUID/reference inputs are controlled | PENDING | Crafted Person/Region/Track/Position values must not yield 500. |
| Cross-region administration is denied | PENDING | Manager outside scope. |
| Historical snapshots survive rename/archive | PENDING | Publish first, then rename/archive master record. |
| Region base and Track physical addresses | PENDING | Configure demo addresses and default travel; confirm travel stays configured rather than route-derived. |
| Published address snapshot immutability | PENDING | Publish, edit live Track/base addresses, and confirm the historical Day retains the original values. |
| Reusable operational places | PENDING | Create a regional hotel and destination, select both in Builder, verify copied addresses, then rename/archive the catalogue rows and confirm the published Day remains unchanged. Confirm one-off free text still saves without an address. |
| IV equipment setup snapshots | PENDING | Create, edit and duplicate a regional setup; apply it in Builder, change one quantity for the Workday, Publish, then alter/archive the template and confirm the published configuration and change history remain unchanged. |
| Track map automatic/manual/reset lifecycle | PENDING | Verify safe automatic image, scoped manual override, reset and last-good retention. |
| Cambridge venues stay distinct | PENDING | Confirm Cambridge Harness and Cambridge Synthetic have separate identity, address, map and travel configuration. |

## Roster lifecycle

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Create incomplete Workday | PENDING | Confirm warnings do not block manual work. |
| Unified Builder creation | PENDING | Open an empty calendar date and `/manage/workdays/new`; confirm date prefill and no Workday until Save & Preview. |
| Position picker interaction | PENDING | Focus opens the complete valid list; typing filters; Escape restores the selected label/hidden ID; keyboard and touch selection work. |
| Person picker interaction | PENDING | Repeat complete-list, filter, Escape, hidden-ID, keyboard and touch checks with scoped People. |
| Vehicle picker interaction | PENDING | Repeat complete-list, filter, Escape, hidden-ID, keyboard and touch checks with normal and truck Vehicles. |
| Race Day workflow | PENDING | Edit → Preview → Publish. |
| Race Day automatic timing | PENDING | With demo values prove first-race floor, Region setup lead, snapshotted Track travel, `last race start + 10m`, race-clear quarter ceiling, Pack-up, Return travel and Finish. |
| Timing layout and truck control | PENDING | Timing controls align; the truck checkbox is inside Timing and defaults checked for a new Race Day. |
| Timing explanation consistency | PENDING | Builder, Preview, published Day calculation detail and Team Hours must agree for the same demo Race Day. |
| Known timing examples | PENDING | Verify `12:12 → 12:00`, `12:35 → 12:30`, `16:27 + 10 → 16:45`, and `17:44 + 10 → 18:00` without private roster data. |
| Track default travel snapshot | PENDING | Confirm Track travel derives the default crew call and published timing does not change after master-data edit. |
| Call-time derivation and explicit reset | PENDING | Confirm vehicle/self travel behavior, explicit timing override, and reset to calculation. |
| Truck Person-effective start | PENDING | Normal vehicle starts at normal Workday Start; truck starts 15 minutes earlier; Workday Start itself does not move and Hours includes the earlier Person start. |
| Generated travel | PENDING | Confirm eligible overnight travel creates the parent-linked prior-day Travel Day and publishes atomically. |
| Non-Race-Day category | PENDING | Confirm race-only wording/fields are absent where appropriate. |
| Private draft creation | PENDING | Verify employee cannot see it. |
| Edit details and Day note | PENDING | Include date and time changes. |
| Add position slots | PENDING | Exercise base position plus numbered slots. |
| Multiple Positions and Move/Keep/Cancel | PENDING | Assign one Person twice and verify each explicit conflict choice, independent row timing/notes, and shared travel. |
| Person-level synchronized travel | PENDING | Change travel on one of a Person's rows and confirm the shared plan and combined span. |
| Regional and borrowed Vehicles | PENDING | Confirm Manager own-region create/edit/archive, Builder selection of another Region's active borrowed Vehicle, and unchanged Vehicle ownership after save/publish. |
| Base Position ordering | PENDING | Confirm configured catalogue order is used operationally. |
| Audit scope and redaction | PENDING | Confirm Admin global reads, complete managed-Workday-Region records, and Sub-Manager/read-only denial. |
| Cross-region employee Audit oversight | PENDING | Publish a Christchurch Workday containing Northern and Christchurch People. Northern Manager automatically sees only the Northern employee's safe addition/Position/timing/removal impacts, responsible actor, timestamp, Christchurch Region and published revision context; no unrelated employee, accommodation/private note, draft or edit control appears. The event appears once if both visibility paths apply. |
| Leave management and warning override | PENDING | Create inclusive leave, observe Builder/Preview warning, and deliberately save an authorized override. |
| ASSIGNED / OPEN / TBC / MANAGER_ACTION_REQUIRED | PENDING | Verify distinct states. |
| Assignment/private note controls | PENDING | Verify visibility separately. |
| Person-specific start/end | PENDING | Confirm effective displayed span. |
| Preview and warnings | PENDING | Review exact proposed changes. |
| Adjacent-day Person conflict | PENDING | Publish a demo 23:00–02:00 Workday, then Preview the same Person at 01:00–04:00 next day; verify one dated warning, explicit Publish-anyway acknowledgement and successful deliberate override. |
| Adjacent-day Vehicle conflict | PENDING | Repeat with different People sharing one Vehicle; exact 02:00 handoff must not warn, while 01:59 overlap must warn. |
| Cross-Region conflict privacy | PENDING | A Manager with only one Region sees the affected Person/Vehicle and dated timing but only “another Workday,” never the other Region's title/location/private detail. |
| TBC adjacent-date behavior | PENDING | Same-date incomplete timing warns conservatively; an adjacent-date TBC row alone does not produce speculative noise. |
| Generated Travel conflict behavior | PENDING | Parent and its generated Travel do not warn against each other; either part warns once against a genuinely overlapping independent Workday. |
| Concurrent publication revalidation | PENDING | Two Managers attempt overlapping different Workdays; the later transaction must revalidate and require Publish-anyway rather than both silently passing. |
| First Publish | PENDING | Confirm atomic authoritative visibility. |
| Employee Month and Day appearance | PENDING | Confirm only published revision appears. |
| Edit published day clones current publication | PENDING | Confirm stable slot keys and private draft. |
| Republish/history/human diff | PENDING | Include a change reason. |
| Date change behavior | PENDING | Old publication remains immutable in history. |
| Reschedule published Workday | PENDING | Confirm a new linked Workday/publication and preserved source history. On a conflicting destination, verify the first submit shows redacted conflict review, requires a separate explicit acknowledgement, and forces re-review if another Manager changes the conflict set before confirmation. |

## Love Racing programme and Race/Trial association

Use a current public meeting and keep manual roster entry available throughout.

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Meeting discovery | PENDING | Refresh Love Racing and confirm a current meeting is discovered without creating authoritative roster data. |
| Race programme detail | PENDING | Confirm complete/partial status, race count, first race, last race and numbered evidence from Meeting-Overview. |
| Trial Heat programme detail | PENDING | Confirm complete/partial status, first Trial, last Trial where available, count and numbered Heat evidence. |
| Automatic bounded refresh | PENDING | Observe scheduler refresh and retry state; a complete programme is not fetched every scheduler tick. |
| No ordinary per-event refresh | PENDING | External Event detail has no “Refresh programme now” control; provider-wide Admin diagnostics remain. |
| Last-good preservation | PENDING | Where practical, observe a transient failure without losing previously accepted structured facts. |
| Same-day Race/Trial presentation | PENDING | Same Track/date/discipline and known first times within 180 minutes display together while source rows remain distinct. |
| Association exclusions | PENDING | Different Track/discipline, unknown timing, or more than 180 minutes remain separate. |
| Published Race Day with related Trial | PENDING | Workday remains authoritative and Trial context does not change calculated Race Day Start/Finish. |

## Concurrency and stale writes

Use two independent browser profiles or sessions on the same Workday.

| Check | Status | Expected result |
|---|---|---|
| Two editors open the shared draft | PENDING | Both initially see the same version. |
| First editor mutates details/assignment | PENDING | Version advances. |
| Second editor submits stale mutation | PENDING | Controlled conflict; no overwrite. |
| Second editor submits stale Publish | PENDING | Controlled conflict; no publication. |
| Refresh/recovery path | PENDING | Latest shared draft loads safely. |
| Shared-draft constraint remains intentional | PENDING | No claim of independent per-Manager branches. |

## Open positions

| Check | Status | Expected result |
|---|---|---|
| Eligible Employee sees opening | PENDING | Effective capability permits it. |
| Blocked/opted-out Employee does not | PENDING | Manager block and opt-out precedence hold. |
| Unknown eligibility is not proactively offered | PENDING | Manual Manager assignment remains possible. |
| Apply to opening | PENDING | Application binds publication and slot. |
| Duplicate/stale application | PENDING | Controlled rejection/no duplicate. |
| Manager selection changes draft only | PENDING | Employee publication remains unchanged. |
| Publish accepts selected applicant | PENDING | Competing applications close correctly. |

## Position capability history

| Check | Status | Expected result |
|---|---|---|
| First published Workday | PENDING | Assigned publication records raw `WORKED` history but does not create history-only eligibility. |
| Second distinct published Workday | PENDING | A second current-published scheduled Workday for the canonical Base Position creates qualified inferred eligibility. |
| Duplicate slots on one Workday | PENDING | Multiple assignments for the canonical Position on one Workday count as one worked day. |
| Canonical aliases | PENDING | Historical aliases contribute to the same canonical two-day threshold. |
| Explicit block precedence | PENDING | Manager block remains blocking even when worked-history qualification exists; Employee opt-out and explicit allow precedence follow the documented rule. |

## Decline

| Check | Status | Expected result |
|---|---|---|
| Future Employee/Contractor can decline own assigned slot | PENDING | Only before effective start. |
| OPEN_IMMEDIATELY policy | PENDING | New immutable publication contains OPEN slot. |
| MANAGER_REVIEW policy | PENDING | New immutable publication requires Manager action. |
| Past/started assignment | PENDING | Decline denied. |
| Unknown-start current day | PENDING | Decline denied safely. |
| Another user attempts decline | PENDING | Denied by authenticated Person binding. |

## Privacy

| Check | Status | Expected result |
|---|---|---|
| Event-only Contractor Day | PENDING | Own assignment only. |
| Employee Crew View | PENDING | Correct regional scope. |
| Viewer published roster | PENDING | Region-scoped published rows remain readable; drafts and management surfaces remain unavailable. |
| Private assignment note/address | PENDING | Assigned Person and authorized Manager/Sub-Manager only; confirm Viewer cannot read another Person's private note or accommodation address. |
| Normal Day note | PENDING | Visible according to documented Day policy. |
| Human Change History | PENDING | No unrelated crew disclosure to event-only users. |
| Cross-region one-event access | PENDING | Does not become permanent region access. |
| Track/base/accommodation privacy | PENDING | Assigned Contractor sees the operational Track address needed for the event; accommodation address and private assignment detail remain own/scoped-management only. |
| Personal offline Day JSON | PENDING | No unrelated crew. |
| Management data cached offline | PENDING | Must not be cached. |

## Operational leave intake

Use demo-only People and the documented conservative three-column format.

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Paste and Preview | PENDING | Paste `Person Name<TAB>Start Date<TAB>End Date`; verify exact Person/date interpretation and zero writes before Apply. |
| Atomic Apply | PENDING | Apply a valid multi-row preview and confirm all rows appear together. |
| Duplicate/idempotent range | PENDING | Reapply an exact active range and confirm it is unchanged rather than duplicated. |
| Invalid/ambiguous input | PENDING | Invalid date, reverse range, unknown or duplicate exact name blocks Apply without partial writes. |
| Builder warning and deliberate override | PENDING | Roster a demo Person on leave only after the warning; Manager override remains possible. |
| Team Hours leave state | PENDING | Leave-only Person appears and a worked-on-leave day visibly retains both states. |

## Hours

| Check | Status | Expected result |
|---|---|---|
| Published effective span | PENDING | Current publication only. |
| Primary-Region employee oversight across Workday Regions | PENDING | Northern Manager sees every current published Northern Person span on Northern, Central and Christchurch Workdays with useful Track/Workday-Region labels, while an unrelated Christchurch Person on a Northern Workday remains absent. |
| Multiple assignments combine | PENDING | Earliest start, latest finish, no duplicated time. |
| Overnight work | PENDING | Correct cross-midnight duration. |
| Fortnight totals/boundaries | PENDING | Correct anchor and grouping. |
| Team Hours filters | PENDING | Name search, Crew-group filter and Primary-Region filter narrow authorized People without changing any selected Person's cross-Workday-Region total; forged Region/Person requests expose nothing. |
| Persisted fortnight anchor | PENDING | Change the Admin setting and confirm Hours uses it after reload. |
| Public/regional holiday marker | PENDING | Person geography drives empty-cell marker. |
| Break deduction | PENDING | No automatic deduction. |
| Allowances | PENDING | Informational and do not increase hours. |
| Fourteen-day Track-colour matrix | PENDING | Desktop columns align; Track colour is the day identity and Person/Total columns remain readable. |
| No weekend-special presentation | PENDING | Saturday/Sunday use the same workload treatment as other operating days. |
| Leave and worked-on-leave state | PENDING | Leave-only Person appears; a worked day on leave visibly retains both states. |
| Mobile Hours behavior | PENDING | At 430/375/320 the matrix scrolls within its container, body does not overflow, visible daily duration is hidden, title/accessible detail remains, and expansion gives exact hours. |

## Offline / PWA

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Service worker registers | PENDING | Inspect browser application state. |
| Online personal Month/Day loads | PENDING | Demo Employee/Contractor. |
| Upcoming work and personal Day prefetch | PENDING | Verify only server-selected days. |
| Physically disconnected cached Day opens | PENDING | Disconnect network after confirmed save. |
| Saved timestamp is actual cache time | PENDING | Compare displayed timestamp. |
| No management controls/unrelated crew offline | PENDING | Inspect cached HTML/JSON. |
| Logout clears roster caches | PENDING | Inspect Cache Storage. |
| Account switch deletes prior namespace | PENDING | Use two demo accounts. |
| Reconnect returns authoritative state | PENDING | Update online, then reconnect. |

## Notifications

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Preferences UI and save behavior | PENDING | Toggle each reminder preference. |
| Enable on this device UX | PENDING | Record configured/unconfigured message. |
| Real Web Push provider delivery | PENDING | Keep pending if staging VAPID is absent; never fake a pass. |
| Push deliberately disabled without staging VAPID | PENDING | With staging-specific keys empty, app/scheduler behavior is quiet and deterministic with no recurring error noise. |
| Event generation is distinct from delivery | PENDING | Review redacted worker/database evidence. |
| Durable scheduler invokes worker | PENDING | Observe scheduler heartbeat and notification status; the manual CLI remains diagnostic only. |

## Responsive and real devices

Before physical-device work, verify Help shows the roster legend, Android notification guidance, iOS installed-web-app guidance and Report error guidance. Normal pages must not expose development/migration diagnostics or a global build footer; Administration must show build/version/SHA.

Test desktop plus 430px, 375px, and 320px. On each applicable width inspect Login, Month, Day, Help, Crew calendar, Crew directory, Crew Person detail and capability picker, Build, Preview, Management and notice Scope, Master Data and scoped Vehicle management, Team Hours filters, Accounts, Administration, and Settings.

| Check | Status | Expected result |
|---|---|---|
| No unintended horizontal overflow | PENDING | Include realistically long names across Builder, Team Hours, Accounts onboarding, Leave, Month/Day and maps at 1280/430/375/320. |
| Navigation remains usable | PENDING | No inaccessible destinations or controls. |
| Forms and selectors remain usable | PENDING | Timing controls align; Position/Person/Vehicle and Crew capability menus remain attached and usable with keyboard/touch. |
| Calendar/list controls remain usable | PENDING | Include swipe/keyboard where supported. |

## Logs and security

| Check | Status | Expected result |
|---|---|---|
| Application logs reviewed | PENDING | No unexpected 500, origin, or database error. |
| PostgreSQL logs reviewed | PENDING | Expected constraint-test errors distinguished from runtime faults. |
| Credential/PIN/password values absent | PENDING | Search only redacted logs. |
| Session/CSRF/invitation tokens absent | PENDING | Raw values must not appear. |
| TOTP/passkey challenge secrets absent | PENDING | Raw values must not appear. |
| Push endpoint/key material absent | PENDING | Raw values must not appear. |
| Normal Cloudflare Tunnel behavior | PENDING | No custom `X-Forwarded-Host` Transform Rule or synthetic override required. |

## Recovery and restart

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Restart staging app safely | PENDING | Do not remove database or volume. |
| Health returns after restart | PENDING | Check live and ready. |
| Admin, roster, and master data remain | PENDING | Compare demo records before/after. |
| Migrations remain clean | PENDING | Review second startup log. |
| Full stack restart | PENDING | Perform only when operationally appropriate. |
| PostgreSQL volume persists | PENDING | Never delete/recreate the volume for this test. |
| Logical backup/restore rehearsal | PENDING | May remain pending until production-promotion phase. |
| Both database and app-data are restored | PENDING | Follow `BACKUP_RESTORE.md` against isolated staging only. |

## Production-promotion work

These items do not block active staging development unless they reveal a structural defect.

| Item | Status | Boundary |
|---|---|---|
| Verified production backup and restore rehearsal | NOT IN CURRENT SCOPE | Production-promotion phase. |
| Production secrets and credential rotation | NOT IN CURRENT SCOPE | Deployment operator work. |
| Production hostname and WebAuthn RP identity | NOT IN CURRENT SCOPE | Freeze before enrolment. |
| Production VAPID and real Push qualification | NOT IN CURRENT SCOPE | Requires provider/device setup. |
| Production monitoring/alerting | NOT IN CURRENT SCOPE | Operations design. |
| Branch protection/release controls | FAIL | GitHub reported `main` unprotected on 27 October 2026 and again on 6 November 2026. Configure and directly verify required release-gate enforcement before production promotion. |
| Immutable release tag/image | NOT IN CURRENT SCOPE | Required at Foundation freeze/promotion, not each active staging change. |
