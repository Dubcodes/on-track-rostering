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

## Required continuation

1. Track palette implementation is delivered in the second checkpoint below;
   actual PostgreSQL migration/concurrency qualification remains pending.
2. Direct read-only-reference Builder/Day transplant, integrated pickers,
   and neutral expandable Race/Trial source evidence.
3. Live provider adapters for Love Racing, HRNZ, and any supplied API. Manual
   import remains the only configured ingestion boundary until those adapters
   are implemented and deployment-qualified.
4. Realistic data import, final cleanup/qualification,
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

At that second checkpoint, no push, exact-head GitHub workflow, Compose/image
qualification, Docker execution, or deployment occurred. Its then-pending
source/import/shared-UI items are superseded by the hardening checkpoint below;
the Builder/Day transplant remains outstanding.

## External-calendar hardening checkpoint — 2026-09-20

Baseline `HEAD == origin/main` was
`13bab9e12149f8edd5f85ce85a58629fbab14eee`. That commit introduced migration
`7e6a1c2d4f90_external_calendar_foundation.py`, following `26a91f48b3d0`, and
the provider-neutral event, observation, Track mapping, provider-state, and
calendar-preference tables. This continuation does not rewrite that committed
migration.

The canonical event is identified across providers by mapped Track, date,
discipline, and event kind. Provider identity first deduplicates observations.
Missing canonical facts may be enriched and receive field provenance; a
conflicting non-null fact is retained as review evidence and never overwrites
the canonical value. Unresolved Track spellings remain observations until an
Admin confirms a reusable provider-to-Track mapping. No live adapter or fake
sync success is present.

The version-1 neutral import contract is now typed with bounded Pydantic models
for Regions, Tracks, Crew Groups, Positions, People/memberships/capabilities,
and external events. Unknown fields—including literal Track colours—and secret,
credential, token, bank, or payroll-shaped fields are rejected. Preview is
read-only and classifies create/match/enrich/conflict/unresolved/duplicate work;
ambiguous people and broken master-data references block the atomic Apply.
Apply uses the regional palette allocator and does not import accounts or
credentials.

Planning-event reads now use the central Actor model: Admin is global;
Manager/SubManager/Viewer and Employee reads are regional; an Employee's home
Region is included when they hold Employee authority; Contractor grants do not
open the planning feed. A private linked draft remains invisible and does not
suppress the source event. A visible current publication suppresses the
duplicate planning card. Adoption locks and rechecks the canonical event,
creates and links exactly one private draft in the caller's transaction, seeds
known facts, and never publishes or later overwrites operational edits.

The Online Sources workspace reports honest manual-only provider state,
unmatched Track mappings, and conflicting incoming/canonical evidence. Calendar
preferences remain separate from notification and authoritative-roster
visibility. Manager-action push now respects notification preferences;
open-position digests require Employee authority in the event Region; and
regional notice banner/push relevance shares grant-or-published-assignment
logic for Contractors.

Shared UI cleanup removed the three-dot menu and redundant Settings Roster
shortcut, retained centralized header controls, added short-lived same-path POST
scroll restoration, and placed the shared holiday marker before the date in
Month/List/Crew/fortnight rows (including unworked days). The Builder and
published Day transplant was deferred to the completed checkpoint below.

## Direct Builder and published Day transplant — 2026-09-20

Starting committed baseline: `657435af3554637f2bcc0218474c3358dea7c6c8`.
This checkpoint completes the direct, read-only-reference adaptation of the
Re-Deputy Builder and manual-roster Day interaction language onto On Track's
authoritative draft/publication model. Re-Deputy was not modified and no
Re-Deputy dependency, identifier, credential, data, or deployment asset was
introduced.

Builder now uses the compact Position | Person | More row, integrated
keyboard/touch search pickers, Relevant/Other crew groups, capability and
same-date hints, explicit Open and TBC choices, and deliberate resolution of
Manager-action rows. Advanced controls contain only supported assignment
timings, note privacy, slot index, and removal; Travel, accommodation, vehicle
mutation, and arbitrary display-order controls are intentionally absent.
Save & Preview submits the complete private draft to one service transaction:
the current Workday row is locked, the exact version and draft are checked,
every row is validated before mutation, existing slot keys survive edits,
omitted rows are removed, display snapshots are regenerated server-side, new
rows are created safely, and the lock version advances once. Existing
per-assignment endpoints remain compatibility boundaries, not a competing UI.

Preview retains On Track's explicit Edit → Save & Preview → Publish boundary,
with a compact publication summary and Timing, Crew, Notes, and Changes review.
Published Day now uses the Re-Deputy-derived page heading, detail card, Race/Trial
timing rows, compact crew table, description, maths, and change panels. It reads
only the existing policy-aware employee read models: Contractor own-only,
Employee/Viewer regional scope, management detail, private-note filtering,
history filtering, and self-decline rules remain authoritative. The offline
personal Day fallback uses the same component vocabulary and remains personal
and read-only.

