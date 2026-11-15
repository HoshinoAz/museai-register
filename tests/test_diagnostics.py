import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from curl_cffi import requests
from curl_cffi.requests.cookies import Cookies

from app.protocol import ObservedSession
from tests.fixtures import Response


def session_with_cookies(response):
    cookies = Cookies()
    cookies.set("datr", "private-device-cookie", domain=".muse.ai")
    cookies.set("hatch_native_auth_device", "private-auth-device", domain="muse.ai")
    cookies.set("datr", "private-other-site", domain="other.example")
    cookies.set("private-cookie-name", "private-unknown-cookie", domain="muse.ai")
    return SimpleNamespace(cookies=cookies, post=Mock(return_value=response))


def test_snapshot_retains_subcode_and_cookie_names_without_secrets():
    response = Response(
        403,
        {
            "type": "registrationBlocked",
            "onboarding_failure_code": "1001",
            "checkpoint_url": "https://muse.ai/checkpoint?token=private-checkpoint",
            "detail": "private@example.test private-response-detail",
            "private-response-key": "private-response-value",
        },
    )
    inner = session_with_cookies(response)
    observed = ObservedSession(inner, lambda *args: None)
    url = "https://muse.ai/api/auth/native/register-completion?private-query"
    kwargs = {
        "headers": {"X-Hatch-CSRF-Token": "private-csrf"},
        "data": json.dumps(
            {
                "first_name": "PrivateGivenName",
                "date_of_birth": "1980-01-02",
                "remember_device": True,
                "return_to": "/?token=private-return",
                "private-payload-key": "private-payload-value",
            }
        ),
    }
    assert observed.post(url, **kwargs) is response
    inner.post.assert_called_once_with(url, **kwargs)
    snapshot = observed.protocol_diagnostics[0]
    assert snapshot["onboarding_failure_code"] == "1001"
    assert snapshot["http_status"] == 403
    assert snapshot["error_type"] == observed.last_error == "registrationBlocked"
    assert snapshot["cookie_jar_before"] == ["datr", "hatch_native_auth_device"]
    assert snapshot["cookie_jar_after"] == snapshot["cookie_jar_before"]
    assert snapshot["boolean_fields"] == {"remember_device": True}
    assert snapshot["other_payload_field_count"] == 1
    assert snapshot["checkpoint_url_present"] and snapshot["csrf_header_present"]
    serialized = json.dumps(snapshot)
    assert "private" not in serialized.lower()
    assert "PrivateGivenName" not in serialized and "1980-01-02" not in serialized


@pytest.mark.parametrize(
    "code", ["private-code", "1001@example.test", True, [], 10**20]
)
def test_non_numeric_or_oversized_failure_code_is_not_retained(code):
    response = Response(
        403, {"type": "registrationBlocked", "onboarding_failure_code": code}
    )
    observed = ObservedSession(session_with_cookies(response), lambda *args: None)
    observed.post("https://muse.ai/api/auth/native/register-completion")
    assert observed.protocol_diagnostics[0]["onboarding_failure_code"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://api.ipify.org?format=json",
        "https://other.example/api/auth/native/restart",
        "https://muse.ai/api/auth/native/private-path",
    ],
)
def test_unrelated_requests_are_not_recorded(url):
    inner = session_with_cookies(Response())
    observed = ObservedSession(inner, lambda *args: None)
    observed.post(url)
    assert observed.protocol_diagnostics == []
    inner.post.assert_called_once_with(url)


def test_transport_exception_is_preserved_and_snapshots_are_bounded():
    inner = session_with_cookies(Response())
    error = requests.exceptions.Timeout("private-transport-message")
    inner.post.side_effect = error
    observed = ObservedSession(inner, lambda *args: None)
    for _ in range(40):
        with pytest.raises(requests.exceptions.Timeout) as caught:
            observed.post("https://muse.ai/api/auth/native/register-completion")
        assert caught.value is error
    assert len(observed.protocol_diagnostics) == 32
    assert inner.post.call_count == 40
    assert "private" not in json.dumps(observed.protocol_diagnostics)
    assert observed.protocol_diagnostics[-1]["transport_exception"] == "Timeout"


@pytest.mark.parametrize("helper", ["request_snapshot", "response_snapshot"])
def test_diagnostic_failure_cannot_change_request_or_result(helper, monkeypatch):
    from app import protocol

    def fail(*args):
        raise RuntimeError("private-diagnostic-error")

    monkeypatch.setattr(protocol, helper, fail)
    response = Response(403, {"type": "registrationBlocked"})
    inner = session_with_cookies(response)
    observed = ObservedSession(inner, lambda *args: None)
    url = "https://muse.ai/api/auth/native/register-completion"
    assert observed.post(url, data="{}") is response
    inner.post.assert_called_once_with(url, data="{}")
    assert observed.last_error == "registrationBlocked"


@pytest.mark.parametrize("scenario", ["success", "blocked"])
def test_diagnostic_storage_failure_preserves_result_and_closes_sessions(
    scenario, tmp_path, monkeypatch
):
    from app.service import TaskManager
    from app.storage import Storage
    from tests.fixtures import OtpSession, client_factory, wait_for
    from tests.test_registration import account

    class ObservedOtp(OtpSession):
        closed = False

        def close(self):
            self.closed = True

    otp = ObservedOtp()
    clients = []
    store = Storage(tmp_path)
    manager = TaskManager(
        store,
        client_factory=client_factory(scenario, clients),
        otp_session_factory=lambda: otp,
    )
    original_update = manager._update

    def update(task, **values):
        if "protocol_diagnostics" in values:
            raise OSError("private-diagnostic-disk-error")
        return original_update(task, **values)

    monkeypatch.setattr(manager, "_update", update)
    try:
        task_id = manager.create([account()])[0]["id"]
        wait_for(manager, task_id)
        manager.close()
        task = manager.get(task_id)
        assert task["status"] == ("succeeded" if scenario == "success" else "failed")
        assert task["error_code"] == (
            None if scenario == "success" else "registrationBlocked"
        )
        assert clients[0].s.inner.closed and otp.closed
        assert (
            sum("register-completion" in url for _, url, _ in clients[0].s.inner.calls)
            == 1
        )
    finally:
        manager.close()
        store.close()
