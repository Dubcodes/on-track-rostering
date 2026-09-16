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

1. Track palette implementation is delivered in the second checkpoint below;
   actual PostgreSQL migration/concurrency qualification remains pending.
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

## Second local checkpoint — track palette and editing

The first checkpoint `3f83b8de1a56c9d4da33934736b56688e02394bf` is preserved,
not amended or rewritten. This remains a partial structural pass, not permission
to push or deploy halfway through.

Migration `26a91f48b3d0_track_palette_slots.py` follows `91ce2f784ab0`.
Existing active tracks are ranked deterministically by name/UUID per region.
Archived tracks retain history and receive deterministic reusable slots. The
migration aborts transactionally if any region has more than 20 active tracks;
it never silently collides or deletes tracks. The active-only unique index and
range check enforce the intended PostgreSQL model.

`Track.palette_slot` is stored, not hashed, and has no default shared colour or
user colour input. Both create endpoints call the same destination-Region-locking
allocation service. Moves/reactivation retain a slot when free or choose the
first free destination slot; a full region produces a controlled error. Editing
supports name, region, lifecycle, and map reference. Managers need administration
authority in both the current and destination regions; Admin is global. Audit
records previous/destination region and slot.

`Track.display_colour` and `WorkdayRevision.track_colour_snapshot` are removed,
not retained as a second presentation authority. All roster business snapshots,
IDs, dates, published names, assignment identities, and publication pointers are
untouched by the migration. Downgrade recreates neutral prototype defaults; it
cannot recover discarded prototype colour values.

One `track_token` resolver supplies Month/List/upcoming, Crew and Day; Master Data
uses the same resolver as a Jinja filter. `track-palette.css` defines all 20 slots
and reserved source/unconfirmed/Office/Training tokens for every supported theme.
Race Night is muted; bright and high-contrast themes have their own presentation;
Track Colours uses the raw palette. Travel preserves its linked track token with
a shared pattern treatment. No transport architecture is introduced. The shared
stylesheet is build-versioned and included in service-worker shell/offline assets.
Old hex injection and colour-distance warning code are deleted.

The centralized position service is retained rather than adding `sort_order`:
no manual ordering editor is requested, and a new persistent field would require
otherwise unnecessary precedence/snapshot rules. Imports must reuse the service;
slot index remains identity, not display order.

Qualification:

- Native Windows release gate passed compile, Ruff, Alembic PostgreSQL SQL
  generation through the new revision, deterministic tests (104 passed,
  12 PostgreSQL-only skips), `pip check`, `pip-audit --local` (no known
  vulnerabilities), and all JavaScript syntax checks.
- Native Playwright: 14 passed at 1280/430/375/320. New Master Data tests cover
  no colour input, editable region/map controls, and distinct computed palette
  values across Race Night/Daylight/High Contrast/Track Colours. Existing
  privacy, cross-region, upcoming work, offline, and fresh-auth flows passed.
- Screenshots: `test-results/ui-fidelity/master-data-palette-{theme}-{width}.png`.
  Race Night 320 and Daylight 1280 were visually inspected, without overflow.
- A real PostgreSQL simultaneous-track-create regression is added and remains
  skipped locally until a disposable `ONTRACK_TEST_DATABASE_URL` is configured.
  SQL generation and SQLite unit/browser fixtures are not PostgreSQL execution
  or migration-preservation qualification.

No push, exact-head GitHub workflow, Compose/image qualification for this new
checkpoint, Docker execution, or deployment occurred. Sources/import/preferences,
header/scroll/Month/holiday refinements, and Builder/Day transplantation remain
unimplemented. The continuation work list above remains authoritative.
