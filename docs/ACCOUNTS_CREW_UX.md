# Accounts and Crew workflow

This document records the confirmed Phase A defects, the implemented correction, and the proposed Phase B information architecture. It does not change authorization policy. The code and `app/auth/policy.py` remain authoritative.

## Phase A: confirmed defects and correction

The previous Accounts workflow had four related problems:

- select and checkbox filters required an explicit submit while comparable operational lists behaved as live filters;
- a stale fresh-authentication session rejected a privileged POST and redirected the operator to personal Settings, losing the task context;
- the direct-account form defaulted to an invalid Contractor/global-Region combination and returned generic errors after discarding safe values;
- successful account and invitation creation returned to a broad page without clearly identifying the User, Person or pending invitation that had been created.

Phase A keeps the existing permissions and corrects those interactions:

- one shared live-filter controller submits selects, checkboxes and dates immediately, debounces text search, combines current fields, replaces rather than grows browser history, and restores practical scroll/focus state; normal GET forms remain the non-JavaScript fallback and URLs remain shareable;
- sensitive account and Crew forms check the server-provided freshness deadline before submission and open a contained credential dialog when required. Reauthentication retains CSRF and throttling, stores no credential or privileged form payload, and never replays the rejected action. The operator explicitly submits the original action after confirmation;
- direct account creation intentionally defaults to Employee and the first permitted Region, adapts Region/Person fields to the role, validates duplicate User and Person/email/Contractor combinations, preserves only non-secret values on failure, and continues to enforce grants through the existing policy boundary;
- linked accounts return to the Person's Account & access section; unlinked Users return to a highlighted Advanced Accounts result. Invitations retain their fragment secret and one-time reveal, have an explicit copy action, identify the recipient, say that no email was sent, and return to their Person or invitation workspace;
- invitation lists distinguish Pending, Accepted, Expired and Revoked without revealing the secret again.

Accounts remains an advanced surface in Phase A. No route or capability has been removed, and no Crew-Group Manager, global Viewer, or Person employment-classification semantics have been added.

## Phase B: proposed page and workflow map

The normal staff workflow should begin at **Management → Crew** and reuse the existing Person identity before creating authentication state.

| Surface | Proposed responsibility | Existing behavior to retain or move |
|---|---|---|
| Crew directory | Find/reuse a Person, consistent search and filters, onboarding state, Add Person | Keep Person creation, batch onboarding entry and directory filters here; make duplicate discovery the first step |
| Person → Profile | Name, contact and operational home data | Keep current Person editing and Region/Crew Group membership |
| Person → Roster & capabilities | Position capability, truck qualification, leave/availability links and roster context | Keep current Person-centric capability workflow and links |
| Person → Account & access | Linked User status, create account, create invitation, ordinary scoped role/Region access | Move the normal single-person account/invitation entry point here; retain server policy checks |
| Person → Security & history | Credential reset/setup handoff, device/session state where authorized, safe audit links | Consolidate existing bounded security actions; do not expose secrets or broaden who may control privileged accounts |
| Advanced Accounts | Exceptional recovery, unlinked User reconciliation, privileged/global grants, pending requests, invitation oversight and system-wide search | Keep `/manage/accounts`; reduce its prominence for normal Person onboarding but retain every recovery/reconciliation route |
| Bulk onboarding | Secondary nationwide/imported-People workflow | Keep metadata preview/apply and one-time setup-link CSV; link from Crew rather than making it the normal Accounts task |

The main duplication to remove in a later approved implementation is offering equivalent ordinary create/invite/link decisions in both a Person workflow and the top-level Accounts workspace. Advanced Accounts should link to the canonical Person when one exists; Person detail should link back to advanced recovery only when exceptional work is needed.

## Decisions required before Phase B implementation

Approval is required before changing navigation or route ownership because the migration must preserve bookmarks, audit meaning and every existing recovery path. Product decisions also remain open for:

- whether full role/grant administration belongs on Person detail or stays exclusively in Advanced Accounts;
- whether an intentionally unlinked User receives a dedicated detail page or remains a focused Accounts search/result;
- the final ownership and prominence of signup requests and bulk onboarding;
- Crew-Group-scoped Manager authority, global Viewer semantics, and Person-level employment classification.

The last three permission concepts are deliberately outside Phase A and must be designed and authorized separately.
