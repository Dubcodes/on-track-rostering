from __future__ import annotations

import binascii
import json
import re
import uuid
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.audit.service import record_audit
from app.auth.access import user_has_active_access
from app.auth.factors import (
    active_totp,
    begin_totp,
    client_challenge,
    consume_challenge,
    create_challenge,
    generate_recovery_codes,
    mfa_policy_requires_factor,
    verify_totp_factor,
)
from app.auth.network import resolve_request
from app.auth.security import (
    challenge_throttle_key,
    clear_failures,
    create_device,
    is_throttled,
    record_failure,
    require_fresh_auth,
    safe_return_path,
    set_auth_cookies,
    verify_csrf,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import utcnow
from app.identity.models import PasskeyCredential, RecoveryCode, TotpFactor, TrustedDevice, User
from app.web import context, templates

router = APIRouter()


def _record_security_change(db: Session, request: Request) -> None:
    user = db.get(User, request.state.user.id)
    current = db.get(TrustedDevice, request.state.device.id)
    if not user or not current:
        raise HTTPException(401)
    now = utcnow()
    user.auth_epoch += 1
    current.auth_epoch = user.auth_epoch
    current.primary_authenticated_at = now
    for device in db.scalars(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user.id,
            TrustedDevice.id != current.id,
            TrustedDevice.revoked_at.is_(None),
        )
    ):
        device.revoked_at = now


def _json_options(value: object, challenge_id: uuid.UUID) -> JSONResponse:
    payload = json.loads(options_to_json(value))
    payload["challenge_id"] = str(challenge_id)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/settings/passkeys/options")
def registration_options(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    throttle_key = challenge_throttle_key("PASSKEY_REGISTER", request.state.user.id)
    if is_throttled(db, throttle_key):
        raise HTTPException(429, "Too many passkey requests. Try again later.")
    record_failure(db, throttle_key)
    existing = list(
        db.scalars(select(PasskeyCredential).where(PasskeyCredential.user_id == request.state.user.id))
    )
    options = generate_registration_options(
        rp_id=get_settings().webauthn_rp_id,
        rp_name=request.state.branding.product_name,
        user_id=request.state.user.id.bytes,
        user_name=request.state.user.email,
        user_display_name=request.state.user.display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=credential.credential_id) for credential in existing
        ],
    )
    challenge, _ = create_challenge(
        db,
        purpose="PASSKEY_REGISTER",
        challenge=options.challenge,
        user_id=request.state.user.id,
        device_id=request.state.device.id,
    )
    db.commit()
    return _json_options(options, challenge.id)


@router.post("/settings/passkeys/verify")
async def register_passkey(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    verify_csrf(request, str(payload.get("csrf_token", "")))
    require_fresh_auth(request)
    credential = payload.get("credential")
    if not isinstance(credential, dict):
        raise HTTPException(400, "Missing WebAuthn credential.")
    try:
        challenge_id = uuid.UUID(str(payload.get("challenge_id", "")))
        expected = client_challenge(credential)
        consume_challenge(
            db,
            challenge_id=challenge_id,
            purpose="PASSKEY_REGISTER",
            raw_challenge=expected,
            user_id=request.state.user.id,
            device_id=request.state.device.id,
        )
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=expected,
            expected_rp_id=get_settings().webauthn_rp_id,
            expected_origin=get_settings().webauthn_origin,
            require_user_verification=True,
        )
    except (ValueError, InvalidRegistrationResponse) as exc:
        raise HTTPException(400, "Passkey registration could not be verified.") from exc
    if db.scalar(
        select(PasskeyCredential.id).where(
            PasskeyCredential.credential_id == verified.credential_id
        )
    ):
        raise HTTPException(409, "That passkey is already registered.")
    passkey = PasskeyCredential(
        user_id=request.state.user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=",".join(credential.get("response", {}).get("transports", [])),
        label=str(payload.get("label", "Passkey")).strip()[:120] or "Passkey",
    )
    db.add(passkey)
    db.flush()
    record_audit(db, "passkey.registered", "passkey", passkey.id, request.state.user.id)
    _record_security_change(db, request)
    db.commit()
    clear_failures(db, challenge_throttle_key("PASSKEY_REGISTER", request.state.user.id))
    return {"ok": True}


@router.post("/settings/passkeys/{passkey_id}/remove")
def remove_passkey(
    passkey_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    passkey = db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.id == passkey_id,
            PasskeyCredential.user_id == request.state.user.id,
        )
    )
    if not passkey:
        raise HTTPException(404)
    if (
        mfa_policy_requires_factor(db, request.state.user)
        and not active_totp(db, request.state.user.id)
        and (db.scalar(
            select(func.count(PasskeyCredential.id)).where(
                PasskeyCredential.user_id == request.state.user.id
            )
        ) or 0) <= 1
    ):
        raise HTTPException(409, "Add another authentication factor before removing this passkey.")
    record_audit(db, "passkey.removed", "passkey", passkey.id, request.state.user.id)
    db.delete(passkey)
    _record_security_change(db, request)
    db.commit()
    return RedirectResponse("/settings#passkeys", status_code=303)


