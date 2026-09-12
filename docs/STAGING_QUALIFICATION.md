# Real private staging qualification

Deployment under test: `https://ontrackrostering.dubcodesmedia.com`

This is an operator record for the real isolated staging system. Automated tests show implementation behavior; they do not replace interaction with the deployed application. Never enter credentials, raw invitation tokens, session values, MFA secrets, passkey challenges, push keys, or private staff data into this document.

## Status rules

- **PASS** — observed against the real deployment, with non-sensitive evidence recorded.
- **FAIL** — observed incorrect behavior; record reproduction, time, deployed SHA, and issue reference.
- **PENDING** — not yet exercised against the real deployment, or evidence is incomplete.
- **NOT IN CURRENT SCOPE** — deliberately deferred and not a blocker for active staging development.

When executing a row, record date/time, deployed footer SHA, role/browser/device, outcome, and a redacted screenshot or log reference where useful. Change a status only from direct staging evidence.

## Known deployment evidence

| Check | Status | Evidence |
|---|---|---|
| HTTPS staging is reachable | PASS | User observed the real private HTTPS deployment. |
| Public hostname is `ontrackrostering.dubcodesmedia.com` | PASS | User observed the public URL. |
| Footer visibly reports `ee8af0d5dcd215c6df02f113ef8c8bf43ee936f2` | PASS | User observed the deployed footer. |
| PostgreSQL initialized | PASS | Real staging database initialization completed. |
| Bootstrap Admin creation | PASS | `python -m app.cli create-admin` completed once in staging. Do not recreate it. |
| Admin password/PIN login through Cloudflare Tunnel | PASS | User successfully authenticated after `ee8af0d`. |
| Authenticated session persists across navigation | PASS | User navigated authenticated pages without losing the session. |
| Month page renders | PASS | `/month` rendered in the real deployment. |
| Settings page renders | PASS | `/settings` rendered in the real deployment. |
| Exact-head gate for deployed `ee8af0d` | PASS | GitHub run `34715787713`: PostgreSQL 17.6, 82 tests, 6 Playwright tests, audits, Compose, and image build passed. |

No other row is pre-qualified.

## Infrastructure and persistence

| Check | Status | Operator evidence / procedure |
|---|---|---|
| `GET /health/live` returns healthy | PENDING | Record status and response. |
| `GET /health/ready` reaches PostgreSQL | PENDING | Record status and response. |
| App container reports healthy | PENDING | Record Portainer health only; do not expose secrets. |
| PostgreSQL container reports healthy | PENDING | Record health only. |
| Startup migration reaches Alembic head | PENDING | Review startup log after redeploy. |
| Repeated app restart keeps Alembic idempotent | PENDING | Restart only the app safely; confirm no migration error. |
| Session and database state survive app restart | PENDING | Create demo state, restart app, re-check it. |
| Correct new build SHA appears after redeploy | PENDING | Match footer exactly to the qualified commit. |
| Startup/runtime logs have no unexpected errors | PENDING | Review app logs after redeploy and smoke flow. |
| No production/shared database volume is attached | PENDING | Verify stack service/volume names without changing them. |
| Staging app has no host-published port | PENDING | After redeploy, verify tunnel reaches `staging_app:8000` over `ontrack_staging_internal`. |

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
| Recovery codes | NOT IN CURRENT SCOPE | Recovery codes are not implemented. |

## Branding and global settings

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Change product name | PENDING | Use a realistic temporary staging name. |
| Navigation branding changes | PENDING | Check authenticated chrome. |
| Login branding changes | PENDING | Log out and check login. |
| Browser page title changes | PENDING | Check multiple pages. |
| Dynamic manifest changes | PENDING | Fetch `/manifest.webmanifest`. |
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

## Master data and input validation

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Regions create/update/archive | PENDING | Include invalid and duplicate names. |
| Tracks create/update/archive and colours | PENDING | Include duplicate within region and invalid colour. |
| Crew groups create/update/archive | PENDING | Include duplicate name. |
| Base positions create/update/archive | PENDING | Include duplicate within group. |
| People create/update/archive | PENDING | Include invalid region and linked lifecycle behavior. |
| Invalid UUID/reference inputs are controlled | PENDING | Crafted Person/Region/Track/Position values must not yield 500. |
| Cross-region administration is denied | PENDING | Manager outside scope. |
| Historical snapshots survive rename/archive | PENDING | Publish first, then rename/archive master record. |

