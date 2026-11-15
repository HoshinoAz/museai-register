"""Bounded auth metadata only; never retain request bodies or cookie values."""

import json
import re
from urllib.parse import urlsplit

AUTH_COOKIES = frozenset(
    {
        "datr",
        "dpr",
        "wd",
        "ps_l",
        "ps_n",
        "h_ccva",
        "hatch_native_auth_device",
        "hatch_native_auth_csrf",
        "hatch_native_auth_state",
        "hatch_native_auth_temp",
    }
)
PAYLOAD_FIELDS = frozenset(
    {
        "resume",
        "contact_point",
        "otp_code",
        "remember_device",
        "supports_muse_link_transparency",
        "date_of_birth",
        "first_name",
        "last_name",
        "tos_content_version",
        "password",
        "return_to",
    }
)
RESPONSE_FIELDS = frozenset(
    {
        "type",
        "reason",
        "title",
        "detail",
        "status",
        "onboarding_failure_code",
        "checkpoint_url",
        "registration_state",
        "flow",
        "requires_name",
        "has_muse_account",
        "tos",
        "auth_meta_session",
        "csrf_token",
    }
)
AUTH_PATHS = frozenset(
    f"/api/auth/native/{name}"
    for name in (
        "restart",
        "send-otp",
        "resend-otp",
        "confirm-otp",
        "register-completion",
    )
)


def identifier(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", value)
        else None
    )


def cookie_names(session):
    # This is jar state, not proof that a cookie was sent on the wire.
    return sorted(
        {
            cookie.name
            for cookie in session.cookies.jar
            if cookie.name in AUTH_COOKIES
            and cookie.domain.lstrip(".") == "muse.ai"
            and not cookie.is_expired()
        }
    )


def request_snapshot(session, method, url, kwargs):
    parsed = urlsplit(url)
    if parsed.hostname != "muse.ai" or parsed.path not in AUTH_PATHS:
        return None
    payload = kwargs.get("data")
    try:
        payload = json.loads(payload) if isinstance(payload, (str, bytes)) else {}
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    headers = {
        key.lower(): value for key, value in (kwargs.get("headers") or {}).items()
    }
    return {
        "method": method,
        "path": parsed.path,
        "payload_fields": sorted(PAYLOAD_FIELDS.intersection(payload)),
        "other_payload_field_count": len(payload.keys() - PAYLOAD_FIELDS),
        "boolean_fields": {
            key: payload[key]
            for key in ("resume", "remember_device", "supports_muse_link_transparency")
            if type(payload.get(key)) is bool
        },
        "csrf_header_present": bool(headers.get("x-hatch-csrf-token")),
        "waterfall_header_present": bool(headers.get("x-hatch-waterfall-id")),
        "cookie_jar_before": cookie_names(session),
    }


def response_snapshot(session, response, data):
    code = data.get("onboarding_failure_code")
    if type(code) is int:
        code = str(code)
    code = (
        code if isinstance(code, str) and re.fullmatch(r"[0-9]{1,12}", code) else None
    )
    return {
        "http_status": response.status_code,
        "response_fields": sorted(RESPONSE_FIELDS.intersection(data)),
        "error_type": identifier(data.get("type") or data.get("reason")),
        "onboarding_failure_code": code,
        "checkpoint_url_present": isinstance(data.get("checkpoint_url"), str)
        and bool(data["checkpoint_url"]),
        "registration_state": identifier(data.get("registration_state")),
        "cookie_jar_after": cookie_names(session),
    }
