from fastapi.testclient import TestClient

from app.service import TaskManager
from app.proxy_engine import ProxyFailure
from app.web import create_app
from tests.fixtures import OtpSession, client_factory, wait_for
from tests.test_registration import account


def app_for_test(tmp_path):
    return create_app(
        tmp_path,
        lambda store: TaskManager(
            store, client_factory=client_factory(), otp_session_factory=OtpSession
        ),
    )


def test_api_registration_export_and_local_guard(tmp_path):
    app = app_for_test(tmp_path)
    with TestClient(app, headers={"X-Muse-Client": "1"}) as client:
        assert client.get("/").status_code == 200
        response = client.post(
            "/api/tasks", json={"accounts": [account().model_dump(mode="json")]}
        )
        assert response.status_code == 201
        task_id = response.json()["tasks"][0]["id"]
        result = wait_for(app.state.manager, task_id)
        assert result["verified"]
        assert "fixture-secret" not in client.get("/api/tasks").text
        export = client.get(f"/api/tasks/{task_id}/credentials")
        assert export.json()["hatch_access_verified"] is True
        assert export.json()["credential_type"] == "meta_frl_and_hatch_session"
        assert export.headers["cache-control"] == "no-store"
        assert (
            client.get(
                "/api/tasks", headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/tasks", headers={"Host": "evil.example"}).status_code
            == 400
        )
        assert (
            client.get("/api/tasks", headers={"X-Muse-Client": ""}).status_code == 403
        )
        assert (
            client.get(
                "/api/tasks", headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
        assert client.get("/api/tasks/unknown").status_code == 404


def test_validation_never_reflects_proxy_credentials(tmp_path):
    app = app_for_test(tmp_path)
    with TestClient(app, headers={"X-Muse-Client": "1"}) as client:
        payload = account().model_dump(mode="json")
        payload.update(proxy="bad://private-password@host", consent=False)
        response = client.post("/api/tasks", json={"accounts": [payload]})
        assert response.status_code == 422
        assert (
            "private-password" not in response.text
            and "input" not in response.json()["detail"][0]
        )


def test_restart_preserves_public_history_and_credentials(tmp_path):
    app = app_for_test(tmp_path)
    with TestClient(app, headers={"X-Muse-Client": "1"}) as client:
        task_id = client.post(
            "/api/tasks", json={"accounts": [account().model_dump(mode="json")]}
        ).json()["tasks"][0]["id"]
        wait_for(app.state.manager, task_id)
    with TestClient(app_for_test(tmp_path), headers={"X-Muse-Client": "1"}) as client:
        assert client.get(f"/api/tasks/{task_id}").json()["verified"]
        assert (
            client.get(f"/api/tasks/{task_id}/credentials").json()["hatch_sess"]
            == "fixture-secret-cookie"
        )


def test_proxy_endpoints_validate_and_do_not_create_tasks(tmp_path, monkeypatch):
    from app import web

    received = []

    def probe(config):
        received.append(config)
        return {"connected": True, "exit_ip": "203.0.113.11"}

    monkeypatch.setattr(web, "probe_proxy", probe)
    monkeypatch.setattr(web, "install_engine", lambda proxy: received.append(proxy))
    with TestClient(app_for_test(tmp_path), headers={"X-Muse-Client": "1"}) as client:
        payload = {
            "proxy": "h:80:u-session-12345:private",
            "proxy_mode": "dynamic",
            "front_proxy_enabled": True,
            "front_proxy": "f:80:u:private",
        }
        response = client.post("/api/proxy/test", json=payload)
        assert response.status_code == 200 and response.json()["connected"]
        assert (
            received[0].proxy_mode == "dynamic"
            and received[0].front_proxy == "http://u:private@f:80"
        )
        assert "private" not in response.text
        assert client.post("/api/proxy/install", json=payload).json()["installed"]
        assert received[1] == "http://u:private@f:80"
        assert client.get("/api/tasks").json()["tasks"] == []
        assert client.get("/api/health").json()["proxy_support"] == "dynamic_and_front"
        payload["front_proxy"] = "bad://private@f"
        response = client.post("/api/proxy/test", json=payload)
        assert response.status_code == 422 and "private" not in response.text
        assert (
            client.post(
                "/api/proxy/test", json={}, headers={"X-Muse-Client": ""}
            ).status_code
            == 403
        )


def test_proxy_error_code_is_preserved(tmp_path, monkeypatch):
    from app import web

    def fail(config):
        raise ProxyFailure("proxy_check_failed", "出口 IP 查询失败")

    monkeypatch.setattr(web, "probe_proxy", fail)
    with TestClient(app_for_test(tmp_path), headers={"X-Muse-Client": "1"}) as client:
        response = client.post("/api/proxy/test", json={})
        assert (
            response.status_code == 502
            and response.json()["code"] == "proxy_check_failed"
        )
