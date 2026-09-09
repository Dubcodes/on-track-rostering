# Architecture

## Runtime

One FastAPI application renders Jinja pages and serves small JSON read models. SQLAlchemy 2 talks to PostgreSQL 17; Alembic owns schema changes. On the Windows development machine, FastAPI, Alembic, tests, and tooling run directly under Python against a native/local or explicitly configured remote PostgreSQL database supplied through `DATABASE_URL`. Docker is excluded from local development and qualification. The retained Compose stack runs the app and internal database only on the separate Docker/Portainer deployment server. Vanilla JavaScript adds view switching, sequential next-day prefetch, PWA registration, and read-only offline cache behavior.

## Bounded modules

- `app/core`: configuration, database/session factory, enums, time and NZ holiday calculations.
- `app/auth`: Argon2id credentials, WebAuthn ceremonies, encrypted/replay-resistant TOTP, login throttling, trusted devices, fresh authentication, trusted-proxy network authority, CSRF/origin checks, invitation activation, and the central policy layer.
- `app/accounts`: Manager/Admin signup approval, explicit person linking, and pending/active/revoked privilege administration.
- `app/identity`: users, people, one-to-one links, scoped role grants, invitations, pending signup, trusted devices, and passkey public-key storage.
- `app/catalog`: Admin global/region policy administration plus Manager-scoped regional tracks/colours; crew groups, base positions, and vehicles remain global catalogues.
- `app/rostering`: operations, stable workday identity, draft/published revisions, structured publication diff, authoritative person/day participation, event slots, assignments, programme source/override fields, travel legs, allowances, publish transaction, and management routes.
- `app/open_positions`: eligible published vacancy reads, explicit employee applications, stale-state checks, and Manager selection into a draft.
- `app/crew`: scoped regional crew search, creation, archive lifecycle, group membership, and Manager capability decisions.
- `app/positions`: effective capability resolution with explicit provenance and precedence.
- `app/employee`: intentionally small Month/Day read models and Settings views.
- `app/hours`: employee and region-scoped management fortnight totals from current published spans.
- `app/audit`: redacted security audit and human publication history.
- `app/notifications`: encrypted device subscriptions, preferences, idempotent authoritative-event outbox, recipient expansion, and per-subscription Web Push delivery/retry state.
- `app/admin`: bootstrap configuration, account activation/disable, device revocation, and invitation creation/revocation.

`app/main.py` only assembles middleware, routers, static content, and health endpoints.

## Identity and authorization

A `User` can exist without a `Person`; a `Person` can be rostered before an account exists. `UserPersonLink` is one-to-one. `RoleGrant` carries a role and optional region scope. `app/auth/policy.py` separately answers visibility and mutation questions; it does not sort roles into a numeric hierarchy.

Employees/Viewers with regional scope can use Crew View for that region. Contractors gain access only through their own published assignment. A cross-region assignment grants event-specific access, not permanent regional membership. Manager/Sub-Manager can maintain rosters only in assigned regions. Regional catalogue and account administration requires Manager; Sub-Manager remains operational. Admin is global.

## Authoritative publication

`Workday` is stable identity. `WorkdayRevision` is either DRAFT or PUBLISHED. `Workday.current_published_revision_id` is the only employee read pointer; `current_draft_revision_id` is private management state. Editing a published day clones its snapshot and stable assignment `slot_key` values. Preview and safe human history share one structured diff covering day and assignment fields; note summaries never contain note text. Publishing locks the workday, verifies the draft was based on the still-current publication, promotes it atomically, learns base-position worked history, and records audit/human history.

An Open application references the published revision and stable assignment slot. Manager selection edits the corresponding draft slot; publication accepts the selected applicant and closes competing applications. Employee decline locks the workday and creates a fresh immutable publication directly, detaching any now-stale Manager draft while preserving it for recovery.

## Read path

Personal Month reads use an assignment-filtered query for ordinary Employees/Contractors and select only date, category, track label/colour, combined person/day role/time/status, cross-region state, and holiday marker. Explicit Crew View performs the authorized regional roster read. Manager/Viewer/Admin Month access may remain broad. Day reads expose normal day notes and policy-filtered assignments. Private assignment notes reach the assigned person and scoped Manager/Sub-Manager/Viewer oversight roles.

The service worker caches only the purpose-built authenticated upcoming-work feed and own critical Day JSON, partitions cache names by returned user namespace, deletes other namespaces before changing the active marker, and performs no offline mutation. The page date-sorts and sequentially prefetches today when rostered plus the next three actual workdays across month boundaries. A saved timestamp is surfaced only after the service worker has completed the corresponding cache write. Push events display only a generic summary and link back to an authoritative online Day read.

## Delivery worker

Authoritative transactions add deterministic `NotificationEvent` keys but never call a push provider. `python -m app.cli deliver-notifications` generates deterministic two-day, night-before, and one-hour events from current published person/day starts, then claims due rows with PostgreSQL `FOR UPDATE SKIP LOCKED`. Five-minute leases recover abandoned claims. Publication audience is the union of prior and new assignees. A unique event/subscription delivery row makes completed delivery idempotent. HTTP 404/410 deactivates an endpoint; transient failures back off and stop at the configured attempt limit.
