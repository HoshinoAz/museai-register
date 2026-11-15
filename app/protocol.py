import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .diagnostics import request_snapshot, response_snapshot
from .fingerprints import create_identity
from .proxy_engine import ProxyFailure, ProxyRoute, exit_ip

UPSTREAM = Path(__file__).resolve().parents[1] / "vendor" / "muse-protocol"
if not (UPSTREAM / "musepy" / "client.py").is_file():
    raise RuntimeError("缺少协议依赖，请先运行 git submodule update --init --recursive")
sys.path.insert(0, str(UPSTREAM))

from musepy.client import BASE, HEADERS_HTML, HEADERS_JSON, MuseClient  # noqa: E402
from musepy.client import MuseError as MuseError  # noqa: E402
from musepy.otp import select_otp as select_otp  # noqa: E402


class ProtocolFailure(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def json_object(response):
    try:
        data = response.json()
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


class ObservedSession:
    def __init__(self, session, emit, identity=None):
        self.inner = session
        self.emit = emit
        self.last_error = None
        self.identity = identity
        self.protocol_diagnostics = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _request(self, method, url, **kwargs):
        self.last_error = None
        if self.identity:
            expected = self.identity.headers()
            headers = dict(kwargs.get("headers") or {})
            if (
                any(
                    key.lower() in expected and expected[key.lower()] != value
                    for key, value in headers.items()
                )
                or kwargs.get("impersonate", self.identity.tls_profile)
                != self.identity.tls_profile
                or kwargs.get("default_headers", False) is not False
                or any(key in kwargs for key in ("ja3", "akamai", "extra_fp"))
            ):
                raise ProtocolFailure(
                    "fingerprint_mismatch", "请求试图覆盖账号固定指纹，已在发送前停止"
                )
            kwargs.update(
                headers=expected | headers,
                impersonate=self.identity.tls_profile,
                default_headers=False,
                accept_encoding="gzip, deflate, br, zstd",
            )
        try:
            snapshot = request_snapshot(self.inner, method, url, kwargs)
        except Exception:
            snapshot = None
        try:
            response = getattr(self.inner, method.lower())(url, **kwargs)
        except Exception as error:
            if snapshot is not None:
                snapshot["transport_exception"] = type(error).__name__
                self.protocol_diagnostics = (self.protocol_diagnostics + [snapshot])[
                    -32:
                ]
            raise
        path = urlsplit(url).path or "/"
        self.emit(f"{method} {path} · HTTP {response.status_code}", "http")
        if response.status_code >= 400:
            data = json_object(response)
            code = (
                data.get("type") or data.get("reason") or f"http_{response.status_code}"
            )
            self.last_error = (
                code
                if isinstance(code, str)
                and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", code)
                else f"http_{response.status_code}"
            )
        if snapshot is not None:
            try:
                snapshot.update(
                    response_snapshot(self.inner, response, json_object(response))
                )
            except Exception:
                snapshot["response_capture_failed"] = True
            self.protocol_diagnostics = (self.protocol_diagnostics + [snapshot])[-32:]
        return response

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)


