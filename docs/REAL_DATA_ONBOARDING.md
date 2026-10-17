# Real data onboarding

On Track is authoritative for rostering. Racing sources provide planning evidence only, and source availability never blocks manual roster entry.

## Intended workflow

1. Deploy an exact-head qualified build to the private staging environment.
2. In **Administration → Online Sources**, enable Love Racing and HRNZ.
3. Refresh each source. A failed source component is reported without preventing manual rostering.
4. Review the grouped **Unmatched source identities** inventory. Download the source mapping template when review outside the application is useful.
5. Populate canonical Regions and Tracks through **Data Import**, **Master data**, or the explicit **Create Track & Map** action for confirmed physical venues.
6. Confirm provider-to-Track mappings. Similarity suggestions are advisory and never write mappings automatically.
7. Refresh the sources again so pending observations reconcile through the canonical external-event service.
8. Verify mapped external events appear on Month with the intended Region, Track, discipline, and event kind.
9. Import People, Crew Group memberships, and capabilities only from explicitly prepared company data.
10. Complete Person metadata and create setup links primarily through **Management → Crew**. A Person is rosterable master data; an account is a separate authentication identity. **Accounts** remains an advanced recovery/reconciliation surface.

For the deliberate blank-environment rehearsal, follow [STAGING_RESET_REIMPORT.md](STAGING_RESET_REIMPORT.md) before executing the qualification matrix.

## One-time real-data migration sequence

The remaining real-data transition is an operator-reviewed staging exercise, not a new runtime integration:

1. Pin one exact-head qualified immutable image and preserve the current isolated staging database and application-data volume.
2. Reset only the explicitly authorized staging resources, migrate a blank PostgreSQL database to the recorded Alembic head, and create the bootstrap staging Admin.
3. Preview and apply neutral structural master data first. Confirm global canonical Base Positions, exact Tracks/Regions, Crew Groups, Vehicles, addresses, travel defaults, presets, and provider mappings.
4. Run Position convergence Preview. Any duplicates created by current import paths are a blocking defect; do not normalize them away to hide the evidence.
5. Preview the manually supplied transition capture, resolve every Track/Position/Vehicle classification, then apply atomically. Existing On Track publications win and no User is created.
6. Reconcile Person metadata through exact UUID worksheets. Create account setup links separately; never import or email credentials.
7. Inspect representative published Workdays, capability history, Vehicle-as-Position conflicts, and source-event deduplication before running the complete staging qualification matrix.

Actual staff files remain outside Git. This sequence does not authorize a staging reset, import, deployment, or production promotion.

## Portable files

The neutral import bundle supports Regions, Tracks, Crew Groups, Positions, People, external events, and `external_track_mappings`. Mapping rows identify the canonical Track by Region and normalized Track name; they contain no database UUIDs.

`docs/examples/ontrack-master-data.example.json` is illustrative and is never run automatically. Its Person and email are fictional. The example position vocabulary is not a complete company catalogue.

**Export structural master data** contains Regions, Tracks, Crew Groups, Base Positions, and confirmed external Track mappings. It is not a system backup. It deliberately excludes People, credentials, PINs, credential hashes, passkeys, sessions, trusted devices, invitations, notification endpoints, and roster data.

The source mapping template is a dedicated review format with observation metadata. Fill human-confirmed decisions into the strict neutral import bundle rather than importing the template metadata directly.

## Safety boundaries

- Data Import never imports authentication credentials.
- Provider evidence never determines a Track's business Region automatically.
- HRNZ club names are not automatically Tracks. A `CLUB_ONLY` source identity receives no fuzzy suggestion and cannot use Create Track & Map.
- No source refresh creates a canonical Track.
- Confirmed mapping import is idempotent. A mapping that already targets another Track is a blocking conflict and is never silently repointed.
- Mapping changes do not rewrite published Workday snapshots.
- Real staff transition data may be used only in the isolated private staging rehearsal when deliberately approved. It remains an external operator input and must not be committed. Credentials, PINs, tokens and authentication data are never imported, and production data, volumes or secrets are never copied merely to test the importer.

