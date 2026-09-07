# Substantial On Track task template

## Task

[Describe the concrete outcome required.]

## Background

[Explain the operational problem and current behavior.]

## Scope

- [Required workflow or behavior]
- [Required data/UI/API changes]

## Non-goals

- [Explicitly deferred work]

## Relevant domain rules

- [Link or quote the applicable rules from `docs/DOMAIN_RULES.md`.]

## Acceptance criteria

- [Observable success condition]
- [Authorization and failure behavior]

## Required tests

- [Positive route/service test]
- [Negative authorization or stale-state test]
- [PostgreSQL-specific test, if applicable]

## Safety constraints

Before changing code, read `AGENTS.md` and the relevant project docs; inspect the current source, migration history, and Git status. Preserve unrelated work. Never modify Re-Deputy and never use Docker on the Windows development machine. Production Portainer pulls GitHub `main`; keep migrations and startup deployable on Linux without Windows paths or committed secrets. Use PostgreSQL semantics and never present SQLite as PostgreSQL qualification.

Preserve server-side authorization, immutable Draft → Preview → Publish, small published-only employee read models, and manual racing-information fallback. Do not weaken a database invariant to simplify a test.

## Final qualification

Run relevant Windows-native compile, Ruff, pytest, JavaScript, dependency, template, and browser checks. Run real PostgreSQL checks only when an explicit disposable test URL is configured; otherwise report them pending. Update tests with behavior changes and update architecture/domain/security decisions as they change.

## Handoff

Update `docs/BUILD_STATUS.md` with exactly what is implemented, partially implemented, tested, pending, and deferred. Report changed architecture, exact commands/results, PostgreSQL and browser status, security findings, deployment readiness, known issues, and the next substantial build. Do not push unless explicitly authorized.
