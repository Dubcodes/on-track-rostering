# Re-Deputy reuse review

Reference examined read-only at current `main` commit `5f5116092144488c9202f3f1faa657b95a6f5c6a` on 2026-09-08. The clone lived in a separate temporary folder. No Re-Deputy data, configuration, secret, generated file, volume, or Git metadata entered On Track.

| Area | Classification | On Track treatment |
|---|---|---|
| Month/Day/Settings visual language | Copy then adapt conceptually | Rebuilt clean Jinja/CSS with the familiar calendar/list, track chips, concise timing strip, collapsed detail, mobile navigation, and holiday star. No Deputy state is referenced. |
| Manual roster builder | Conceptually reuse/rewrite | Retained stable rows, person selection, Open/TBC distinction, incomplete drafts, Preview and Publish; rewritten against base positions and authoritative revisions. |
| Public holidays | Copy then adapt | Adapted the deterministic NZ national holiday/Matariki calculation into `app/core/holidays.py`; operational regions remain separate from statutory geography. |
| Trusted devices and invitations | Conceptually reuse/rewrite | Preserved hash-only random tokens, expiry/single use/revocation concepts and sliding device trust; rewritten for Argon2id, role-sensitive lifetimes, CSRF, auth epochs, and PostgreSQL. |
| Same-origin/security headers/redirect safety | Conceptually reuse/rewrite | Central middleware/policy implementation replaces Re-Deputy route-specific checks. |
| PWA shell/service worker | Conceptually reuse/rewrite | New user-namespaced read-only roster cache and sequential prefetch; Re-Deputy's notification click behavior is not copied. |
| Push identity/notifications | Deferred implementation | Clean schema/preference boundary exists. Delivery, VAPID management, deduplication, and schedulers need the next focused pass. |
| Track maps | Deferred investigation | Track has a non-blocking future map reference. Retrieval/upload/catalog mechanics were not transplanted. |
| Backup philosophy | Conceptually reuse/rewrite | Documented validated `pg_dump`/restore workflow; SQLite backup code is intentionally not copied. |
| Release-gate philosophy | Copy then adapt | The Windows-native runner compiles, lints, tests, checks JS, and conditionally runs real PostgreSQL migration idempotence. Docker is excluded from local qualification; Compose is retained for the separate production server. |
| `main.py` / `database.py` | Behavioral study only | Their behavior informed boundaries; their monolithic architecture was rejected. |
| Deputy capture, OAuth, iCal, evidence reconciliation, interpreted workdays, roster-note inference | Deputy-specific / do not migrate | Entirely absent. On Track assignments and revision snapshots are authoritative. |
| Live Love Racing/HRNZ | Deferred investigation | Provider-neutral programme/source override schema only; no scraping occurs in request paths. |
