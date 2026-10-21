# Accounts and Crew workflow

This document records the implemented Phase A interaction corrections and Phase B information architecture. Neither phase changes authorization policy. The code and `app/auth/policy.py` remain authoritative.

## Phase A: confirmed defects and correction

The previous Accounts workflow had four related problems:

- select and checkbox filters required an explicit submit while comparable operational lists behaved as live filters;
- a stale fresh-authentication session rejected a privileged POST and redirected the operator to personal Settings, losing the task context;
- the direct-account form defaulted to an invalid Contractor/global-Region combination and returned generic errors after discarding safe values;
- successful account and invitation creation returned to a broad page without clearly identifying the User, Person or pending invitation that had been created.

Phase A keeps the existing permissions and corrects those interactions:

- one shared live-filter controller submits selects, checkboxes and dates immediately, debounces text search, combines current fields, replaces rather than grows browser history, and restores practical scroll/focus state; normal GET forms remain the non-JavaScript fallback and URLs remain shareable;
- sensitive account and Crew forms check the server-provided freshness deadline before submission and open a contained credential dialog when required. Reauthentication retains CSRF and throttling and stores no credential or privileged form payload. It never replays the action automatically: after confirmation the same dialog presents an explicit, action-named Continue control that performs the still-visible original form submission;
- direct account creation intentionally defaults to Employee and the first permitted Region, adapts Region/Person fields to the role, validates duplicate User and Person/email/Contractor combinations, preserves only non-secret values on failure, and continues to enforce grants through the existing policy boundary;
- linked accounts return to the Person's Account & access section; unlinked Users return to a highlighted Advanced Accounts result. Invitations retain their fragment secret and one-time reveal, have an explicit copy action, identify the recipient, say that no email was sent, and return to their Person or invitation workspace;
- invitation lists distinguish Pending, Accepted, Expired and Revoked without revealing the secret again.

Accounts remains an advanced surface in Phase A. No route or capability has been removed, and no Crew-Group Manager, global Viewer, or Person employment-classification semantics have been added.

## Phase B: implemented page and workflow map

The normal staff workflow should begin at **Management → Crew** and reuse the existing Person identity before creating authentication state.

| Surface | Responsibility | Implemented behavior |
|---|---|---|
| Crew directory | Find/reuse a Person, consistent search and filters, onboarding state, Add Person | Search-first guidance, duplicate guards, plain Person creation, an atomic Add & create setup link option with name/email/Region/account type, and a secondary bulk-onboarding entry |
| Person → Profile | Name, contact and operational home data | Existing continuous-save Person and Primary Region editing |
| Person → Roster & capabilities | Crew Groups, truck qualification and Position capability | Existing Person-centric controls, collected behind a stable section link |
| Person → Account & access | Linked User status and ordinary scoped account setup | One-time setup invitation is the recommended path; authorized Person-level regional grants reuse pending-grant/revocation services with human Region names; Admin direct creation remains an explicitly exceptional collapsed path using the existing account route |
| Person → Security & history | Credential/device controls where authorized, audit history and Person lifecycle | Existing policy-checked controls and fresh-authentication requirements, grouped without changing authority |
| Advanced Accounts | Exceptional recovery, unlinked User reconciliation, privileged/global grants, pending requests, invitation oversight and system-wide search | `/manage/accounts` remains bookmark-compatible and preserves routes; ordinary batch onboarding and Contractor invitation entry move to Crew/Person context |
| Bulk onboarding | Secondary nationwide/imported-People workflow | `/manage/crew/onboarding` owns filters and entry; existing metadata and batch invitation POST routes remain service endpoints and return to Crew |

Sub-Managers keep their established operational roster authority and ordinary linked-account assistance. Their Crew views are server-filtered as well as visually simplified: no Person creation/profile/capability mutation, account-type/security summary, role grants, trusted devices, audit history, setup invitation, direct account creation, or privilege escalation is loaded or offered. Manager/Admin boundaries remain unchanged.

Person work email uses one normalized identity rule across ordinary create/edit, account linking and invitation, signup approval, batch metadata and import. Active People are unique case-insensitively across Regions and linked Person/User emails stay aligned. A matching unlinked User is shown for deliberate Advanced Accounts reconciliation; it is never auto-linked or merged. Authorized Crew pages flag legacy duplicates and mismatches without rewriting them. PostgreSQL serializes competing normalized-email writes; adding a database partial unique constraint remains a later convergence step after production legacy conflicts are reviewed.

Existing account, invitation, recovery, grant and Contractor lifecycle endpoints remain intact for bookmarks and security behavior. Invitation activation loads authoritative email/name metadata by posting the fragment token to a no-store endpoint, requires server-confirmed credential confirmation, leaves the roster Person unchanged when the recipient corrects the account display name, and establishes the normal session unless existing MFA policy requires its second factor. Linked Contractor results point to their Person; temporary-access extension stays advanced. Existing unlinked accounts remain focused search/reconciliation results rather than receiving a new detail page.

## Decisions deliberately left open

Global/privileged recovery and unlinked-account reconciliation remain in Advanced Accounts. Person-level regional role grants are available in Crew only to the extent `can_grant_role()` and account-administration scope already authorize them; pending activation, final-Admin protection, Contractor exclusivity, audit and session invalidation are unchanged. Signup-request review also remains advanced, while bulk onboarding is secondary Crew work. No dedicated unlinked-account page was needed.

Crew-Group-scoped Manager authority, global Viewer semantics and employment-classification policy remain unresolved. Phase B does not alter `RoleGrant`, regional policy, Contractor exclusivity, Viewer scope or fresh-authentication boundaries.
