import json
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.fingerprints import (
    BrowserIdentity,
    FingerprintFailure,
    PROFILES,
    create_identity,
    restore_identity,
)
from app.protocol import (
    HEADERS_HTML,
    HEADERS_JSON,
    ObservedSession,
    ProtocolClient,
    ProtocolFailure,
)
from app.service import TaskManager
from app.storage import Storage
from tests.fixtures import FixtureSession, OtpSession, client_factory, wait_for
from tests.test_registration import account


def test_seed_recreates_identity_and_email_profiles_are_independent():
    first = create_identity(seed="ab" * 32)
    assert first == create_identity(seed="ab" * 32)
    assert first.profile_id != create_identity(seed="cd" * 32).profile_id
    assert (
        first.public()["sha256"] == create_identity(seed="ab" * 32).public()["sha256"]
    )
    assert "seed" not in first.public()
    assert first.headers()["accept-language"] == "ja-JP,ja;q=0.9"
    assert first.tls_profile.startswith("chrome152")


@pytest.mark.parametrize("profile", PROFILES)
def test_headers_and_native_profile_are_consistent(profile):
    identity = create_identity("zh-CN", profile)
    identity.check_runtime()
    headers = identity.headers()
    assert "Chrome/152.0.0.0" in headers["user-agent"]
    assert '"Google Chrome";v="152"' in headers["sec-ch-ua"]
    assert headers["sec-ch-ua-platform"] == f'"{identity.platform}"'
    assert ("Macintosh" in headers["user-agent"]) == (identity.platform == "macOS")
    assert headers["accept-language"] == "zh-CN,zh;q=0.9"


@pytest.mark.parametrize("seed", ["", "ab", "x" * 64, "AB" * 32])
def test_invalid_seed_is_rejected(seed):
    with pytest.raises(FingerprintFailure):
        BrowserIdentity(seed, "chrome152", "ja")


def test_unavailable_native_profile_stops_without_downgrade(monkeypatch):
    from app import fingerprints

    class Unavailable:
        closed = False

        def impersonate(self, profile, default_headers):
            assert profile == "chrome152" and default_headers is False
            return 1

        def close(self):
            self.closed = True

    curl = Unavailable()
    monkeypatch.setattr(fingerprints, "Curl", lambda: curl)
    with pytest.raises(FingerprintFailure) as caught:
        create_identity(preference="chrome152").check_runtime()
    assert caught.value.code == "fingerprint_unsupported" and curl.closed


def test_identity_is_encrypted_reused_and_never_rotated_on_conflict(tmp_path):
    with_store = Storage(tmp_path)
    first = with_store.identity_for("One@Example.test", "ja")
    second = with_store.identity_for("two@example.test", "ja")
    assert first.profile_id != second.profile_id
    assert with_store.identity_for("one@example.TEST", "ja") == first
    with pytest.raises(ValueError, match="固定协议指纹"):
        with_store.identity_for("one@example.test", "en-US")
    opposite = (
        "chrome152" if first.tls_profile.endswith("_macos") else "chrome152_macos"
    )
    with pytest.raises(ValueError):
        with_store.identity_for("one@example.test", "ja", opposite)
    blob = with_store.connection.execute(
        "SELECT profile FROM identities LIMIT 1"
    ).fetchone()[0]
    assert first.seed.encode() not in blob and b"chrome152" not in blob
    with_store.close()
    reopened = Storage(tmp_path)
    try:
        assert reopened.identity_for("one@example.test", "ja") == first
    finally:
        reopened.close()


def test_corrupted_stored_identity_fails_without_replacement(tmp_path):
    store = Storage(tmp_path)
    try:
        store.identity_for("one@example.test", "ja")
        with store.connection:
            store.connection.execute("UPDATE identities SET profile=?", (b"invalid",))
        with pytest.raises(FingerprintFailure) as caught:
            store.identity_for("one@example.test", "ja")
        assert caught.value.code == "fingerprint_restore_failed"
        assert (
            store.connection.execute("SELECT profile FROM identities").fetchone()[0]
            == b"invalid"
        )
    finally:
        store.close()


