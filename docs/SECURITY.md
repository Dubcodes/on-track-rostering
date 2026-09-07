# Security

## Authentication

Credentials use Argon2id with per-hash salts and an optional deployment pepper. Normal role PINs require 6–32 ASCII digits. Admin requires an 8+ digit PIN or a 12+ character password. Login errors are generic. A keyed email/IP throttle blocks after five failures in a 15-minute window.

The first Admin can only be created from `python -m app.cli create-admin`; no public route bootstraps authority. Public signup, when enabled, creates only a pending request with a requested region and no role, person link, or roster access.

Invitations use 40-byte URL-safe random tokens; only SHA-256 hashes are stored. They expire, are single-use, and a new invitation revokes unused invitations for that email. Activation locks the row and links an available person without manufacturing a second identity.

## Sessions and elevation

Trusted-device cookies contain opaque random tokens; only hashes are stored. Session cookies are HttpOnly, SameSite=Lax, and Secure when configured. CSRF cookies are SameSite=Strict and compared with a server-stored hash on every mutation. Origin and Sec-Fetch-Site are checked as defense in depth.

Standard trust defaults to 90 days; elevated roles default to 14. Both slide on authenticated activity and the oldest devices over the configured limit are revoked. Role elevation must validate credential strength, increment `User.auth_epoch`, and require login; an older device with a stale epoch is rejected. The Admin UI in this foundation does not yet expose role mutation, reducing accidental bypass risk.

Passkey rows store credential IDs, public keys, counters, and transports—never biometrics or private keys. WebAuthn registration/authentication ceremonies and optional TOTP are not yet implemented.

Disabling or reactivating another account increments its authentication epoch and revokes every active trusted device. An Admin cannot disable their own current account through this route. Role mutation remains unavailable until credential-policy validation and fresh privileged authentication are implemented together.

## Authorization and data visibility

`app/auth/policy.py` is the central backend authority. Every management route checks Admin or regional Manager/Sub-Manager authority. Published Day/JSON reads check regional Crew View or direct assignment. Employee reads never follow the draft pointer. Private assignment notes are filtered at query/view-model construction; Viewer scope never grants them.

Crew-management mutations validate the target person's home region against the actor's management scope. Open applications require a linked Employee role in the workday region plus effective base-position eligibility; Contractors and Viewers cannot apply. Applicant selection requires regional management authority and rejects applications from an older publication. Decline lookup binds the authenticated person's ID, current published revision, and stable slot key, preventing another user's assignment from being declined by ID substitution.

## Browser and output protection

Jinja autoescaping remains enabled. User text is rendered as text, never as a template. SQLAlchemy parameters all database values. Responses set CSP, frame-ancestor denial, no-sniff, same-origin referrer, and restrictive browser permissions. Redirect targets must be local absolute paths.

Audit detail is recursively redacted for credential/token/cookie/secret keys. The application must never log raw invitation URLs beyond their one-time Admin display.

## Remaining security work

Complete WebAuthn ceremonies, TOTP fallback, role-change/elevation UI/service, invitation issuance for regional Managers/Sub-Managers, push recipient expansion/encryption/delivery, explicit trusted proxy forwarding rules, dependency audit remediation workflow, and penetration tests for IDOR/CSRF/note visibility before production use.
