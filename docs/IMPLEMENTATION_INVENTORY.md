# Implementation inventory

This inventory maps the delivered foundation to code, migrations, automated evidence, and remaining operational validation. “Implemented” means code and deterministic tests exist; it does not turn an unavailable external system into a claimed pass.

| Area | Delivered behavior | Primary implementation | Evidence / boundary |
|---|---|---|---|
| Identity | Separate Users and People, unique link, lifecycle, invitations and signup approval | `app/identity`, `app/accounts`, `app/admin` | Route/security tests; PostgreSQL uniqueness test runs in CI |
| Authorization | Explicit capability policy, regional scope, Manager/Admin administration boundary, Viewer read-only breadth, pending/active/revoked grants, final-Admin protection | `app/auth/policy.py`, `app/accounts` | Account, route, and security tests; elevation concurrency is PostgreSQL-only |
| Sessions | Hash-only trusted devices, role-sensitive trust, epoch invalidation, fresh-primary timestamp | `app/auth/security.py`, `app/auth/service.py`, middleware | Account security and route tests |
| Passkeys | Bound, expiring, one-use registration/auth challenges; RP/origin verification; required user verification | `app/auth/factors.py`, `app/auth/factor_routes.py`, `app/static/passkeys.js` | Factor tests; real hardware ceremony remains deployment validation |
| TOTP | Encrypted inactive setup, confirmation, ±1-window verification, replay prevention, fresh-auth removal | `app/auth/factors.py`, factor routes/templates | Factor and login tests; no recovery codes |
| Roster authority | Stable workday, private draft, immutable publication, structured diff, safe history, row lock, snapshots | `app/rostering` | Diff/route tests and PostgreSQL concurrent-publish/rollback tests |
| Open positions | Eligibility, employee application, Manager draft selection, close-on-publication | `app/open_positions`, `app/positions` | Route and PostgreSQL uniqueness tests |
| Decline | Confirmed employee decline creates atomic immutable publication and detaches stale draft | roster/open-position services | Route tests plus PostgreSQL stale-draft test |
| Crew/capability | Scoped Crew View, Viewer oversight notes, lifecycle, groups, independent employee/Manager signals, preserved worked history | `app/crew`, `app/positions` | Route, policy, and capability-clear tests |
| Hours/read models | Shared multi-slot person/day participation, personal/regional fortnight totals, overnight, no break inference, NZ holidays | `app/rostering/participation.py`, `app/employee`, `app/hours` | Fixed multi-role/overnight/date/holiday tests |
| Offline | Per-user upcoming/own-Day cache, CSRF-free generated HTML, cache-backed timestamp, cross-user deletion, cross-month today-plus-three prefetch | service worker and employee JSON routes | Offline contract and route tests; physical offline revocation remains impossible |
| Notifications | Transactional outbox, encrypted subscriptions, prior/new audience union, deterministic reminders, skip-locked claims/leases, retry/deactivate | `app/notifications`, `python -m app.cli deliver-notifications` | Fixed-time/delivery tests; claim concurrency is PostgreSQL-only; real provider pending |
| Regional administration | Manager tracks/colours and scoped accounts; Admin region policy/lifecycle and global groups/positions | `app/catalog/routes.py`, `app/accounts` | Regional authority and directory privacy route tests |
| Schema | Append-only migrations through credential eligibility and notification claims; theme, holiday geography, and reminder columns originate in the foundation schema | `migrations/versions` | PostgreSQL-dialect SQL generation passed; current execution pending locally |
| Responsive UI | Server-rendered Employee/Manager/Viewer/Admin paths at four target widths, with theme/colour/console/picker assertions | templates/static CSS, `tests/browser` | 4 local Chromium tests passed using disposable SQLite; this is not PostgreSQL qualification |
| Deployment | Separate PostgreSQL 17 Compose/Portainer artifacts and migration-before-app startup | `compose.yaml`, deployment scripts/docs | Static/image checks in CI only; no local Docker execution |

## Migration chain additions in this completion pass

- `4c72a6f19d31`: account approval, role-grant lifecycle, and fresh-primary authentication state.
- `7a91d36e5b20`: server-bound WebAuthn challenges and encrypted TOTP factors.
- `ab24e50d17c4`: notification availability, per-subscription delivery, attempts, retry, and result state.
- `c8e451d10a77`: Admin-eligible primary credential marker, notification claim lease fields/index, and one-hour preference default correction. User theme, regional holiday geography, and the preference column already exist in the foundation schema.

## PostgreSQL-only assertions

The PostgreSQL suite deliberately covers simultaneous publication, transactional outbox rollback, simultaneous pending-grant activation, partial/unique identity and application constraints, stale-draft behavior during decline, and concurrent notification claims/expired-lease recovery. These are not reported as passed from the local SQLite suite.

## Independent review items

- Decide whether future collaborative editing requires one draft branch per Manager rather than the current single workday draft pointer.
- Design recovery-code issuance/rotation before making MFA mandatory for accounts without a second authenticator.
- Configure the production scheduler cadence and VAPID credentials, then validate delivery against real browser endpoints.
- Validate production RP/origin settings with real authenticators and production VAPID credentials with real endpoints.
- Rehearse backup restoration and application restart persistence on a disposable deployment target.
