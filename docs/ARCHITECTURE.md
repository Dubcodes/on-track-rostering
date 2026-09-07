# Architecture

## Runtime

One FastAPI application renders Jinja pages and serves small JSON read models. SQLAlchemy 2 talks to PostgreSQL 17; Alembic owns schema changes. On the Windows development machine, FastAPI, Alembic, tests, and tooling run directly under Python against a native/local or explicitly configured remote PostgreSQL database supplied through `DATABASE_URL`. Docker is excluded from local development and qualification. The retained Compose stack runs the app and internal database only on the separate Docker/Portainer deployment server. Vanilla JavaScript adds view switching, sequential next-day prefetch, PWA registration, and read-only offline cache behavior.

## Bounded modules

- `app/core`: configuration, database/session factory, enums, time and NZ holiday calculations.
- `app/auth`: Argon2id credentials, login throttling, trusted devices, CSRF/origin checks, middleware, invitation activation, and the central policy layer.
- `app/identity`: users, people, one-to-one links, scoped role grants, invitations, pending signup, trusted devices, and passkey public-key storage.
- `app/catalog`: editable regions, tracks/colours, crew groups, base positions, and vehicles.
- `app/rostering`: operations, stable workday identity, draft/published revisions, event slots, assignments, programme source/override fields, travel legs, allowances, publish transaction, and management routes.
- `app/open_positions`: eligible published vacancy reads, explicit employee applications, stale-state checks, and Manager selection into a draft.
- `app/crew`: scoped regional crew search, creation, archive lifecycle, group membership, and Manager capability decisions.
- `app/positions`: effective capability resolution with explicit provenance and precedence.
- `app/employee`: intentionally small Month/Day read models and views.
- `app/audit`: redacted security audit and human publication history.
- `app/notifications`: device subscriptions, preferences, and an idempotent authoritative-event outbox; push delivery is deferred.
- `app/admin`: bootstrap configuration, account activation/disable, device revocation, pending-signup rejection, and invitation creation/revocation. Role mutation and signup approval remain deferred.

`app/main.py` only assembles middleware, routers, static content, and health endpoints.

## Identity and authorization

A `User` can exist without a `Person`; a `Person` can be rostered before an account exists. `UserPersonLink` is one-to-one. `RoleGrant` carries a role and optional region scope. `app/auth/policy.py` separately answers visibility and mutation questions; it does not sort roles into a numeric hierarchy.

Employees/Viewers with regional scope can use Crew View for that region. Contractors gain access only through their own published assignment. A cross-region assignment grants event-specific access, not permanent regional membership. Manager/Sub-Manager can maintain rosters only in assigned regions. Admin is global.

## Authoritative publication

`Workday` is stable identity. `WorkdayRevision` is either DRAFT or PUBLISHED. `Workday.current_published_revision_id` is the only employee read pointer; `current_draft_revision_id` is private management state. Editing a published day clones its snapshot and stable assignment `slot_key` values. Publishing locks the workday, verifies the draft was based on the still-current publication, promotes it atomically, learns base-position worked history, and records audit/human history.

An Open application references the published revision and stable assignment slot. Manager selection edits the corresponding draft slot; publication accepts the selected applicant and closes competing applications. Employee decline locks the workday and creates a fresh immutable publication directly, detaching any now-stale Manager draft while preserving it for recovery.

## Read path

Personal Month reads use an assignment-filtered query for ordinary Employees/Contractors and select only date, category, track label/colour, own role/time/status, cross-region state, and holiday marker. Explicit Crew View performs the authorized regional roster read. Manager/Viewer/Admin Month access may remain broad. Day reads expose normal day notes and policy-filtered assignments. Private assignment notes reach only the assigned person and operational management; Viewer breadth does not imply note privilege.

The service worker caches only authenticated Month/Day JSON, partitions cache names by returned user namespace, and performs no offline mutation. The page prefetches at most three visible workdays sequentially.
