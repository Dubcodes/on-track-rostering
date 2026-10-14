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
10. Create or invite accounts separately. A Person is rosterable master data; an account is an authentication identity.

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
- Real staff data must not be committed to the repository. Prepare it as an external neutral bundle for controlled preview and import.

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

Accounts → People onboarding exports `person_id,display_name,email,primary_region,account_state`. `person_id` is the exact On Track Person UUID; Deputy employee IDs are neither required nor exported. Preview validates the complete worksheet without writes. Apply updates only Person email and Primary region in one transaction and creates no User, PIN, password, or invitation.

Ready People may then receive fresh-auth, CSRF-protected batch Employee invitations bound to those exact People. Setup URLs use the configured trusted origin and place the raw token only in the fragment. The no-store result can create a CSV entirely in the browser; raw links are never posted back or persisted as a CSV. On Track sends no onboarding email.

Operational leave accepts `Person Name<TAB>Start Date<TAB>End Date`, with DD/MM/YYYY or YYYY-MM-DD dates. Exact normalized active-Person names are required. Preview performs no writes; Apply is atomic and an exact existing active range is idempotent.

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
