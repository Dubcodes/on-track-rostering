# Security

## Authentication

Credentials use Argon2id with per-hash salts and an optional deployment pepper. Normal role PINs require 6–32 ASCII digits. Admin requires an 8+ digit PIN or a 12+ character password. A passkey cannot elevate an account whose persisted primary credential is below that Admin floor. Login errors are generic. A keyed email/IP throttle blocks after five failures in a 15-minute window.

The first Admin can only be created from `python -m app.cli create-admin`; no public route bootstraps authority. Public signup, when enabled, creates only a pending request with a requested region and no role, person link, or roster access.

Invitations use 40-byte URL-safe random tokens; only SHA-256 hashes are stored. They expire, are single-use, and a new invitation revokes unused invitations for that email. Activation locks the row and links an available person without manufacturing a second identity.

## Sessions and elevation

Trusted-device cookies contain opaque random tokens; only hashes are stored. Session cookies are HttpOnly, SameSite=Lax, and Secure when configured. CSRF cookies are SameSite=Strict and compared with a server-stored hash on every mutation. Origin and Sec-Fetch-Site are checked as defense in depth.

Standard trust defaults to 90 days; elevated roles default to 14. Both slide on authenticated activity and the oldest devices over the configured limit are revoked. Primary-authentication time is stored separately; sensitive factor, privilege, invitation, and account-lifecycle actions require it within the configurable 15-minute default. Role changes increment `User.auth_epoch`; an older Employee device with a stale epoch is rejected rather than silently becoming Admin.

Passkey rows store credential IDs, public keys, counters, and transports—never biometrics or private keys. Registration challenges are random, hashed at rest, five-minute, one-use, and bound to the authenticated account and trusted device. Authentication challenges are one-use. The maintained `webauthn` library verifies RP ID, configured origin, signature, and counter. Registration requires a discoverable credential so username-less login works. Removing a passkey requires fresh authentication.

Optional TOTP secrets are encrypted with Fernet using domain-separated key material derived from the deployment `ONTRACK_SECRET_KEY`. Setup remains inactive until a valid code confirms it. Verification tolerates one 30-second step either side and stores the last accepted counter to prevent replay. Enabling/disabling a factor requires fresh authentication, advances the auth epoch, retains only the current trusted device, and creates redacted audit events. Admin/Manager enforcement flags default false to avoid locking out bootstrap accounts; a user who enables TOTP is always challenged on PIN/password login. A verified passkey is a strong login path and does not also require TOTP.

New grants to existing accounts are `PENDING` and confer no authority. A primary credential login activates eligible pending grants and mints a device with the correct shorter elevated lifetime. Managers may stage only Sub-Manager grants in regions they manage; Admins may stage broader explicit grants. Active-grant revocation invalidates every target session. The final active Admin grant/account cannot be revoked or disabled through the UI, and an Admin cannot disable their own current session.

## Authorization and data visibility

`app/auth/policy.py` is the central backend authority. Operational roster routes check Admin or regional Manager/Sub-Manager authority; account and regional catalogue administration requires Admin or regional Manager. Published Day/JSON reads check regional Crew View or direct assignment. Employee reads never follow the draft pointer. Regional account queries include only linked people or grants in the Manager's regions. Private assignment notes are filtered at view-model construction and are available to the assigned person plus scoped Manager/Sub-Manager/Viewer oversight roles.

Crew-management mutations validate the target person's home region against the actor's management scope. Open applications require a linked Employee role in the workday region plus effective base-position eligibility; Contractors and Viewers cannot apply. Applicant selection requires regional management authority and rejects applications from an older publication. Decline lookup binds the authenticated person's ID, current published revision, and stable slot key, preventing another user's assignment from being declined by ID substitution.

## Browser and output protection

Jinja autoescaping remains enabled. User text is rendered as text, never as a template. SQLAlchemy parameters all database values. Responses set nonce-based CSP without broad `unsafe-inline`, frame-ancestor denial, no-sniff, same-origin referrer, and restrictive browser permissions. Validated six-digit track colours are emitted only in nonce-bearing style blocks/data attributes. Redirect targets must be local absolute paths.

Forwarded client/origin authority is accepted only when the immediate peer is in the configured trusted-proxy CIDRs and each forwarding value is singular and valid. Direct clients and malformed forwarding fall back to the socket peer and internal request origin. Login/signup throttling and same-origin checks consume this shared resolution.

Audit detail is recursively redacted for credential/token/cookie/secret keys. The application must never log raw invitation URLs beyond their one-time Admin display.

## Push and residual risks

Push subscription endpoints and key material are encrypted at rest with separate domain-separated Fernet key material. The VAPID private key is environment-only. Delivery stores bounded retry/permanent failure state and never logs endpoint or key material. Already-cached offline content cannot be remotely erased while a device never reconnects; caches are therefore narrowly scoped to roster reads and are deleted on the next observed account switch/logout.

Recovery codes are not implemented. Production readiness still requires real HTTPS WebAuthn browser qualification, deployment VAPID keys, trusted-proxy review for the selected reverse proxy, and targeted penetration testing.
