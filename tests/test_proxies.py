import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import proxy_engine
from app.models import ProxyInput
from app.protocol import ProtocolClient
from app.proxies import parse_proxy, resolve_proxy
from app.proxy_engine import (
    ProxyFailure,
    build_config,
    exit_ip,
    install_engine,
    probe_proxy,
)
from tests.fixtures import Response
from tests.test_registration import account


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("host.test:8000:user:p@ss:word", "http://user:p%40ss%3Aword@host.test:8000"),
        ("host.test:8000", "http://host.test:8000"),
        ("https://u%40s:p%2Fss@host.test:443/", "https://u%40s:p%2Fss@host.test:443"),
        ("socks5h://[::1]:1080", "socks5h://[::1]:1080"),
        ("gate.kookeey.info:1000:u:p", "socks5h://u:p@gate.kookeey.info:1000"),
        ("http://u:p@geo.iprocket.io:59999", "socks5h://u:p@geo.iprocket.io:59999"),
        (
            "prem.country.iprocket.io:9595:u:p",
            "socks5h://u:p@prem.country.iprocket.io:9595",
        ),
    ],
)
def test_proxy_normalization(raw, expected):
    assert parse_proxy(raw).url() == expected


@pytest.mark.parametrize(
    "raw",
    [
        "bad://h:80",
        "http://h:0",
        "h:65536",
        "h:word",
        "http://h:80/path",
        "http://h:80?q=1",
        "http://h:80#x",
        "http://u:%0A@h:80",
        "http://h:80\\",
        "no-port",
    ],
)
def test_invalid_proxy_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_proxy(raw)


@pytest.mark.parametrize("marker", ["session", "lsid", "sid", "sessid"])
def test_dynamic_identity_is_stable_and_preserves_provider_options(marker):
    value = f"host.test:8000:customer-country-us-{marker}-123456789-lifetime-30:secret"
    one, isolated = resolve_proxy(value, "dynamic", "task-one")
    repeat, _ = resolve_proxy(value, "dynamic", "task-one")
    two, _ = resolve_proxy(value, "dynamic", "task-two")
    assert isolated and one == repeat and one != two
    token = one.username.split(f"{marker}-")[1].split("-")[0]
    assert token.isdigit() and len(token) == 9
    assert one.username.startswith("customer-country-us-")
    assert one.username.endswith("-lifetime-30") and one.password == "secret"
    assert resolve_proxy(value, "fixed", "task-one")[0] == parse_proxy(value)


def test_session_templates_in_both_credentials_and_unrecognized_identity():
    endpoint, isolated = resolve_proxy(
        "h:80:u-{session}:p-{session}", "dynamic", "task"
    )
    assert isolated and endpoint.username[2:] == endpoint.password[2:]
    assert "{session}" not in endpoint.url()
    assert resolve_proxy("h:80:u:p", "dynamic", "task")[1] is False
    assert resolve_proxy("", "dynamic", "task") == (None, False)
    with pytest.raises(ValidationError):
        ProxyInput(front_proxy_enabled=True)


def test_chain_direction_tls_and_no_direct_fallback():
    config = build_config(
        parse_proxy("https://u:p@task.test:443"),
        parse_proxy("socks5h://front.test:1080"),
        10001,
        10002,
        "local-pass",
        "local-secret",
    )
    front, task = config["proxies"]
    assert front["name"] == "FRONT" and "dialer-proxy" not in front
    assert task["dialer-proxy"] == "FRONT" and task["tls"] is True
    assert task["skip-cert-verify"] is False and task["sni"] == "task.test"
    assert config["rules"] == ["MATCH,TASK"]
    assert config["allow-lan"] is False
    assert config["bind-address"] == "127.0.0.1"
    assert config["authentication"] == ["muse:local-pass"]
    assert "DIRECT" not in json.dumps(config)


def test_install_checksum_failure_never_creates_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(proxy_engine.platform, "system", lambda: "Windows")
    monkeypatch.setattr(proxy_engine.platform, "machine", lambda: "AMD64")

    class Download:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200, content=b"tampered")

    monkeypatch.setattr(proxy_engine.requests, "Session", Download)
    with pytest.raises(ProxyFailure) as caught:
        install_engine()
    assert caught.value.code == "engine_checksum_failed"
    assert not proxy_engine.engine_path().exists()


def test_missing_engine_never_falls_back_to_direct(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DATA_DIR", str(tmp_path))
    with pytest.raises(ProxyFailure) as caught:
        ProtocolClient(
            account(front_proxy_enabled=True, front_proxy="h:80"), lambda *args: None
        )
    assert caught.value.code == "proxy_engine_missing"


def test_task_session_configuration_and_chain_cleanup(monkeypatch):
    chains = []

    class Chain:
        def __init__(self, selected, front):
            self.selected, self.front = selected, front
            self.url = "http://muse:local@127.0.0.1:8000"
            self.closed = False
            chains.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(proxy_engine, "ProxyChain", Chain)
    config = account(
        proxy="h:80:u-session-12345678:p",
        proxy_mode="dynamic",
        front_proxy_enabled=True,
        front_proxy="f:80",
    )
    first = ProtocolClient(config, lambda *args: None)
    second = ProtocolClient(config, lambda *args: None)
    try:
        assert chains[0].selected != chains[1].selected
        assert first.s.trust_env is False and first.proxy == chains[0].url
        assert first.s.inner.proxies == {"http": chains[0].url, "https": chains[0].url}
    finally:
        first.close()
        second.close()
    assert all(chain.closed for chain in chains)


@pytest.mark.parametrize(
    "payload", [{}, {"ip": "not-an-ip"}, {"ip": "<script>secret</script>"}, {"ip": 123}]
)
def test_ip_query_validates_response(payload):
    session = SimpleNamespace(get=lambda *args, **kwargs: Response(payload=payload))
    with pytest.raises(ProxyFailure) as caught:
        exit_ip(session)
    assert caught.value.code == "proxy_check_failed"


def test_probe_samples_same_route_and_never_posts(monkeypatch):
    class ProbeSession:
        def __init__(self, **kwargs):
            assert kwargs["proxy"] == "http://u:p@h:80" and not kwargs["trust_env"]
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            self.calls.append(url)
            return Response(payload={"ip": "203.0.113.11"})

    monkeypatch.setattr(proxy_engine.requests, "Session", ProbeSession)
    result = probe_proxy(ProxyInput(proxy="h:80:u:p"))
    assert result["connected"] and result["exit_ip"] == "203.0.113.11"
    assert "u:p" not in json.dumps(result)
