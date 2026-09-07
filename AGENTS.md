# On Track agent rules

Read this before changing the repository.

- On Track is the authoritative roster. Manual entry must always work and external racing data must never block rostering.
- Never modify, configure, deploy, or connect to Re-Deputy. It is a read-only behavioral reference only. No Deputy dependency, credential, identifier, database, secret, volume, or production file belongs here.
- This Windows development machine is Docker-free. Never run, start, repair, install, or configure Docker or Docker Desktop here. Keep Compose files solely for deployment on the separate Docker/Portainer server.
- Production Portainer deploys the GitHub `main` branch. Keep `main` deployable, use repository-relative Linux deployment paths, never commit secrets, and never push without explicit user authorization.
- Run FastAPI, Alembic, tests, and tooling directly under Windows/Python. Use PostgreSQL through `DATABASE_URL`, backed by a native/local installation or an explicitly configured remote development instance.
- PostgreSQL and Alembic are required. SQLite is acceptable only for narrow deterministic unit tests; it is never PostgreSQL integration qualification. If no usable PostgreSQL URL is configured, report PostgreSQL checks as pending rather than substituting SQLite.
- Keep the application server-rendered and operationally simple: FastAPI, Jinja, SQLAlchemy, PostgreSQL, modest vanilla JS, one deployable app.
- Keep domain modules bounded. Do not grow monolithic `main.py` or `database.py` files.
- Published workdays are immutable snapshots. Employee reads use only `current_published_revision_id`; edits occur in a private draft and become visible only through Preview → atomic Publish.
- Treat committed Alembic revisions as append-only production artifacts unless deployment history proves a rewrite is safe. Open applications bind to a published revision/slot; selection changes a draft. Employee decline creates a new immutable publication atomically.
- Enforce authorization server-side through `app/auth/policy.py`. Roles are capabilities plus independent region visibility, not a numeric hierarchy. Accounts and rosterable people are separate identities.
- Never expose private assignment notes to unrelated crew or Viewers. Public signup grants no role or roster access. Never log credentials, tokens, cookies, hashes, or MFA secrets.
- Six numeric digits is the normal minimum credential; Admin requires 8+ digits or a 12+ character password. Privilege elevation must invalidate/reclassify trusted sessions and require fresh authentication.
- No payroll, leave system, or automatic unpaid-break deduction. Allowances and public holidays are informational.
- Employee Month/Day APIs must stay purpose-built and small. Offline v1 is read-only and user-namespaced.
- People may work across regions and disciplines. Base positions own capability history; numbered event slots do not become separate qualifications.
- Prefer archive lifecycles over hard deletion of referenced master data.
- Run `python scripts/release_gate.py` from the project virtual environment and document exactly which PostgreSQL checks ran. Do not include Docker execution in local qualification.
