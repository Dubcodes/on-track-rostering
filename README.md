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

The Admin page creates regions, tracks, base positions, people, linked accounts, and one-time invitations. A Manager or Sub-Manager uses **Build** to create a private draft, add position slots, preview it, and publish it. Authorized employees then see only the published revision in Month and Day views.

The operational continuation adds regional Crew View and crew management, employee base-position preferences, explicit Open-position applications with Manager draft selection, policy-driven authoritative decline, privilege-safe account approval and role grants, WebAuthn passkeys, optional encrypted TOTP, fortnight hours, isolated offline roster caches, and an idempotent Web Push delivery worker.

WebAuthn defaults to `localhost` and `http://localhost:8000`; use that exact origin in local development or update both RP/origin settings consistently. Production requires HTTPS and deployment-specific RP/origin values. Web Push remains visibly unavailable until real VAPID public/private keys are supplied in environment configuration. Process its outbox independently of the web request:

```powershell
.\.venv\Scripts\python.exe -m app.cli deliver-notifications --limit 50
```

## Production deployment

The retained Compose stack is for the separate Docker/Portainer production server only. PostgreSQL is not published to that server's host, and the project, network, and volume are independent of Re-Deputy. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

See [docs/TESTING.md](docs/TESTING.md), [docs/SECURITY.md](docs/SECURITY.md), and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