def test_stored_template_digest_is_checked():
    identity = create_identity()
    assert restore_identity(identity.record()) == identity
    record = identity.record() | {"sha256": "0" * 64}
    with pytest.raises(FingerprintFailure) as caught:
        restore_identity(record)
    assert caught.value.code == "fingerprint_restore_failed"


@pytest.mark.parametrize(
    "override",
    [
        {"headers": {"User-Agent": "different"}},
        {"headers": {"Sec-CH-UA-Platform": '"Linux"'}},
        {"headers": {"accept-language": "en-US"}},
        {"impersonate": "chrome142"},
        {"default_headers": True},
        {"ja3": "custom"},
    ],
)
def test_request_override_is_blocked_before_transmission(override):
    fixture = FixtureSession()
    observed = ObservedSession(fixture, lambda *args: None, create_identity())
    with pytest.raises(ProtocolFailure) as caught:
        observed.post("https://muse.ai/", **override)
    assert caught.value.code == "fingerprint_mismatch" and fixture.calls == []


def test_account_retries_and_service_restart_reuse_identity(tmp_path):
    first_id = None
    for _ in range(2):
        store = Storage(tmp_path)
        clients = []
        manager = TaskManager(
            store,
            client_factory=client_factory("blocked", clients),
            otp_session_factory=OtpSession,
        )
        try:
            created = manager.create([account()])[0]
            result = wait_for(manager, created["id"])
            assert result["error_code"] == "registrationBlocked"
            identity = clients[0].identity
            if first_id is None:
                first_id = identity.profile_id
            assert identity.profile_id == first_id == created["fingerprint"]["id"]
            calls = clients[0].s.inner.calls
            assert sum("register-completion" in url for _, url, _ in calls) == 1
            for _, _, kwargs in calls:
                assert kwargs["impersonate"] == identity.tls_profile
                assert kwargs["default_headers"] is False
                assert all(
                    kwargs["headers"][key] == value
                    for key, value in identity.headers().items()
                )
            assert identity.seed not in json.dumps(result)
        finally:
            manager.close()
            store.close()


def test_profile_summary_survives_history_and_export(tmp_path):
    store, clients = Storage(tmp_path), []
    manager = TaskManager(
        store,
        client_factory=client_factory(clients=clients),
        otp_session_factory=OtpSession,
    )
    try:
        task_id = manager.create([account()])[0]["id"]
        result = wait_for(manager, task_id)
        assert (
            result["verified"] and result["fingerprint"] == clients[0].identity.public()
        )
        exported = store.credentials(task_id)["protocol_fingerprint"]
        assert exported["id"] == result["fingerprint"]["id"]
        assert exported["headers"] == clients[0].identity.headers()
    finally:
        manager.close()
        store.close()


@pytest.fixture
def capture_server(tmp_path):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def request_body(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            captured.append(
                (self.path, {key.lower(): value for key, value in self.headers.items()})
            )
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = request_body
        do_POST = request_body

        def log_message(self, *args):
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.pem", tmp_path / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    context.set_alpn_protocols(["http/1.1"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", str(cert_path), captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("profile", PROFILES)
def test_real_tls_and_wire_headers_separate_api_from_navigation(
    capture_server, profile
):
    base, certificate, captured = capture_server
    client = ProtocolClient(account(browser_profile=profile), lambda *args: None)
    try:
        for path, headers in [
            ("/home", client._h(HEADERS_HTML, {"sec-fetch-site": "none"})),
            ("/json", client._h(HEADERS_JSON)),
        ]:
            client.s.post(base + path, headers=headers, data="{}", verify=certificate)
        assert len(captured) == 2
        home, api = [headers for _, headers in captured]
        for headers in (home, api):
            assert all(
                headers[key] == value
                for key, value in client.identity.headers().items()
            )
            assert "152" in headers["user-agent"] and "142" not in headers["user-agent"]
        assert (
            home["sec-fetch-user"] == "?1" and home["upgrade-insecure-requests"] == "1"
        )
        assert home["sec-fetch-site"] == "none" and home["sec-fetch-mode"] == "navigate"
        assert "sec-fetch-user" not in api and "upgrade-insecure-requests" not in api
        assert api["sec-fetch-mode"] == "cors" and api["sec-fetch-dest"] == "empty"
    finally:
        client.close()
