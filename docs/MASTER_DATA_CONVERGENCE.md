# Master data / calendar source / Builder convergence

## First local checkpoint — 2026-09-16

Starting clean `HEAD == origin/main`: `9d92afaf4623cca3278a7d51e6cf0090a062b086`.
This is a partial implementation checkpoint, not completion or release approval.

Delivered:

- One operational position-order service shared by Builder/Preview, published
  Day/Crew reads, and personal participation summaries. Sorting normalizes
  aliases only; names, assignment UUIDs, stable slot keys, and slot indices are
  unchanged. A persistent manual sort-order field is not introduced.
- One server-side minute-precision time parser accepts `930`, `0930`, `9:30`,
  and `09:30`. Builder workday timings and assignment overrides use it.
  Shared browser normalization is only a convenience; invalid forged input
  remains rejected server-side. Builder clock fields use numeric text entry.
- New-workday choices are restricted to authorized regions and active tracks;
  the initial selector contains only its selected region. Changing region
  rebuilds the selector and resets invalid selections to To be confirmed.
  Existing server-side cross-region rejection remains intact.
- Reason for change is hidden until the workday has a published revision.

Evidence:

- Native Windows release gate: compile, Ruff, existing Alembic PostgreSQL SQL
  generation, `pip check`, `pip-audit --local`, and all JavaScript syntax checks
  passed. The initial sandboxed audit could not reach its network proxy; the
  network-enabled retry found no known vulnerabilities.
- Deterministic suite: 100 passed; 11 PostgreSQL-only tests skipped because no
  disposable `ONTRACK_TEST_DATABASE_URL` is configured. Real PostgreSQL
  migration execution/concurrency qualification remains pending, not replaced
  by SQLite.
- Native Playwright: 12 tests passed, covering 1280/430/375/320 and existing
  personal/offline/privacy/fresh-auth flows. Local browser fixtures use a
  disposable SQLite database; this is browser evidence only.
- Four selector/time-entry browser regressions also passed with screenshots
  under `test-results/ui-fidelity/region-track-filter-{width}.png` (ignored).
  The 320px screenshot was visually inspected without overflow.

No new migration exists in this checkpoint. No Docker execution, push, CI run,
staging/production deployment, or Re-Deputy modification occurred.

## Required continuation — not yet implemented

1. Track palette slots, deterministic migration/backfill, theme palettes,
   region moves with both-region authorization/audit, and obsolete hex cleanup.
2. Fixed shared header controls, three-dot removal, measured Month centering,
   global POST scroll restoration, and holiday-star alignment.
3. Canonical calendar event/observations, reconciliation/provenance, Online
   Sources, safe preview/import, separate display preferences, source markers,
   and source-event adoption into a private draft.
4. Substantial read-only-reference Builder/Day transplant, integrated pickers,
   and neutral expandable Race/Trial source evidence.
5. New-model/domain/browser regressions, full final cleanup/qualification,
   origin recheck, authorized normal push only after completion, and exact-head
   GitHub release-gate evidence. Compose/image checks belong in CI, never local
   Docker execution.

Operations/Travel architecture and live-source production scraping remain out
of scope. Manual rostering, immutable publication, optimistic locking, privacy,
and existing server-side authorization must be preserved through continuation.
