# On Track Rostering

On Track is a standalone, authoritative race-production rostering system. It uses FastAPI, server-rendered Jinja, PostgreSQL 17, SQLAlchemy, Alembic, and a small PWA shell.

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

**Administration** holds global settings and system functions. **Master Data** holds Regions, Tracks, Positions, Vehicles, and related master data; **Crew** administers People and regional crew; and **Accounts** administers User access, linking, and invitations. Managers administer their own Regions according to policy. A Manager or Sub-Manager uses **Build** to create a private draft, add position slots, preview it, and publish it. Authorized employees then see only the published revision in Month and Day views.

The operational continuation adds regional Crew View and crew management, employee base-position preferences, explicit Open-position applications with Manager draft selection, policy-driven authoritative decline, privilege-safe account approval and role grants, WebAuthn passkeys, optional encrypted TOTP, fortnight hours, isolated offline roster caches, and an idempotent Web Push delivery worker.

Public racing sources are planning evidence only. On Track automatically refreshes the broad racing calendars on a bounded approximately 48-hour cadence and refreshes programme detail more frequently as an upcoming meeting approaches. Disabling a racing provider disables that provider's automatic racing-data requests. Manual rostering remains available through every source failure; a Manager can apply current programme facts to a private draft, while published snapshots are never rewritten. Official Track maps are cached under `ONTRACK_DATA_DIR`, with a scoped manual override that takes precedence; their refresh lifecycle is independent of the racing-provider toggle.

The Data Import workspace also has an isolated one-time Deputy capture mode. It reads only published schedule rows from a manually supplied text capture, creates People without accounts, skips unresolved operational locations, and never imports authentication, payroll, timesheet, or break data.

WebAuthn defaults to `localhost` and `http://localhost:8000`; use that exact origin in local development or update both RP/origin settings consistently. Production requires HTTPS and deployment-specific RP/origin values. Web Push remains visibly unavailable until real VAPID public/private keys are supplied in environment configuration. Process its outbox independently of the web request:

```powershell
.\.venv\Scripts\python.exe -m app.cli deliver-notifications --limit 50
```

For development-only scheduler observation, run `python -m app.scheduler` in a separate terminal. The deployment Compose files run the same module as a singleton scheduler service. Do not run it against production from a development workstation.

## Deployment

The retained production and isolated-staging Compose files are for the separate Docker/Portainer server only. PostgreSQL is not published to that server's host, and every project, network, and volume is independent of Re-Deputy. Back up both the PostgreSQL volume and the application-data volume: the latter contains Track map images while the database contains their hashes and metadata. During active development staging may follow a CI-qualified `main`; production promotion freezes one exact qualified revision. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

See [docs/TESTING.md](docs/TESTING.md), [docs/SECURITY.md](docs/SECURITY.md), and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
