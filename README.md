# On Track Rostering

On Track is a standalone, authoritative race-production rostering system. It uses FastAPI, server-rendered Jinja, PostgreSQL 17, SQLAlchemy, Alembic, and a small PWA shell.

New development conversations should start with [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md), then follow its links to the current domain, architecture, implementation, testing, and staging records.

## Windows local development

Install or select a native/local PostgreSQL server, or obtain an explicitly configured remote development PostgreSQL database. Then run the application directly under Windows/Python:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.windows.example .env
# Edit .env and set DATABASE_URL to the dedicated development PostgreSQL database.
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.cli create-admin
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

Open `http://127.0.0.1:8000` and sign in. Do not use Docker or Docker Desktop on the Windows development machine. If no usable PostgreSQL instance is configured, PostgreSQL integration qualification remains pending; SQLite unit tests do not replace it.

**Settings** is personal. From there, authorized staff open **Management** for the normal operational workflow: **Build**, **Crew**, **Leave & Unavailability**, **Team Hours**, and other permitted tools. **Crew** is the normal Person and linked-account management surface. **Accounts** remains an advanced recovery and reconciliation surface rather than the starting point for staff administration. **Administration** holds global settings and system functions, while **Master Data** holds Regions, Tracks, Positions, Vehicles, and related records. Managers administer their own Regions according to policy. A Manager or Sub-Manager uses **Build** to create a private draft, add position slots, preview it, and publish it.

Personal **Month** is role-independent planning: it shows the linked Person's own current published assignments, applicable Open Positions, and Primary-Region racing events only after that Region's latest published roster horizon. Management authority does not broaden Personal Month into an all-crew calendar; authorized regional all-crew planning belongs in **Crew**. Public racing information remains planning evidence only, and manual roster entry remains authoritative and available when external sources fail.

The operational continuation adds regional Crew View and crew management, employee base-position preferences, explicit Open-position applications with Manager draft selection, policy-driven authoritative decline, privilege-safe account approval and role grants, WebAuthn passkeys, encrypted TOTP with single-use recovery codes, fortnight hours, isolated offline roster caches, and an idempotent Web Push delivery worker.

Public racing sources are planning evidence only. On Track automatically refreshes the broad Love Racing and HRNZ calendars on a bounded approximately 48-hour cadence and refreshes programme detail more frequently as an upcoming meeting approaches. HRNZ public race sources can return HTTP 403 and Love Racing can present a bot challenge; these are reported as source failures and are never bypassed. Disabling a racing provider disables that provider's automatic racing-data requests. Manual rostering remains available through every source failure; a Manager can apply current programme facts to a private draft, while published snapshots are never rewritten. Official Love Racing Track maps are cached under `ONTRACK_DATA_DIR`, with a scoped manual override that takes precedence; ordinary reads verify the local file without loading the complete image into application memory, and the independent 90-day refresh retains the last fully validated automatic cache on failure.

The Data Import workspace also has an isolated one-time Deputy capture mode. It reads only published schedule rows from a manually supplied text capture, creates People without accounts, skips unresolved operational locations, and never imports authentication, payroll, timesheet, or break data. The real-data path is deliberately staged: neutral structural master data first, human mapping/convergence review, transition Preview, atomic Apply, Person metadata completion, then separately generated setup links. See [docs/REAL_DATA_ONBOARDING.md](docs/REAL_DATA_ONBOARDING.md).

WebAuthn defaults to `localhost` and `http://localhost:8000`; use that exact origin in local development or update both RP/origin settings consistently. Production requires HTTPS and deployment-specific RP/origin values. Web Push remains visibly unavailable until real VAPID public/private keys are supplied in environment configuration. The application scheduler automatically generates and delivers notifications; this command remains available for diagnostics or a deliberate manual run:

```powershell
.\.venv\Scripts\python.exe -m app.cli deliver-notifications --limit 50
```

For development-only scheduler observation, run `python -m app.scheduler` in a separate terminal. The deployment Compose files run the same module as a singleton scheduler service at a five-minute cadence while retaining independent racing and map due checks. Do not run it against production from a development workstation.

## Deployment

The retained production and isolated-staging Compose files are for the separate Docker/Portainer server only. Portainer reads stack configuration from Git but pulls one CI-qualified GHCR application image for both web and scheduler services; it never builds source. PostgreSQL is not published to that server's host, and every project, network, and volume is independent of Re-Deputy. Back up both the PostgreSQL database and the application-data volume: the latter contains Track map images and custom branding assets, while the database contains authoritative application records and map metadata. Staging may pull the qualified `:staging` image; production promotion pins one immutable `sha-<commit>` image. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and the [backup/restore runbook](docs/BACKUP_RESTORE.md).

See [docs/TESTING.md](docs/TESTING.md), [docs/SECURITY.md](docs/SECURITY.md), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), and the separately authorized [clean staging reset/re-import runbook](docs/STAGING_RESET_REIMPORT.md).
