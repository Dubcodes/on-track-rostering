# Project handover

This is the starting point for a new On Track development conversation. Read `AGENTS.md` first, then use this file to locate the current source of truth. Historical implementation journals are useful evidence but do not override current code, domain rules, or explicit decisions.

## Purpose and non-negotiable boundaries

On Track is the authoritative race-production roster. Manual entry must always work; Love Racing and HRNZ data are optional planning evidence and never rewrite a published roster automatically. The product is not payroll or an HR leave system. A rosterable `Person` and an authenticated `User` are separate identities.

Development is Windows-native Python against PostgreSQL. Do not use Docker locally and never connect to, configure, modify, or deploy Re-Deputy. Compose exists only for the separate Portainer host. Published Workday revisions are immutable; management changes flow through private Draft → Preview → atomic Publish.

## Architecture and important modules

- FastAPI/Jinja/SQLAlchemy/Alembic/PostgreSQL provide one server-rendered application; modest vanilla JavaScript supplies interactive Builder and PWA behavior.
- `app/auth/policy.py` is the server-side authority. Roles are explicit capabilities plus independent Region scope, not a numeric hierarchy.
- `app/rostering` owns Workdays, revisions, publication, timing, travel and participation. `app/static/builder.js` mirrors immediate Builder interactions; the server remains authoritative.
- `app/crew`, `app/accounts`, and `app/identity` separate normal Person-centric administration from advanced account recovery/security.
- `app/external_calendar` normalizes provider evidence. `app/track_maps` owns trusted Love Racing map discovery and persistent files under `/app/data`.
- `app.scheduler` uses a PostgreSQL advisory lock and independently runs racing/programme/map due work, Contractor maintenance, and notification generation/delivery.
- `app/transition_import` is a finite manually supplied migration boundary, never a live Deputy integration.

Detailed design: [ARCHITECTURE.md](ARCHITECTURE.md), [DOMAIN_RULES.md](DOMAIN_RULES.md), [DECISIONS.md](DECISIONS.md), and [SECURITY.md](SECURITY.md).

## Implemented and working

The current code includes immutable roster publication and history; multi-Position People with one synchronized travel plan; Race Day timing and generated Travel; Open Positions and decline; rescheduling; Person/day Hours; leave advisories; regional Crew/Manager workflows; invitation, Contractor, passkey, TOTP and credential-recovery lifecycles; notices and error reports; notifications from the scheduler; provider-neutral Love Racing/HRNZ evidence; trusted persistent Track maps; neutral master import; and bounded transition capture.

Builder Vehicle capacity counts final distinct People. A one-for-one replacement does not prompt unless the replaced Person remains in that Vehicle through another Position; genuine additions retain Kick last, Keep and Cancel plus server validation and Preview warnings. Track-map upload/refresh canonicalizes and percent-encodes trusted official paths, validates Love Racing's bounded `Common/Image.ashx` form and nested map path, and still enforces trusted host, redirect, type, content, dimensions, size and hashes. A blocked Love Racing course page still permits its known official image fallback; ordinary page checks use metadata, file size and a bounded header before streaming the file. Master data exposes specific sanitized source/image/storage/missing-cache status and manual refresh, while matching published Days show the valid effective map at the bottom with a larger mobile-pan/zoom view.

Online Sources maps evidence identities to one canonical Track UUID and inherits that Track's Region; it no longer presents a competing map editor. Routine source and Track/map saves are enhanced in place without adding history entries, while protected server POST/redirect behavior remains the fallback. Aurora is the default only for new, anonymous, unset or invalid theme state; valid saved preferences remain unchanged.

The complete feature ledger and evidence boundary are in [IMPLEMENTATION_INVENTORY.md](IMPLEMENTATION_INVENTORY.md) and [BUILD_STATUS.md](BUILD_STATUS.md).

Accounts/Crew Phase A also provides reusable immediate/debounced GET filters, valid direct-account defaults with safe validation recovery, workflow-local fresh authentication without secret storage or POST replay, meaningful linked/unlinked success destinations, and explicit one-time invitation copy/status/return handling. The proposed Phase B Person-centric workflow and its still-open product decisions are recorded in [ACCOUNTS_CREW_UX.md](ACCOUNTS_CREW_UX.md); the navigation restructure is not implemented.

## Unfinished work and explicit decisions still open

- The private staging stack is initialized and reachable with a bootstrap Admin, but the current candidate is not thereby deployed or operationally qualified.
- A separately authorized clean staging reset/re-import, canonical real-data correction, full staging matrix, physical-device security/push checks, persistence/restart checks, and backup/restore rehearsal remain.
- Production promotion, secrets/RP identity freeze, monitoring/alerting and operational governance remain pending.
- Product decisions remain open for Crew-Group-scoped Manager authority, global read-only Viewer accounts, full role administration in Crew, and whether missing truck-driver designation blocks Save & Preview or only Publish.
- The Phase B Accounts/Crew navigation and route-ownership proposal still requires approval, including the treatment of unlinked User detail, signup/bulk-onboarding ownership, and which grant controls remain exclusively advanced.
- Canonical real-data conversion must explicitly resolve imported Vehicle-as-Position history. Configurable Open Position auto-accept and IV configuration are not implemented.

Do not describe any of these as complete. See [REAL_DATA_ONBOARDING.md](REAL_DATA_ONBOARDING.md), [STAGING_RESET_REIMPORT.md](STAGING_RESET_REIMPORT.md), and [STAGING_QUALIFICATION.md](STAGING_QUALIFICATION.md).

## Racing and map limitations

Love Racing can return a bot challenge; HRNZ public race endpoints have returned HTTP 403 to the service client. On Track reports component health and retains last-good evidence without bypassing source controls. A map-page challenge can still use the separately trusted official image fallback, but upstream image blocking remains an honest source failure. HRNZ club text is not automatically a physical Track. TAB is not configured. Automatic maps cover only the code-owned Love Racing course allowlist; successful maps refresh every 90 days and failed attempts retry after one day. Manual override remains authoritative until deliberate reset.

See [RACING_SOURCE_DISCOVERY.md](RACING_SOURCE_DISCOVERY.md) for the adapter contract and dated source observations.

## Development and release workflow

1. Verify a clean worktree, expected `HEAD == origin/main`, and preserve unrelated stashes.
2. Make focused changes and tests. Run `python scripts/release_gate.py` from `.venv`; without `ONTRACK_TEST_DATABASE_URL` or browser opt-in, report those checks pending rather than substituting SQLite.
3. Review the full diff and commit logically. Never push without explicit user authorization.
4. Fetch and guard live `origin/main`, push normally without history rewriting, then monitor only the exact-head GitHub `release-gate`.
5. A qualified source head requires PostgreSQL tests, Playwright, dependency audit, production/staging Compose contracts, embedded build-SHA verification, immutable GHCR publication, and the conditional `:staging` publication.

Current exact evidence is recorded in [BUILD_STATUS.md](BUILD_STATUS.md) and [TESTING.md](TESTING.md). CI publication is not a Portainer deployment.

## Staging-to-production path

Choose and record one exact qualified SHA, gate, immutable image and digest. Pin that immutable image for the destructive clean-staging rehearsal; do not use mutable `:staging` for that cycle. Preserve staging first, reset only named staging resources under explicit authority, migrate/bootstrap, perform structural and transition imports, reconcile real data, then execute every applicable row of the staging matrix with direct evidence. Only a candidate that passes both exact-head CI and deliberate staging qualification is eligible for a separately authorized production promotion.

Deployment and recovery detail: [DEPLOYMENT.md](DEPLOYMENT.md) and [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
