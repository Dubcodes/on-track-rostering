from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def run(*command: str, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def run_quiet(*command: str, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env=env, stdout=subprocess.DEVNULL)


def main() -> int:
    python = sys.executable
    run(python, "-m", "compileall", "-q", "app", "migrations", "scripts", "tests")
    run(python, "-m", "ruff", "check", "app", "migrations", "scripts", "tests")
    run_quiet(python, "-m", "alembic", "upgrade", "head", "--sql")
    postgres_url = os.environ.get("ONTRACK_TEST_DATABASE_URL", "")
    if postgres_url:
        migration_env = os.environ.copy()
        migration_env["DATABASE_URL"] = postgres_url
        migration_env.pop("ONTRACK_DATABASE_URL", None)
        run(python, "-m", "alembic", "upgrade", "head", env=migration_env)
        run(python, "-m", "alembic", "upgrade", "head", env=migration_env)
    else:
        print(
            "PENDING PostgreSQL integration gate: ONTRACK_TEST_DATABASE_URL is not set; "
            "no SQLite fallback is used for PostgreSQL qualification",
            flush=True,
        )
    run(python, "-m", "pytest", "--ignore", "tests/browser")
    run(python, "-m", "pip", "check")
    if os.environ.get("ONTRACK_SKIP_DEPENDENCY_AUDIT") == "1":
        print("PENDING dependency vulnerability audit: explicitly skipped", flush=True)
    else:
        run(python, "-m", "pip_audit", "--local")
    run("node", "--check", "app/static/app.js")
    run("node", "--check", "app/static/invite.js")
    run("node", "--check", "app/static/passkeys.js")
    run("node", "--check", "app/static/notifications.js")
    run("node", "--check", "app/static/service-worker.js")
    if os.environ.get("ONTRACK_RUN_BROWSER_TESTS") == "1":
        run(python, "-m", "pytest", "tests/browser")
    else:
        print("PENDING browser gate: ONTRACK_RUN_BROWSER_TESTS is not enabled", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
