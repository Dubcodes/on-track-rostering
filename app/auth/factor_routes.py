from __future__ import annotations

import json
import uuid
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
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
from app.auth.factors import (
    active_totp,
    begin_totp,
    client_challenge,
    consume_challenge,
    create_challenge,
    verify_totp_factor,
)
from app.auth.security import (
    create_device,
    require_fresh_auth,
    set_auth_cookies,
    verify_csrf,
)
from app.auth.service import activate_pending_grants
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import utcnow
from app.identity.models import PasskeyCredential, TotpFactor, TrustedDevice, User

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
    record_audit(db, "passkey.removed", "passkey", passkey.id, request.state.user.id)
    db.delete(passkey)
    _record_security_change(db, request)
    db.commit()
    return RedirectResponse("/settings#passkeys", status_code=303)


@router.post("/login/passkey/options")
def authentication_options(db: Session = Depends(get_db)):
    options = generate_authentication_options(
        rp_id=get_settings().webauthn_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge, _ = create_challenge(db, purpose="PASSKEY_AUTH", challenge=options.challenge)
    return _json_options(options, challenge.id)


@router.post("/login/passkey/verify")
async def authenticate_passkey(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    credential = payload.get("credential")
    if not isinstance(credential, dict):
        raise HTTPException(400, "Missing WebAuthn credential.")
    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str):
        raise HTTPException(400, "Malformed WebAuthn credential.")
    passkey = db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == base64url_to_bytes(raw_id)
        )
    )
    user = db.get(User, passkey.user_id) if passkey else None
    if not passkey or not user or user.status != "ACTIVE":
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
        raise HTTPException(400, "Passkey authentication failed.") from exc
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = utcnow()
    db.commit()
    activate_pending_grants(db, user, strong_auth=True)
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Passkey browser")[:120]
    )
    response = Response(status_code=204)
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
            "<link rel=stylesheet href='/static/style.css'><main class='auth-setup'>"
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


@router.post("/settings/totp/confirm")
def confirm_totp(
    request: Request,
    code: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    factor = db.get(TotpFactor, request.state.user.id)
    if not factor or factor.confirmed_at is not None or not verify_totp_factor(factor, code):
        raise HTTPException(400, "Authenticator code could not be verified.")
    factor.confirmed_at = utcnow()
    record_audit(db, "totp.enabled", "user", request.state.user.id, request.state.user.id)
    _record_security_change(db, request)
    db.commit()
    return RedirectResponse("/settings?totp=enabled#totp", status_code=303)


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
    factor.disabled_at = utcnow()
    record_audit(db, "totp.disabled", "user", request.state.user.id, request.state.user.id)
    _record_security_change(db, request)
    db.commit()
    return RedirectResponse("/settings?totp=disabled#totp", status_code=303)