## CLI inventory

The inventory reads the configured database and never calls provider networks:

```powershell
python -m app.external_calendar.inventory --format json --unmatched-only
python -m app.external_calendar.inventory --format json --provider LOVE_RACING --unmatched-only
python -m app.external_calendar.inventory --format json --provider HRNZ
```

Preview every import in the Admin UI before applying it. Preview performs zero writes; Apply is transactional.

## Operational place and People worksheets

The neutral master-data bundle accepts Region `base_name`/`base_address` and Track `address`, configured default travel minutes, and a safe Love Racing catalogue `map_reference`. Travel time is never calculated from an address. Cambridge Harness and Cambridge Synthetic must remain separate canonical Tracks.

**Management → Crew** is the primary onboarding/setup workflow. Its UUID worksheet/batch metadata flow exports `person_id,display_name,email,primary_region,account_state`. `person_id` is the exact On Track Person UUID; Deputy employee IDs are neither required nor exported. Preview validates the complete worksheet without writes. Apply updates only Person email and Primary Region in one transaction and creates no User, PIN, password, or invitation. The advanced Accounts surface retains compatible recovery/reconciliation tooling where needed.

Ready People may then receive fresh-auth, CSRF-protected batch Employee invitations bound to those exact People. Setup URLs use the configured trusted origin and place the raw token only in the fragment. The no-store result can create a CSV entirely in the browser; raw links are never posted back or persisted as a CSV. On Track sends no onboarding email.

When verifying Personal Month, distinguish roster horizon from source evidence: if the Person's Primary Region has no current published scheduled horizon, future mapped source events may appear from today; once published roster data exists, source-only events on or before that horizon are intentionally suppressed for that Person.

Operational leave accepts `Person Name<TAB>Start Date<TAB>End Date`, with DD/MM/YYYY or YYYY-MM-DD dates. Exact normalized active-Person names are required. Preview performs no writes; Apply is atomic and an exact existing active range is idempotent.

This is intentionally a conservative first parser contract. Extend it only from a real copied external-leave sample; do not guess extra columns, fuzzy names, or implicit dates. The broader real-data transition remains: import and reconcile master/source data first, complete Person metadata through Preview/Apply, generate setup links separately, and validate the resulting demo workflows in private staging before production promotion.

## Staging setup controls

Administration and **Master data** now use the same Region and Track controls.
Active records appear first; archived records stay collapsed and are excluded
from new operational selectors. Archive records that have operational history,
and use **Remove unused** only for a confirmed setup mistake with no foreign-key
business references. Referenced records return a conflict and must be archived
instead. Restoring a Track retains safe regional palette allocation.

An authorized manager opens the full Builder from an empty calendar date or
`/manage/workdays/new`; the selected date is prefilled. Opening an unsaved
Builder creates no Workday or draft. Creation and later editing use the same
Builder: **Save & Preview** persists the private draft, Preview shows the
proposed crew result, and **Publish** makes it authoritative.

Online Sources separates source health from mapping work. Unmapped observations
and unique source identities are a mapping backlog, not provider warnings.
Provider status and warning counts describe fetch, component, contract, or
malformed-record problems; reconciliation conflicts remain a separate review
count. A healthy refresh can therefore be `OK` while many venue identities still
await human confirmation.

Contextual Help is available from roster, management, setup, source, import,
account, and security workspaces. Related links are filtered to the signed-in
user's authority; route authorization remains authoritative.

## Recorded transition discrepancy: Ellerslie 17 October

The reviewed historical capture showed source Vehicle labels including `684`, `685` and `Rav91` represented as Positions and extra assignment rows. Existing published Workdays are not rewritten. For the forthcoming one-time migration, these labels must be classified through an explicit Vehicle mapping before apply; import preview now blocks vehicle-shaped identifiers from resolving or creating a canonical Base Position. The source evidence remains a reconciliation item and creates no capability history.