One provider-neutral source-evidence partial is shared by external-event and
published-Day pages. Linked Race and Trial days expose canonical facts,
field provenance, observations, parsed facts, and retrieval timestamps in a
collapsed section; manual Workdays render no empty source panel. Raw payload
values and secret-shaped data are not rendered. Live Love Racing, HRNZ, and
future API adapters remain deliberately outside this checkpoint.

Local qualification evidence:

- Native Windows release gate passed Python compile, Ruff, PostgreSQL-dialect
  Alembic SQL generation through `7e6a1c2d4f90`, 119 deterministic tests,
  `pip check`, `pip-audit --local` (no known vulnerabilities), and syntax checks
  for every JavaScript asset including the new Builder module.
- 13 PostgreSQL-only tests were skipped because no disposable
  `ONTRACK_TEST_DATABASE_URL` is configured. No SQLite result is represented as
  PostgreSQL execution evidence; real migration and concurrency qualification
  remains required from exact-head CI.
- Native Playwright passed 16 tests. Builder and Day paths cover 1280, 430, 375,
  and 320 pixels, Race Night and Daylight, picker keyboard/touch behavior,
  hints, Manager-action resolution, advanced/add/remove controls, Preview,
  Race and Trial timing, source evidence, privacy flows, and horizontal
  overflow. Captures under `test-results/ui-fidelity/` were visually inspected.
- No schema migration, local Docker execution, deployment, live source adapter,
  Operations/Travel implementation, or staging-state claim is part of this
  checkpoint.
# Public racing calendar refresh

Admins enable and refresh Love Racing or HRNZ in **Administration → Online Sources**. Unknown provider venue names remain unmatched observations. On Track offers cautious, display-only Track suggestions; an Admin must confirm every authoritative `ExternalTrackMapping`. Confirming a mapping replays pending observations through the canonical event model and never creates a Track.

The same refresh can be invoked for deployment scheduling without embedding a scheduler in the web process:

```text
python -m app.external_calendar.refresh LOVE_RACING
python -m app.external_calendar.refresh HRNZ
python -m app.external_calendar.refresh ALL
```

Providers must be enabled first. `OK`, `PARTIAL`, and `ERROR` reflect real component outcomes. Manual rostering remains available in every state. See `docs/RACING_SOURCE_DISCOVERY.md` for live-source limitations.

## Real master-data and Track-mapping readiness

The system is ready to onboard real organisational master data and explicit
provider-to-Track decisions; this does not mean real staff data or every real
venue mapping is loaded. Online Sources groups unmatched observations by
provider plus normalized source name, keeps similarity suggestions advisory,
and disables suggestions and quick Track creation for HRNZ `CLUB_ONLY`
evidence.

The strict version-1 neutral import contract now accepts optional
`external_track_mappings` identified by provider, external source name, and
canonical Region/Track names. Preview reports create, match, conflict, missing
Track, and invalid-provider outcomes without writing. Apply processes master
data before mappings in one transaction, reuses the canonical reconciliation
service for pending observations, and never silently repoints an existing
mapping.

Admins can download a dedicated grouped source-review template or a
non-sensitive structural master-data export. The latter contains Regions,
Tracks, Crew Groups, Base Positions, and confirmed mappings only; it excludes
People, authentication data, sessions, notification endpoints, private roster
data, and publications, and is not a system backup. Confirmed mappings are
read-only until safe remap semantics are designed. The detailed staging
workflow and fictional starter bundle are documented in
`docs/REAL_DATA_ONBOARDING.md` and
`docs/examples/ontrack-master-data.example.json`.

## First live-staging UX correction

Administration and the Master Data workspace share one reusable Region/Track
management surface. Region policy and geography, Track Region/map reference,
Archive, Restore, and normalized Track-name rules remain owned by the existing
catalog routes and services. Active and archived records are separated;
archived Regions and their Tracks are excluded from operational selectors.
`Remove unused` is deliberately narrow: SQLAlchemy's actual foreign-key graph
is checked transactionally, referenced records are rejected, and audit history
is retained. No lifecycle or deletion schema was added.

The manual Build entry now uses the same private-draft and compact component
language as the existing roster Builder. It remains a small identity step and
does not persist anything until submitted. External-event adoption continues to
open the same Builder and retains its duplicate guard.

Help content is centralized as structured topics with concise tasks, rules, and
authorization-filtered workspace links. Unknown contexts resolve to a useful
Help index rather than a generic paragraph.

Provider refresh health now reflects source/component and malformed-record
warnings only. Unmapped observations, unique unmatched identities, confirmed
mappings, and reconciliation conflicts are computed as a separate Online
Sources read model. Existing observations and canonical events are not rewritten
and real Track mappings are not claimed complete.
