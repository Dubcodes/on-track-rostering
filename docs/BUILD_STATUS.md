# Build status — 2026-09-13

## Current result

The Foundation is running in a real, private, isolated staging deployment at `https://ontrackrostering.dubcodesmedia.com`. The visibly deployed build is `ee8af0d5dcd215c6df02f113ef8c8bf43ee936f2`. PostgreSQL initialized successfully, the bootstrap Admin was created through the CLI, HTTPS access through the standard Cloudflare Tunnel works, password/PIN login succeeds, the secure authenticated session persists across navigation, and `/month` and `/settings` render against PostgreSQL-backed data.

This is verified deployment smoke evidence, not a completed functional staging qualification and not production-readiness approval. The operator matrix in `docs/STAGING_QUALIFICATION.md` records the remaining real-browser, role, workflow, restart, privacy, offline, notification, log, and recovery checks. Any source revision newer than the visibly deployed SHA still requires its own exact-head CI pass and a manual staging redeploy before its behavior is live.

## Staging findings closed in `ee8af0d`

Real staging exposed two integration defects. Standard Cloudflare Tunnel requests preserved the public `Host` and supplied `X-Forwarded-For`/`X-Forwarded-Proto` without requiring `X-Forwarded-Host`; the application had incorrectly required all four headers. Pydantic Settings also attempted JSON decoding of tuple-valued environment fields before the intended comma-separated validator ran.

`ee8af0d` permits a single valid `X-Forwarded-Host` when supplied and otherwise uses the single ordinary `Host`, only after the direct peer matches an explicit trusted-proxy CIDR and the forwarded client/protocol values validate. Ambiguous, comma-joined, malformed, and port-conflicting forwarding still fails safely. `NoDecode` now preserves raw comma-separated allowed-host and proxy-CIDR environment values for typed tuple validation.

Exact-head GitHub release-gate run `34715787713` passed for `ee8af0d`: PostgreSQL 17.6 applied the full Alembic chain through `e72a19c5f40b`, a second `alembic upgrade head` was clean, the PostgreSQL-backed suite reported **82 passed with no skips**, and all **6 Playwright tests** passed. Python compile, Ruff, PostgreSQL-dialect SQL generation, `pip check`, `pip-audit --local`, JavaScript syntax, production/staging Compose validation, and the production image build also passed.

## Foundation delivered

- Standalone FastAPI/Jinja/SQLAlchemy application with PostgreSQL 17, append-only Alembic migrations, and no Re-Deputy runtime dependency.
- Separate Users and rosterable People; scoped capability roles; final-Admin protection; pending-grant activation; trusted-device, fresh-auth, passkey, and optional TOTP controls.
- Stable Workday identity, immutable published snapshots, private optimistic-versioned shared Manager drafts, structured Preview/diff/history, and atomic Publish.
- Purpose-built employee Month/Day reads, event-only Contractor and private-note filtering, regional Crew View, Open-position applications, policy-driven immutable decline, and current-publication hours.
- User-namespaced read-only offline data and an idempotent notification outbox/delivery worker separated from roster transactions.
- Persisted Admin-only global branding and public-signup policy.
- Separate production and staging deployment artifacts. The live staging app and database use their own service names, volume, network, credentials, and data.

## Current automated evidence boundaries

- CI PostgreSQL coverage includes simultaneous first-draft creation, stale detail and assignment mutations, stale editor state after Publish, exact `lock_version` conflict behavior, concurrent Publish/outbox behavior, constraints, decline immutability, and notification claiming.
- CI Playwright covers 1280/430/375/320 layouts and offline personal-Day cases, but automated browser evidence is not a substitute for the real-device/operator matrix.
- Local PostgreSQL remains pending when no disposable `ONTRACK_TEST_DATABASE_URL` is configured. SQLite deterministic tests are not presented as PostgreSQL qualification.
- Docker is not run on the Windows development machine. Compose and image evidence comes from GitHub CI.

## Explicitly pending or deferred

- Full real functional staging qualification is pending. In particular: real passkey and TOTP ceremonies; the complete role/direct-request matrix; roster lifecycle, stale-write, Open-position, decline, privacy, hours, physical offline, responsive-device, restart/persistence, and log review.
- Real Web Push delivery remains pending unless staging-specific VAPID credentials are deliberately configured. Reminder delivery requires `python -m app.cli deliver-notifications` under a durable server-side scheduler.
- Recovery codes are not implemented.
- Production backup/restore rehearsal, production secrets/hostname/RP identity/VAPID, monitoring, scheduler, release controls, and immutable release artifact remain production-promotion work.
- Operations/Travel, Accommodation, provider ingest, full vehicle operations, abandoned/rescheduled workflow, management-only crew-note expansion, logo upload, and major recovery redesign remain outside the current Foundation pass.

## Known architecture constraint

Each Workday owns one shared `current_draft_revision_id`; independent per-Manager branches are not represented. Row locking plus exact optimistic versions prevent silent last-writer overwrites. Stale drafts detached by authoritative decline remain preserved for recovery.

## Release rule

During active development the isolated staging stack may intentionally follow `main`, but each redeploy follows local checks and a successful exact-head GitHub gate. Production promotion is a separate freeze: complete the real staging matrix, select one known-green exact SHA, and use an immutable tag and/or immutable image. A green CI run alone does not prove a live redeploy or production readiness.