## Roster lifecycle

| Check | Status | Operator evidence / procedure |
|---|---|---|
| Create incomplete Workday | PENDING | Confirm warnings do not block manual work. |
| Race Day workflow | PENDING | Edit → Preview → Publish. |
| Non-Race-Day category | PENDING | Confirm race-only wording/fields are absent where appropriate. |
| Private draft creation | PENDING | Verify employee cannot see it. |
| Edit details and Day note | PENDING | Include date and time changes. |
| Add position slots | PENDING | Exercise base position plus numbered slots. |
| ASSIGNED / OPEN / TBC / MANAGER_ACTION_REQUIRED | PENDING | Verify distinct states. |
| Assignment/private note controls | PENDING | Verify visibility separately. |
| Person-specific start/end | PENDING | Confirm effective displayed span. |
| Preview and warnings | PENDING | Review exact proposed changes. |
| First Publish | PENDING | Confirm atomic authoritative visibility. |
| Employee Month and Day appearance | PENDING | Confirm only published revision appears. |
| Edit published day clones current publication | PENDING | Confirm stable slot keys and private draft. |
| Republish/history/human diff | PENDING | Include a change reason. |
| Date change behavior | PENDING | Old publication remains immutable in history. |

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
| Viewer management detail | PENDING | Read-only. |
| Private assignment note | PENDING | Assigned person and authorized Manager/SubManager/Viewer only. |
| Normal Day note | PENDING | Visible according to documented Day policy. |
| Human Change History | PENDING | No unrelated crew disclosure to event-only users. |
| Cross-region one-event access | PENDING | Does not become permanent region access. |
| Personal offline Day JSON | PENDING | No unrelated crew. |
| Management data cached offline | PENDING | Must not be cached. |

## Hours

| Check | Status | Expected result |
|---|---|---|
| Published effective span | PENDING | Current publication only. |
| Multiple assignments combine | PENDING | Earliest start, latest finish, no duplicated time. |
| Overnight work | PENDING | Correct cross-midnight duration. |
| Fortnight totals/boundaries | PENDING | Correct anchor and grouping. |
| Public/regional holiday marker | PENDING | Person geography drives empty-cell marker. |
| Break deduction | PENDING | No automatic deduction. |
| Allowances | PENDING | Informational and do not increase hours. |

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
| Event generation is distinct from delivery | PENDING | Review redacted worker/database evidence. |
| Durable scheduler invokes worker | PENDING | Required command: `python -m app.cli deliver-notifications`; production scheduler is separate. |

## Responsive and real devices

Test desktop plus 430px, 375px, and 320px. On each applicable width inspect Login, Month, Day, Crew, Build, Preview, Manage, Masters, Team hours, Accounts, Admin, and Settings.

| Check | Status | Expected result |
|---|---|---|
| No unintended horizontal overflow | PENDING | Include realistically long names. |
| Navigation remains usable | PENDING | No inaccessible destinations or controls. |
| Forms and selectors remain usable | PENDING | Labels, errors, buttons, and pickers fit. |
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

## Production-promotion work

These items do not block active staging development unless they reveal a structural defect.

| Item | Status | Boundary |
|---|---|---|
| Verified production backup and restore rehearsal | NOT IN CURRENT SCOPE | Production-promotion phase. |
| Production secrets and credential rotation | NOT IN CURRENT SCOPE | Deployment operator work. |
| Production hostname and WebAuthn RP identity | NOT IN CURRENT SCOPE | Freeze before enrolment. |
| Production VAPID and real Push qualification | NOT IN CURRENT SCOPE | Requires provider/device setup. |
| Durable production reminder scheduler | NOT IN CURRENT SCOPE | External scheduler required. |
| Production monitoring/alerting | NOT IN CURRENT SCOPE | Operations design. |
| Branch protection/release controls | NOT IN CURRENT SCOPE | Production release governance. |
| Immutable release tag/image | NOT IN CURRENT SCOPE | Required at Foundation freeze/promotion, not each active staging change. |
| Operations/Travel, Accommodation, provider ingest, full vehicle operations | NOT IN CURRENT SCOPE | Future product phase. |
| Abandoned/rescheduled workflow, logo upload, major recovery redesign | NOT IN CURRENT SCOPE | Future product phase. |