@router.post("/login/passkey/options")
def authentication_options(request: Request, db: Session = Depends(get_db)):
    throttle_key = challenge_throttle_key(
        "PASSKEY_AUTH", resolve_request(request).client_address
    )
    if is_throttled(db, throttle_key):
        raise HTTPException(429, "Too many passkey requests. Try again later.")
    record_failure(db, throttle_key)
    options = generate_authentication_options(
        rp_id=get_settings().webauthn_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge, _ = create_challenge(db, purpose="PASSKEY_AUTH", challenge=options.challenge)
    db.commit()
    return _json_options(options, challenge.id)


@router.post("/login/passkey/verify")
async def authenticate_passkey(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    credential = payload.get("credential")
    if not isinstance(credential, dict):
        raise HTTPException(400, "Missing WebAuthn credential.")
    raw_id = credential.get("rawId") or credential.get("id")
    if (
        not isinstance(raw_id, str)
        or len(raw_id) > 2048
        or not re.fullmatch(r"[A-Za-z0-9_-]+", raw_id)
    ):
        raise HTTPException(400, "Malformed WebAuthn credential.")
    try:
        credential_id = base64url_to_bytes(raw_id)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "Malformed WebAuthn credential.") from exc
    passkey = db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == credential_id
        ).with_for_update()
    )
    user = db.get(User, passkey.user_id) if passkey else None
    contractor_expiry = user.contractor_access_expires_at if user else None
    if contractor_expiry and contractor_expiry.tzinfo is None:
        from datetime import UTC

        contractor_expiry = contractor_expiry.replace(tzinfo=UTC)
    if (
        not passkey
        or not user
        or user.status != "ACTIVE"
        or not user_has_active_access(db, user.id)
        or (contractor_expiry is not None and contractor_expiry <= utcnow())
    ):
        raise HTTPException(400, "Passkey authentication failed.")
    try:
        challenge_id = uuid.UUID(str(payload.get("challenge_id", "")))
        expected = client_challenge(credential)
        consume_challenge(
            db,
            challenge_id=challenge_id,
            purpose="PASSKEY_AUTH",
            raw_challenge=expected,
        )
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=expected,
            expected_rp_id=get_settings().webauthn_rp_id,
            expected_origin=get_settings().webauthn_origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except (ValueError, InvalidAuthenticationResponse) as exc:
        db.commit()
        raise HTTPException(400, "Passkey authentication failed.") from exc
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = utcnow()
    db.commit()
    clear_failures(
        db,
        challenge_throttle_key("PASSKEY_AUTH", resolve_request(request).client_address),
    )
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Passkey browser")[:120]
    )
    response = JSONResponse({"ok": True, "next": safe_return_path(str(payload.get("next", "")))})
    set_auth_cookies(response, raw_session, raw_csrf, device)
    return response


@router.post("/settings/totp/begin", response_class=HTMLResponse)
def begin_totp_setup(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    settings = get_settings()
    asset_version = escape(settings.build_id or settings.app_version, quote=True)
    try:
        _, secret, qr_svg = begin_totp(
            db, request.state.user, product_name=request.state.branding.product_name
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return HTMLResponse(
        content=(
            "<!doctype html><meta name=viewport content='width=device-width'>"
            f"<title>Set up authenticator · {escape(request.state.branding.product_name)}</title>"
            f"<link rel=stylesheet href='/static/style.css?v={asset_version}'>"
            "<main class='auth-setup'>"
            "<h1>Set up authenticator</h1><p>Scan this once, then enter the current code.</p>"
            f"<img alt='Authenticator QR code' src='data:image/svg+xml;base64,{qr_svg}' "
            "class='auth-qr'>"
            f"<p>Manual key: <code>{secret}</code></p>"
            "<form method=post action='/settings/totp/confirm'>"
            f"<input type=hidden name=csrf_token value='{csrf_token}'>"
            "<label>Six-digit code <input name=code inputmode=numeric autocomplete=one-time-code required></label> "
            "<button>Confirm</button></form></main>"
        ),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/settings/totp/confirm", response_class=HTMLResponse)
def confirm_totp(
    request: Request,
    code: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    factor = db.scalar(
        select(TotpFactor)
        .where(TotpFactor.user_id == request.state.user.id)
        .with_for_update()
    )
    if not factor or factor.confirmed_at is not None or not verify_totp_factor(factor, code):
        raise HTTPException(400, "Authenticator code could not be verified.")
    factor.confirmed_at = utcnow()
    codes = generate_recovery_codes(db, request.state.user.id)
    record_audit(db, "totp.enabled", "user", request.state.user.id, request.state.user.id)
    _record_security_change(db, request)
    db.commit()
    return templates.TemplateResponse(
        "recovery_codes.html",
        context(request, recovery_codes=codes),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/settings/totp/recovery-codes", response_class=HTMLResponse)
def regenerate_recovery_codes(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    if not active_totp(db, request.state.user.id):
        raise HTTPException(409, "Authenticator MFA is not enabled.")
    codes = generate_recovery_codes(db, request.state.user.id)
    record_audit(
        db, "mfa.recovery_codes_regenerated", "user", request.state.user.id,
        request.state.user.id,
    )
    _record_security_change(db, request)
    db.commit()
    return templates.TemplateResponse(
        "recovery_codes.html",
        context(request, recovery_codes=codes),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/settings/totp/recovery-codes/remove")
def remove_recovery_codes(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == request.state.user.id))
    record_audit(
        db, "mfa.recovery_codes_removed", "user", request.state.user.id,
        request.state.user.id,
    )
    _record_security_change(db, request)
    db.commit()
    return RedirectResponse("/settings?recovery_codes=removed#totp", status_code=303)


@router.post("/settings/totp/disable")
def disable_totp(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    factor = active_totp(db, request.state.user.id)
    if not factor:
        raise HTTPException(409, "Authenticator MFA is not enabled.")
    if mfa_policy_requires_factor(db, request.state.user):
        raise HTTPException(409, "Authenticator MFA is required for this account's access.")
    factor.disabled_at = utcnow()
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == request.state.user.id))
    record_audit(db, "totp.disabled", "user", request.state.user.id, request.state.user.id)
    _record_security_change(db, request)
    db.commit()
    return RedirectResponse("/settings?totp=disabled#totp", status_code=303)
