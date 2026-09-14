# Re-Deputy reuse review

Reference examined read-only at local commit `20d65d9` (`Re-Deputy 0.5.5`) on 2026-09-09. Its working tree remained clean. No Re-Deputy data, configuration, secret, generated file, volume, or Git metadata entered On Track.

| Area | Classification | On Track treatment |
|---|---|---|
| Month/Day/Settings visual language | Copy then adapt conceptually | Rebuilt clean Jinja/CSS with the familiar calendar/list, track chips, concise timing strip, collapsed detail, mobile navigation, and holiday star. No Deputy state is referenced. |
| Manual roster builder | Conceptually reuse/rewrite | Retained stable rows, person selection, Open/TBC distinction, incomplete drafts, Preview and Publish; rewritten against base positions and authoritative revisions. |
| Public holidays | Copy then adapt | Adapted the deterministic NZ national holiday/Matariki calculation into `app/core/holidays.py`; operational regions remain separate from statutory geography. |
| Trusted devices and invitations | Conceptually reuse/rewrite | Preserved hash-only random tokens, expiry/single use/revocation concepts and sliding device trust; rewritten for Argon2id, role-sensitive lifetimes, CSRF, auth epochs, and PostgreSQL. |
| Same-origin/security headers/redirect safety | Conceptually reuse/rewrite | Central middleware/policy implementation replaces Re-Deputy route-specific checks. |
| PWA shell/service worker | Conceptually reuse/rewrite | New user-namespaced read-only roster cache and sequential prefetch; Re-Deputy's notification click behavior is not copied. |
| Push identity/notifications | Conceptually reuse/rewrite | Encrypted subscriptions, deterministic events, claim leases, retries, audience union, and person/day reminders were written for On Track's publication model. Production VAPID/browser delivery still needs deployment validation. |
| Track maps | Deferred investigation | Track has a non-blocking future map reference. Retrieval/upload/catalog mechanics were not transplanted. |
| Backup philosophy | Conceptually reuse/rewrite | Documented validated `pg_dump`/restore workflow; SQLite backup code is intentionally not copied. |
| Release-gate philosophy | Copy then adapt | The Windows-native runner compiles, lints, tests, checks JS, and conditionally runs real PostgreSQL migration idempotence. Docker is excluded from local qualification; Compose is retained for the separate production server. |
| `main.py` / `database.py` | Behavioral study only | Their behavior informed boundaries; their monolithic architecture was rejected. |
| Deputy capture, OAuth, iCal, evidence reconciliation, interpreted workdays, roster-note inference | Deputy-specific / do not migrate | Entirely absent. On Track assignments and revision snapshots are authoritative. |
| Live Love Racing/HRNZ | Deferred investigation | Provider-neutral programme/source override schema only; no scraping occurs in request paths. |
# Fidelity inventory (2026-09-14)

The current application deliberately reuses Re-Deputy's interaction language, not its data source or runtime architecture.

## Ported or adapted

- Personal Month/List: shared header navigation, seven-day calendar plus desktop Week column, compact cards, Next Up, keyboard navigation and guarded horizontal swipe.
- Public holidays: reusable accessible star marker and tap/click popover, backed by On Track's configurable New Zealand holiday engine.
- Hours: fortnight marker derived from `fortnight_anchor` and a 14-day personal view backed only by current published assignment spans.
- Crew: the same Month/List calendar language, with On Track regional authorization and privacy filtering remaining authoritative.
- Day: compact published-workday hierarchy, category-specific timing, assignments, allowances and collapsible calculation/history.
- Settings and notifications: compact progressive sections, built-in CSP-safe theme previews, per-device push state, persisted preferences and outbox-backed test notifications.
- Builder: private-draft status, searchable people/positions, advisory capability and same-date hints, explicit On Track assignment states, human preview summary and atomic publish.
- Operational notices: the small calendar-banner location is reused for persisted On Track global/regional notices. Regional notices outrank global notices; within a scope the latest start/create/id wins deterministically.

## Intentionally not ported

Deputy credentials, login, synchronization, scraping, API behavior, source evidence/diagnostics, source-specific alerts, the `S` shortcut, hard-coded historical timesheet anchors, Deputy payroll semantics and SQLite architecture are excluded.

## Deferred to Operations/Travel

Vehicle/accommodation mutation in the ordinary builder, travel legs and larger operation coordination remain deferred. Existing published values may be displayed, but this pass does not invent service behavior around schema fields whose product workflow is not yet settled.
