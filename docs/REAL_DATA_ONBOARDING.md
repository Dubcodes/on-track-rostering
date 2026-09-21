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