class ProtocolClient(MuseClient):
    """Keep upstream wire behavior; add stable flow identity and explicit checks."""

    def __init__(self, config, emit):
        self.identity = config._identity or create_identity(
            config.language, config.browser_profile
        )
        self.identity.check_runtime()
        self.route = ProxyRoute(config, uuid.uuid4().hex, emit)
        try:
            super().__init__(
                proxy=self.route.url or None,
                impersonate=self.identity.tls_profile,
                lang=self.identity.language,
                timeout=config.request_timeout,
            )
            self.s.trust_env = False
            self.s.default_headers = False
            self.s.headers.update(self.identity.headers())
        except Exception:
            self.route.close()
            raise
        self.proxy_check = self.route.enabled and config.proxy_check
        self.exit_address = None
        self.waterfall_id = str(uuid.uuid4())
        self.s = ObservedSession(self.s, emit, self.identity)
        self.completion_sent = False
        emit(
            f"账号协议指纹：{self.identity.profile_id[:12]} · Chrome 152 / {self.identity.platform} · UA / Client Hints / TLS 固定",
            "info",
        )

    def check_proxy(self):
        address = exit_ip(self.s, timeout=self.timeout)
        if self.exit_address is not None and address != self.exit_address:
            raise ProxyFailure(
                "proxy_ip_changed", "出口 IP 与注册前采样不一致，已停止；未提交账号创建"
            )
        self.exit_address = address
        return address

    def close(self):
        try:
            super().close()
        finally:
            self.route.close()

    def _h(self, base, extra=None):
        headers = super()._h(base, extra)
        headers.update(self.identity.headers())
        if headers.get("sec-fetch-mode") == "navigate":
            headers.update({"sec-fetch-user": "?1", "upgrade-insecure-requests": "1"})
        if base is HEADERS_JSON and self.csrf is None and hasattr(self, "waterfall_id"):
            headers["X-Hatch-Waterfall-Id"] = self.waterfall_id
        return headers

    def open_home(self):
        return self.s.get(
            BASE + "/",
            impersonate=self.fp,
            headers=self._h(HEADERS_HTML, {"sec-fetch-site": "none"}),
        )

    def _auth_headers(self):
        headers = super()._auth_headers()
        headers["X-Hatch-Waterfall-Id"] = self.waterfall_id
        return headers

    def resend_otp(self):
        response = self.s.post(
            BASE + "/api/auth/native/resend-otp",
            impersonate=self.fp,
            headers=self._auth_headers(),
            data="{}",
        )
        data = json_object(response)
        if (
            response.status_code not in (200, 201)
            or data.get("registration_state") != "otp_sent"
        ):
            raise ProtocolFailure(
                self.s.last_error or "resend_unconfirmed", "验证码重发未获服务端确认"
            )
        return data

    def register_completion(self, *args, **kwargs):
        if not self.tos_content_version:
            raise ProtocolFailure(
                "tos_version_missing", "验证响应未提供动态条款版本，已停止创建"
            )
        if self.completion_sent:
            raise ProtocolFailure(
                "completion_already_sent", "同一任务不能重复提交账号创建"
            )
        self.completion_sent = True
        data = super().register_completion(*args, **kwargs)
        meta = data.get("auth_meta_session")
        if (
            not isinstance(meta, dict)
            or not isinstance(meta.get("frl_access_token"), str)
            or not meta["frl_access_token"].strip()
            or not meta.get("frl_account_id")
        ):
            raise ProtocolFailure(
                "creation_uncertain",
                "创建接口返回 HTTP 200，但未返回明确会话结果；请先检查邮箱，勿重复注册",
            )
        return data

    def validate_access(self):
        response = self.invite()
        data = json_object(response)
        if (
            response.status_code != 200
            or not data
            or data.get("success") is False
            or data.get("error")
        ):
            raise ProtocolFailure(
                self.s.last_error or "access_unconfirmed",
                "账号已创建，但 Hatch 会话访问尚未验证",
            )
        if not self.session_cookie():
            raise ProtocolFailure(
                "session_cookie_missing", "接口可访问，但未取得 hatch_sess"
            )

    def redeem_checked(self, code):
        # Upstream wake_vm ignores the wake status. Verify it before redemption.
        page = self.s.get(
            BASE + "/?aymh_complete=1",
            impersonate=self.fp,
            headers=self._h({"accept": "text/html"}),
        )
        match = re.search(r'vmId[\\"]*:[\\"]*([0-9a-fA-F-]{36})', page.text)
        if page.status_code != 200 or not match:
            raise ProtocolFailure(
                "vm_unresolved", "会话已验证，但未解析到 VM，未提交兑换"
            )
        body = {
            "vm_id": match.group(1),
            "retry_count": 0,
            "connect_attempt_id": str(uuid.uuid4()),
            "wake_trigger": "mount",
            "open_id": str(uuid.uuid4()),
            "away_ms": 0,
        }
        response = self.s.post(
            BASE + "/api/hatch/vm/wake",
            impersonate=self.fp,
            headers=self._h(HEADERS_JSON),
            data=json.dumps(body),
        )
        if response.status_code != 200:
            raise ProtocolFailure(
                self.s.last_error or "wake_unconfirmed", "VM 未成功唤醒，未提交兑换"
            )
        response = self.stateful_sharing()
        if response.status_code != 200:
            raise ProtocolFailure(
                self.s.last_error or "sharing_unconfirmed",
                "会话初始化未完成，未提交兑换",
            )
        response = self.s.post(
            BASE + "/api/hatch/invite-code/redeem",
            impersonate=self.fp,
            headers=self._h(HEADERS_JSON),
            data=json.dumps({"code": code, "supportsRedemptionStatus": True}),
        )
        data = json_object(response)
        reason = data.get("reason")
        if (
            response.status_code == 200
            and data.get("success") is True
            and data.get("redemptionStatus") in (None, "redeemed")
        ):
            return "redeemed", self.scratch_code()
        if reason in {"already_redeemed", "used_up", "invalid"}:
            return reason, None
        raise ProtocolFailure(
            self.s.last_error or "redemption_unconfirmed",
            "会话已验证，但邀请码兑换未获确认",
        )

    def credentials(self, email, meta=None):
        return {
            "email": email,
            "credential_type": "meta_frl_and_hatch_session",
            "protocol_fingerprint": self.identity.public()
            | {"headers": self.identity.headers()},
            "frl_account_id": (meta or {}).get("frl_account_id"),
            "frl_access_token": (meta or {}).get("frl_access_token"),
            "hatch_sess": self.session_cookie(),
            "cookies": [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                    "secure": c.secure,
                    "expires": c.expires,
                }
                for c in self.s.cookies.jar
            ],
        }
