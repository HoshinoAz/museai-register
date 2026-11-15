import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import AccountInput
from app.protocol import select_otp
from app.service import TaskManager
from app.storage import Storage
from tests.fixtures import OtpSession, client_factory, wait_for


def account(**overrides):
    values = {
        "email": "fixture@example.test",
        "first_name": "Haru",
        "last_name": "Mori",
        "date_of_birth": "1993-11-14",
        "otp_url": "https://mail.example.test/unique-mailbox",
        "consent": True,
    }
    return AccountInput(**(values | overrides))


@pytest.fixture
def make_manager(tmp_path):
    resources = []

    def create(scenario="success"):
        store = Storage(tmp_path / str(len(resources)))
        clients = []
        manager = TaskManager(
            store,
            client_factory=client_factory(scenario, clients),
            otp_session_factory=OtpSession,
        )
        resources.append((manager, store))
        return manager, store, clients

    yield create
    for manager, store in resources:
        manager.close()
        store.close()


def test_full_protocol_and_encrypted_export(make_manager):
    manager, store, clients = make_manager()
    created = manager.create([account(invite_code="FIXTURE")])[0]
    result = wait_for(manager, created["id"])
    assert result["status"] == "succeeded"
    assert result["registered"] and result["verified"] and result["has_credentials"]
    assert (
        result["invite_status"] == "redeemed" and result["own_invite"] == "FIXTURE-OWN"
    )
    credentials = store.credentials(created["id"])
    assert credentials["hatch_sess"] == "fixture-secret-cookie"
    assert credentials["frl_access_token"] == "fixture-secret-frl"
    serialized = json.dumps(result)
    assert "fixture-secret" not in serialized and "fixture-csrf" not in serialized
    with store.lock:
        sealed = store.connection.execute("SELECT credentials FROM tasks").fetchone()[0]
    assert b"fixture-secret" not in sealed
    calls = clients[0].s.inner.calls
    native = [call for call in calls if "/api/auth/native/" in call[1]]
    identities = {call[2]["headers"]["X-Hatch-Waterfall-Id"] for call in native}
    assert len(identities) == 1
    assert sum("register-completion" in call[1] for call in calls) == 1
    assert sum("accept-tos" in call[1] for call in calls) == 1
    assert clients[0].s.inner.closed


@pytest.mark.parametrize(
    "scenario, expected, registered, verified",
    [
        ("existing", "not_registration_flow", False, False),
        ("missing_tos", "tos_version_missing", False, False),
        ("blocked", "registrationBlocked", False, False),
        ("waitlist", "region_waitlist", True, False),
        ("tos_denied", "tosDenied", True, False),
        ("tos_false", "tos_acceptance_unconfirmed", True, False),
        ("invalid_access", "access_unconfirmed", True, False),
        ("bad_redirect", "protocol_unconfirmed", True, False),
        ("timeout", "creation_uncertain", False, False),
        ("empty_completion", "creation_uncertain", False, False),
    ],
)
def test_rejections_never_become_success(
    make_manager, scenario, expected, registered, verified
):
    manager, store, clients = make_manager(scenario)
    created = manager.create([account()])[0]
    result = wait_for(manager, created["id"])
    assert result["error_code"] == expected
    assert result["registered"] is registered and result["verified"] is verified
    assert result["status"] == ("partial" if registered else "failed")
    assert "fixture-secret" not in json.dumps(result) and "never-log" not in json.dumps(
        result
    )
    paths = [call[1] for call in clients[0].s.inner.calls]
    assert sum("register-completion" in path for path in paths) <= 1
    assert sum("accept-tos" in path for path in paths) <= 1
    if expected == "creation_uncertain" or registered:
        with pytest.raises(ValueError):
            manager.create([account()])
    if scenario == "bad_redirect":
        assert not any("untrusted.example" in path for path in paths)


@pytest.mark.parametrize(
    "scenario,status", [("wake_failed", "partial"), ("invalid_invite", "succeeded")]
)
def test_invite_failure_preserves_verified_account(make_manager, scenario, status):
    manager, store, clients = make_manager(scenario)
    created = manager.create([account(invite_code="FIXTURE")])[0]
    result = wait_for(manager, created["id"])
    assert result["status"] == status and result["registered"] and result["verified"]
    if scenario == "wake_failed":
        assert not any(
            "invite-code/redeem" in call[1] for call in clients[0].s.inner.calls
        )
    else:
        assert result["invite_status"] == "invalid"


