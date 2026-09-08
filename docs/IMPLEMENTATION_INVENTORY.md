# Implementation inventory

This inventory maps the delivered foundation to code, migrations, automated evidence, and remaining operational validation. “Implemented” means code and deterministic tests exist; it does not turn an unavailable external system into a claimed pass.

| Area | Delivered behavior | Primary implementation | Evidence / boundary |
|---|---|---|---|
| Identity | Separate Users and People, unique link, lifecycle, invitations and signup approval | `app/identity`, `app/accounts`, `app/admin` | Route/security tests; PostgreSQL uniqueness test runs in CI |
| Authorization | Explicit capability policy, regional scope, pending/active/revoked grants, final-Admin protection | `app/auth/policy.py`, `app/accounts` | Account security tests; elevation concurrency in PostgreSQL CI |
| Sessions | Hash-only trusted devices, role-sensitive trust, epoch invalidation, fresh-primary timestamp | `app/auth/security.py`, `app/auth/service.py`, middleware | Account security and route tests |
| Passkeys | Bound, expiring, one-use registration/auth challenges; RP/origin verification; required user verification | `app/auth/factors.py`, `app/auth/factor_routes.py`, `app/static/passkeys.js` | Factor tests; real hardware ceremony remains deployment validation |
| TOTP | Encrypted inactive setup, confirmation, ±1-window verification, replay prevention, fresh-auth removal | `app/auth/factors.py`, factor routes/templates | Factor and login tests; no recovery codes |
| Roster authority | Stable workday, private draft, immutable publication, base check, row lock, snapshot/history | `app/rostering` | Route tests and PostgreSQL concurrent-publish/rollback tests |
| Open positions | Eligibility, employee application, Manager draft selection, close-on-publication | `app/open_positions`, `app/positions` | Route and PostgreSQL uniqueness tests |
| Decline | Confirmed employee decline creates atomic immutable publication and detaches stale draft | roster/open-position services | Route tests plus PostgreSQL stale-draft test |
| Crew/capability | Scoped Crew View, normal/private note policy, lifecycle, groups, preference precedence | `app/crew`, `app/positions` | Route and policy tests |
| Hours | Personal and region-scoped fortnight totals, anchor, overnight, no break inference, markers | `app/hours` | Hours unit/route tests |
| Offline | Per-user cache, exact Month/Day scope, CSRF stripping, cross-user deletion, next-three-workday prefetch | service worker and employee JSON routes | Offline contract tests; physical offline revocation remains impossible |
| Notifications | Transactional outbox, encrypted subscriptions, preferences, audience expansion, VAPID worker, retry/deactivate | `app/notifications`, `python -m app.cli deliver-notifications` | Delivery tests with injected sender; real provider validation pending |
| Schema | Append-only migrations through WebAuthn/TOTP and delivery state | `migrations/versions` | Offline PostgreSQL SQL generation; actual upgrades in CI |
| Responsive UI | Server-rendered login/employee/management/Admin paths at four target widths | templates/static CSS, `tests/browser` | Chromium suite in CI; local run pending |
| Deployment | Separate PostgreSQL 17 Compose/Portainer artifacts and migration-before-app startup | `compose.yaml`, deployment scripts/docs | Static/image checks in CI only; no local Docker execution |

## Migration chain additions in this completion pass

- `4c72a6f19d31`: account approval, role-grant lifecycle, and fresh-primary authentication state.
- `7a91d36e5b20`: server-bound WebAuthn challenges and encrypted TOTP factors.
- `ab24e50d17c4`: notification availability, per-subscription delivery, attempts, retry, and result state.

## PostgreSQL-only assertions

The CI suite deliberately uses PostgreSQL for simultaneous publication, transactional outbox rollback, simultaneous pending-grant activation, partial/unique identity and application constraints, and stale-draft behavior during decline. These are not reported as passed from the local SQLite suite.

## Independent review items

- Decide whether future collaborative editing requires one draft branch per Manager rather than the current single workday draft pointer.
- Design recovery-code issuance/rotation before making MFA mandatory for accounts without a second authenticator.
- Add a scheduler that produces reminder events if reminder preferences become an operational promise.
- Validate production RP/origin settings with real authenticators and production VAPID credentials with real endpoints.
- Rehearse backup restoration and application restart persistence on a disposable deployment target.
