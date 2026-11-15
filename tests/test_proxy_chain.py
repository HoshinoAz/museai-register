"""Real engine + loopback-only authenticated HTTP / SOCKS servers."""

import base64
import select
import socket
import socketserver
import struct
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from curl_cffi import requests

from app import proxy_engine
from app.proxies import parse_proxy
from app.proxy_engine import ProxyChain, engine_path


def receive(sock, count):
    result = b""
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise ConnectionError
        result += chunk
    return result


@contextmanager
def running_server(server):
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def proxy_handler(kind, label, calls, reject=False):
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            remote = None
            try:
                self.request.settimeout(5)
                if kind == "http":
                    header = b""
                    while not header.endswith(b"\r\n\r\n") and len(header) < 16384:
                        header += receive(self.request, 1)
                    lines = header.decode().split("\r\n")
                    method, address, _ = lines[0].split()
                    assert method == "CONNECT"
                    headers = {
                        k.lower(): v.strip()
                        for k, v in (
                            line.split(":", 1) for line in lines[1:] if ":" in line
                        )
                    }
                    auth = base64.b64encode(b"fixture:password").decode()
                    if reject or headers.get("proxy-authorization") != "Basic " + auth:
                        self.request.sendall(
                            b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n"
                        )
                        return
                    host, port = address.rsplit(":", 1)
                    port = int(port)
                else:
                    version, count = receive(self.request, 2)
                    assert version == 5 and 2 in receive(self.request, count)
                    self.request.sendall(b"\x05\x02")
                    version, length = receive(self.request, 2)
                    username = receive(self.request, length)
                    password = receive(self.request, receive(self.request, 1)[0])
                    if (
                        reject
                        or version != 1
                        or username != b"fixture"
                        or password != b"password"
                    ):
                        self.request.sendall(b"\x01\x01")
                        return
                    self.request.sendall(b"\x01\x00")
                    version, command, _, address_type = receive(self.request, 4)
                    assert version == 5 and command == 1
                    if address_type == 1:
                        host = socket.inet_ntoa(receive(self.request, 4))
                    elif address_type == 3:
                        host = receive(
                            self.request, receive(self.request, 1)[0]
                        ).decode()
                    else:
                        raise ValueError("unexpected address type")
                    port = struct.unpack("!H", receive(self.request, 2))[0]
                calls.append((label, host, port))
                remote = socket.create_connection((host, port), timeout=3)
                self.request.sendall(
                    b"HTTP/1.1 200 Connection established\r\n\r\n"
                    if kind == "http"
                    else b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00"
                )
                sockets = [self.request, remote]
                while True:
                    readable, _, _ = select.select(sockets, [], [], 5)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (remote if source is self.request else self.request).sendall(
                            data
                        )
            except (ConnectionError, OSError):
                return
            finally:
                if remote:
                    remote.close()

    return Proxy


@pytest.mark.skipif(
    not engine_path().is_file(),
    reason="Optional real Mihomo integration: install engine first",
)
@pytest.mark.parametrize(
    "front_kind,task_kind,reject",
    [("http", "socks5", False), ("socks5", "http", False), ("http", "socks5", True)],
)
def test_real_two_hop_authentication_and_cleanup(
    tmp_path, monkeypatch, front_kind, task_kind, reject
):
    binary = engine_path()
    monkeypatch.setenv("MUSE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(proxy_engine, "engine_path", lambda: binary)
    calls, hits = [], []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "13")
            self.end_headers()
            self.wfile.write(b"fixture-chain")

        def log_message(self, *args):
            pass

    with (
        running_server(ThreadingHTTPServer(("127.0.0.1", 0), Target)) as target_port,
        running_server(
            socketserver.ThreadingTCPServer(
                ("127.0.0.1", 0), proxy_handler(task_kind, "TASK", calls)
            )
        ) as task_port,
        running_server(
            socketserver.ThreadingTCPServer(
                ("127.0.0.1", 0), proxy_handler(front_kind, "FRONT", calls, reject)
            )
        ) as front_port,
    ):
        selected = parse_proxy(f"{task_kind}://fixture:password@127.0.0.1:{task_port}")
        front = parse_proxy(f"{front_kind}://fixture:password@127.0.0.1:{front_port}")
        chain = ProxyChain(selected, front)
        process, directory = chain.process, chain.directory
        try:
            with requests.Session(
                proxy=chain.url, trust_env=False, timeout=4
            ) as session:
                if reject:
                    try:
                        failed = session.get(f"http://127.0.0.1:{target_port}/proof")
                        assert failed.status_code >= 400
                    except requests.exceptions.RequestException:
                        pass
                    assert not hits and not calls
                else:
                    response = session.get(f"http://127.0.0.1:{target_port}/proof")
                    assert (
                        response.status_code == 200 and response.text == "fixture-chain"
                    )
                    assert calls == [
                        ("FRONT", "127.0.0.1", task_port),
                        ("TASK", "127.0.0.1", target_port),
                    ]
                    assert hits == ["/proof"]
        finally:
            chain.close()
            chain.close()
        assert process.poll() is not None and not directory.exists()
