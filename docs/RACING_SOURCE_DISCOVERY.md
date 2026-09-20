# Racing source discovery

Discovery was performed against the official public sources on 21 September 2026. External data is planning evidence; On Track remains authoritative and manual rostering never depends on these sources.

## Love Racing

### Race meetings and trials

- Provider: `LOVE_RACING`
- Official calendar UI: `https://loveracing.nz/RaceInfo.aspx#bm-meeting-calendar`
- Chosen endpoint: `POST https://loveracing.nz/ServerScript/RaceInfo.aspx/GetCalendarEvents`
- Format: ASP.NET JSON wrapper. The `d` property contains a JSON event array.
- Request: `start` and `end` in `dd-MMM-yyyy` form.
- Useful fields: `DayID`, `RaceDate` (`/Date(milliseconds)/`), `Racecourse`, `Club`, and `WebMeetingType`.
- Stable identity: `DayID`.
- Classification: `WebMeetingType == T` is a trial; other published meeting types are race meetings.
- Timing and race count: not present in this calendar response, so they remain unknown.
- Horizon: accepts a bounded date interval. On Track requests the configured lookback/future horizon once per refresh.
- Observed behavior: one response covers races and trials and is the preferred source. The site can apply an upstream bot challenge; this is surfaced as a component error rather than bypassed.
- Failure modes: HTTP/challenge response, invalid outer JSON, invalid nested JSON, or malformed individual records. Invalid records are warnings; a wholly unusable response is fatal.

The official `https://events.loveracing.nz/` calendar is stable structured HTML and is used as a race-meeting fallback if the RaceInfo endpoint is unavailable. Each event tile exposes a date, meeting title, venue text, and details link. It does not provide the complete trials calendar. Its generated date/venue key is stable enough for provider deduplication but is weaker than `DayID`.

No documented public Love Racing API was found. No authentication, cookie capture, challenge bypass, or unofficial source is used.

## Harness Racing New Zealand

### Race meetings

- Provider: `HRNZ`
- Official index: `https://infohorse.hrnz.co.nz/datahrs/calendar/raceday/dates_index.htm`
- Chosen source: `https://infohorse.hrnz.co.nz/datahrs/calendar/HRNZOfficialMeetings.ics`
- Format: iCalendar (`VEVENT`).
- Useful fields: `UID`, `DTSTART`, `SUMMARY`, and `LOCATION`.
- Stable identity: `UID`, with date/normalized venue fallback.
- Timing and race count: used only if explicitly present in a future compatible observation; the current adapter does not infer them.
- Horizon: the official feed controls its published horizon; On Track filters it to the configured interval.
- Caching: one request per refresh. No meeting-detail fan-out.
- Failure modes: HTTP/challenge response, non-iCalendar body, or malformed individual events.

During local live smoke testing, the official ICS host returned HTTP 403 to a non-browser service client even though the link is publicly advertised. The adapter and offline contract tests are complete, but this component correctly reports `ERROR`/provider `PARTIAL` until the deployment environment can fetch the official feed. On Track does not bypass the challenge.

HRNZ also documents an API at `https://harness.hrnz.co.nz/APIdoc/Version-1.1/`, but production access requires approval and is a paid service. It is therefore not used or represented as a public API.

### Trials

- Official source: `https://www.hrnz.co.nz/racing/race-programmes/trials-diary/`
- Format: structured HTML with venue headings and date paragraphs.
- Stable identity: event date plus normalized venue because the diary exposes no event ID.
- Venue: section heading, preserved exactly for Admin mapping.
- Date: month-labelled diary entries; repeated dates in the grouped summary are deduplicated.
- Timing: only explicit section start times are normalized.
- `Qualifiers before races`: retained as a source fact and never converted into a precise start time.
- Horizon: provider-controlled diary horizon, then filtered to On Track's configured interval.
- Failure modes: HTML structure changes, ambiguous/missing month, invalid date, or a section with no usable entries. Ambiguous rows are skipped with warnings.

## Operational choices

- Default lookback: 14 days.
- Default future horizon: 366 days, subject to each provider's published range.
- Timeout: 15 seconds; response limit: 2 MB.
- Provider URLs are code-owned constants. Admins cannot enter arbitrary URLs.
- Fetching never happens during application startup.
- A missing observation never deletes or cancels an existing event.
- Explicit source status can become evidence for Manager review; it never silently changes a Workday.
- Automated tests use small structural fixtures and never call the internet.

## Status at implementation

| Data class | Adapter | Local live smoke |
| --- | --- | --- |
| Love Racing races | RaceInfo JSON, official event-HTML fallback | Live |
| Love Racing trials | RaceInfo JSON | Live |
| HRNZ races | Official ICS | Upstream HTTP 403 from local service client; reports partial |
| HRNZ trials | Official Trials Diary HTML | Live |
| Future API | None | Not configured |
