# Testing

Create the Windows development environment and run directly under Python:

```powershell
.\.venv\Scripts\python.exe scripts\release_gate.py
```

The deterministic gate compiles Python, lints app/migrations/scripts/tests, runs pytest, checks installed dependencies, audits them for known vulnerabilities, checks application and service-worker JavaScript syntax, and renders every Jinja template with autoescape enabled. Set `ONTRACK_SKIP_DEPENDENCY_AUDIT=1` only when advisory network access is unavailable; the result is then explicitly pending rather than passed.

The unit suite covers credential minimums, safe redirects, role/scope separation, base-position eligibility precedence, published-history learning, draft isolation, Month/Crew/Day reads, private-note filtering, Open application deduplication and selection, atomic policy-driven decline snapshots, notification outbox creation, overnight/no-deduction hours, national holidays, and template escaping. SQLite is used only for these narrow deterministic tests. It is not an application runtime, integration-test substitute, or PostgreSQL qualification.

For real PostgreSQL migration checks, point the gate at a blank disposable database:

```powershell
$env:ONTRACK_TEST_DATABASE_URL='postgresql+psycopg://ontrack:test@localhost:5432/ontrack_test'
.\.venv\Scripts\python.exe scripts\release_gate.py
```

`ONTRACK_TEST_DATABASE_URL` must identify a blank or disposable PostgreSQL test database, never a production database. The runner passes it to the application as `DATABASE_URL` and upgrades twice to prove migration idempotence. If the variable is absent, the gate reports PostgreSQL integration as pending and does not fall back to SQLite.

Additional local qualification:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\pip-audit.exe --local
```

With the disposable PostgreSQL URL configured, run `alembic upgrade head` twice, verify exactly five regions/four groups/no people/no workdays, bootstrap an Admin, execute the Manager → Publish → Employee route flow, restart the Windows application process, and verify persistence and `/health/ready`.

Docker and Docker Desktop must not be used, started, repaired, installed, or configured on the Windows development machine. Compose artifacts remain maintained for qualification and deployment on the separate Docker/Portainer server.

GitHub Actions is the intended real-PostgreSQL and Docker qualification environment. Its release gate provisions PostgreSQL 17, applies Alembic twice, runs the Windows-equivalent code checks, audits dependencies, statically validates Compose, and builds the production image. A green CI run is still not evidence of an actual Portainer rollout.

Responsive manual/browser qualification targets widths 1280, 430, 375, and 320 for login, Month, Day, Admin, builder, and preview. Confirm no horizontal overflow; calendar/list switching; keyboard selection; note visibility; public-holiday focus labels; and draft invisibility.

When native Playwright tests and browser binaries are installed, set `ONTRACK_RUN_BROWSER_TESTS=1`; the gate runs `tests/browser`. Without that opt-in, browser qualification is explicitly reported pending.