def test_manual_otp_wrong_then_correct(make_manager):
    manager, store, clients = make_manager()
    created = manager.create([account(otp_url="")])[0]
    task_id = created["id"]
    wait_for(manager, task_id, lambda item: item["status"] == "waiting_otp")
    manager.submit_otp(task_id, "000000")
    wait_for(
        manager,
        task_id,
        lambda item: (
            item["status"] == "waiting_otp"
            and any("验证码被拒绝" in event["message"] for event in item["events"])
        ),
    )
    manager.submit_otp(task_id, "123456")
    assert wait_for(manager, task_id)["verified"]


def test_cancel_while_waiting_never_creates(make_manager):
    manager, store, clients = make_manager()
    task_id = manager.create([account(otp_url="")])[0]["id"]
    wait_for(manager, task_id, lambda item: item["status"] == "waiting_otp")
    manager.cancel(task_id)
    assert wait_for(manager, task_id)["status"] == "cancelled"
    assert not any(
        "register-completion" in call[1] for call in clients[0].s.inner.calls
    )
    with pytest.raises(ValueError):
        manager.submit_otp(task_id, "123456")


def test_resend_is_serial_and_cooldown_enforced(make_manager):
    manager, store, clients = make_manager()
    task_id = manager.create([account(otp_url="")])[0]["id"]
    wait_for(manager, task_id, lambda item: item["status"] == "waiting_otp")
    with pytest.raises(ValueError, match="秒后"):
        manager.resend_otp(task_id)
    manager.tasks[task_id].last_send -= 46
    manager.resend_otp(task_id)
    wait_for(
        manager,
        task_id,
        lambda item: any("确认重发" in event["message"] for event in item["events"]),
    )
    manager.submit_otp(task_id, "123456")
    assert wait_for(manager, task_id)["verified"]


def test_batch_validation_is_atomic(make_manager):
    manager, _, _ = make_manager()
    with pytest.raises(ValueError, match="重复邮箱"):
        manager.create([account(), account()])
    assert manager.list() == []


def test_batch_rejection_stops_queued_mailboxes(make_manager):
    manager, _, clients = make_manager("blocked")
    created = manager.create(
        [account(email="one@example.test"), account(email="two@example.test")]
    )
    assert wait_for(manager, created[0]["id"])["error_code"] == "registrationBlocked"
    assert wait_for(manager, created[1]["id"])["status"] == "cancelled"
    assert len(clients) == 1


def test_restart_during_creation_remains_uncertain(tmp_path):
    store = Storage(tmp_path)
    store.save(
        {
            "id": "unknown-create",
            "email": "fixture@example.test",
            "status": "running",
            "stage": "completion",
            "registered": False,
            "verified": False,
            "created_at": "2026-11-16T00:00:00Z",
            "events": [],
        }
    )
    manager = TaskManager(store)
    try:
        assert manager.get("unknown-create")["error_code"] == "creation_uncertain"
        with pytest.raises(ValueError):
            manager.create([account()])
    finally:
        manager.close()
        store.close()


def test_restart_marks_inflight_interrupted(tmp_path):
    store = Storage(tmp_path)
    original = {
        "id": "before-restart",
        "email": "fixture@example.test",
        "status": "running",
        "registered": True,
        "verified": False,
        "created_at": "2026-11-16T00:00:00Z",
        "events": [],
    }
    store.save(original, {"hatch_sess": "fixture-secret-cookie"})
    manager = TaskManager(store)
    try:
        assert manager.get("before-restart")["status"] == "interrupted"
        assert (
            store.credentials("before-restart")["hatch_sess"] == "fixture-secret-cookie"
        )
    finally:
        manager.close()
        store.close()


def test_otp_freshness_and_ambiguity():
    boundary = datetime.now(timezone.utc)
    fresh = (boundary + timedelta(seconds=1)).isoformat()
    stale = (boundary - timedelta(seconds=1)).isoformat()
    assert (
        select_otp(
            [{"otp": "000000", "time": stale}, {"otp": "123456", "time": fresh}],
            boundary,
        )
        == "123456"
    )
    assert (
        select_otp(
            [{"otp": "123456", "time": fresh}, {"otp": "654321", "time": fresh}],
            boundary,
        )
        is None
    )
    assert (
        select_otp([{"otp": "123456", "time": "2026-11-16T00:00:00"}], boundary) is None
    )
