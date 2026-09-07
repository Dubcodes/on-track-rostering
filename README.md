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

The operational continuation adds regional Crew View and crew management, employee base-position preferences, explicit Open-position applications with Manager draft selection, policy-driven authoritative decline, Admin account/device revocation, and an idempotent notification-event outbox. Push delivery and privilege-safe role mutation remain intentionally disabled until their security workflows are complete.

## Production deployment

The retained Compose stack is for the separate Docker/Portainer production server only. PostgreSQL is not published to that server's host, and the project, network, and volume are independent of Re-Deputy. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

See [docs/TESTING.md](docs/TESTING.md), [docs/SECURITY.md](docs/SECURITY.md), and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
