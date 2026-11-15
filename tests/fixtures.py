"""Scripted HTTP fixture. Never opens an external connection."""

import json
import time
from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie, CookieJar
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from curl_cffi import requests

from app.protocol import ObservedSession, ProtocolClient


def flight_html(waitlist=False):
    payload = {
        "resolvedDestination": "waitlist" if waitlist else "main_app",
        "status": {"isGeoGated": waitlist, "hasHatchAccess": not waitlist},
        "disclosureText": "Fixture dynamic disclosure.\nA second line.",
        "disclosureVersion": "HATCH_ROW_v0",
    }
    row = "a1:" + json.dumps(payload) + "\n"
    return (
        "<html><script>self.__next_f.push("
        + json.dumps([1, row])
        + ");</script></html>"
    )


class Response:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self.text = (
            text
            if text is not None
            else json.dumps(payload if payload is not None else {})
        )
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class FixtureSession:
    def __init__(self, scenario="success"):
        self.scenario = scenario
        self.calls = []
        self.cookies = SimpleNamespace(jar=CookieJar())
        self.closed = False
        self.otp_confirmed = False
        self.ip_queries = 0

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        path = parsed.path
        self.calls.append((method, url, kwargs))
        if parsed.hostname == "api.ipify.org":
            self.ip_queries += 1
            if self.scenario == "ip_failed":
                raise requests.exceptions.Timeout("private proxy credentials")
            return Response(
                payload={
                    "ip": "203.0.113.12"
                    if self.scenario == "ip_changed" and self.ip_queries > 1
                    else "203.0.113.11"
                }
            )
        body = kwargs.get("data")
        payload = (
            json.loads(body)
            if body
            and kwargs.get("headers", {}).get("content-type") == "application/json"
            else None
        )
        if path == "/api/auth/native/restart":
            assert payload == {"resume": False}
            return Response(201, {"csrf_token": "fixture-csrf", "flow_state": "email"})
        if path == "/api/auth/native/send-otp":
            if self.scenario == "existing":
                return Response(
                    201,
                    {
                        "registration_state": "otp_sent",
                        "flow": "login",
                        "has_muse_account": True,
                    },
                )
            return Response(
                201,
                {
                    "registration_state": "otp_sent",
                    "flow": "register",
                    "has_muse_account": False,
                },
            )
        if path == "/api/auth/native/resend-otp":
            return Response(201, {"registration_state": "otp_sent"})
        if path == "/api/auth/native/confirm-otp":
            assert payload["supports_muse_link_transparency"] is True
            assert payload["remember_device"] is True
            if payload["otp_code"] == "000000":
                return Response(
                    400, {"type": "invalidOtp", "private": "never-log-this-body"}
                )
            self.otp_confirmed = True
            response = {"registration_state": "completion_required"}
            if self.scenario != "missing_tos":
                response["tos"] = {"content_version": "fixture-dynamic-tos-v7"}
            return Response(200, response)
        if path == "/api/auth/native/register-completion":
            assert self.otp_confirmed
            assert payload["tos_content_version"] == "fixture-dynamic-tos-v7"
            assert payload["first_name"] == "Haru" and payload["last_name"] == "Mori"
            if self.scenario == "blocked":
                return Response(
                    403,
                    {"type": "registrationBlocked", "private": "never-log-this-body"},
                )
            if self.scenario == "timeout":
                raise requests.exceptions.Timeout(
                    "fixture private URL must not appear in logs"
                )
            if self.scenario == "empty_completion":
                return Response(200, {})
            cookie = Cookie(
                0,
                "hatch_sess",
                "fixture-secret-cookie",
                None,
                False,
                ".muse.ai",
                True,
                True,
                "/",
                True,
                True,
                None,
                True,
                None,
                None,
                {},
                False,
            )
            self.cookies.jar.set_cookie(cookie)
            return Response(
                200,
                {
                    "auth_meta_session": {
                        "frl_access_token": "fixture-secret-frl",
                        "frl_account_id": "fixture-account",
                    }
                },
            )
        if parsed.hostname == "auth.meta.com":
            assert method == "POST"
            assert parse_qs(body) == {"frl_access_token": ["fixture-secret-frl"]}
            location = (
                "https://untrusted.example/steal"
                if self.scenario == "bad_redirect"
                else "https://muse.ai/access/disclosure?transient=fixture"
            )
            return Response(302, headers={"location": location})
        if path == "/access/disclosure":
            assert kwargs["allow_redirects"] is False
            return Response(text=flight_html(self.scenario == "waitlist"))
        if path == "/api/hatch/accept-tos":
            assert (
                payload["disclosure_text"]
                == "Fixture dynamic disclosure.\nA second line."
            )
            assert payload["disclosure_version"] == "HATCH_ROW_v0"
            assert kwargs["headers"]["referer"] == "https://muse.ai/access/disclosure"
            assert not payload["saw_meta_ai_tos"]
            if self.scenario == "tos_denied":
                return Response(403, {"type": "tosDenied"})
            return Response(200, {"success": self.scenario != "tos_false"})
        if path == "/api/hatch/invite":
            if method == "POST":
                return Response(200, {"code": "FIXTURE-OWN"})
            if self.scenario == "invalid_access":
                return Response(200, {"success": False})
            return Response(200, {"code": None, "available": True})
        if path == "/api/hatch/vm/wake":
            assert payload["wake_trigger"] == "mount" and payload["away_ms"] == 0
            return Response(
                503 if self.scenario == "wake_failed" else 200, {"success": True}
            )
        if path == "/api/hatch/stateful-sharing":
            return Response(200, {"success": True})
        if path == "/api/hatch/invite-code/redeem":
            if self.scenario == "invalid_invite":
                return Response(200, {"success": False, "reason": "invalid"})
            return Response(200, {"success": True, "redemptionStatus": "redeemed"})
        if path == "/" and method == "GET":
            if self.scenario == "bootstrap_timeout":
                raise requests.exceptions.Timeout("private proxy credentials")
            return Response(
                text='<html><body>{"vmId":"12345678-1234-1234-1234-123456789abc"}</body></html>'
            )
        raise AssertionError(f"Unexpected fixture request: {method} {path}")

    def close(self):
        self.closed = True


class OtpSession:
    def get(self, url, **kwargs):
        return Response(
            200,
            [
                {
                    "otp": "123456",
                    "time": (
                        datetime.now(timezone.utc) + timedelta(seconds=1)
                    ).isoformat(),
                }
            ],
        )

    def close(self):
        pass


def client_factory(scenario="success", clients=None):
    def create(config, emit):
        # No engine or external connection in scripted UI / registration tests.
        client = ProtocolClient(
            config.model_copy(update={"front_proxy_enabled": False}), emit
        )
        client.proxy_check = (
            bool(config.proxy or config.front_proxy_enabled) and config.proxy_check
        )
        client.s.close()
        client.s = ObservedSession(FixtureSession(scenario), emit, client.identity)
        if clients is not None:
            clients.append(client)
        return client

    return create


def wait_for(manager, task_id, predicate=None, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        task = manager.get(task_id)
        if (
            predicate(task)
            if predicate
            else task["status"]
            in {"succeeded", "partial", "failed", "cancelled", "interrupted"}
        ):
            return task
        time.sleep(0.015)
    raise AssertionError(f"Task did not settle: {manager.get(task_id)}")
