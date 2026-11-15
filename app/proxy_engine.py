import gzip
import hashlib
import io
import ipaddress
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from curl_cffi import requests

from .proxies import parse_proxy, resolve_proxy

VERSION = "1.19.29"
ROOT = Path(__file__).resolve().parents[1]
START_LOCK = threading.Lock()
INSTALL_LOCK = threading.Lock()
RELEASES = {
    ("windows", "amd64"): (
        "zip",
        "1a8520cfe425441eba3eba8623b27b985020031243fe1ecaa1af2b92358a03f9",
    ),
    ("windows", "arm64"): (
        "zip",
        "f71736f9c2a17abb8909a726c69ac55279d0cb43d1d9f2c85afdbb70a0f326a3",
    ),
    ("linux", "amd64"): (
        "gz",
        "60de76a35a6cbf7b4fa4a20f5c257c24345d1d635ab1aa3877022a1997ef413c",
    ),
    ("linux", "arm64"): (
        "gz",
        "9a868b5e4e0ad91d9d71e1b41b0cfce78aaba44360c30df74a723f8e3926a86c",
    ),
    ("darwin", "amd64"): (
        "gz",
        "b57fec2e38462532fe75252792b355b99db16b0b8ea2d6bdf0cd8bc7ddacb9d2",
    ),
    ("darwin", "arm64"): (
        "gz",
        "4dc25df9e899f14161911302a8ee5fc9e202ed9c976fc405bf82c50ff27466ca",
    ),
}


class ProxyFailure(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def data_root():
    return Path(os.environ.get("MUSE_DATA_DIR", ROOT / "data"))


def engine_path():
    return data_root() / "bin" / ("mihomo.exe" if os.name == "nt" else "mihomo")


def install_engine(download_proxy=""):
    with INSTALL_LOCK:
        destination = engine_path()
        if destination.is_file():
            return
        system = platform.system().lower()
        machine = {"x86_64": "amd64", "x64": "amd64", "aarch64": "arm64"}.get(
            platform.machine().lower(), platform.machine().lower()
        )
        release = RELEASES.get((system, machine))
        if not release:
            raise ProxyFailure(
                "engine_platform_unsupported", "当前平台没有预置代理内核"
            )
        extension, expected = release
        asset = f"mihomo-{system}-{machine}-v{VERSION}.{extension}"
        url = (
            f"https://github.com/MetaCubeX/mihomo/releases/download/v{VERSION}/{asset}"
        )
        try:
            with requests.Session(
                proxy=download_proxy or None, trust_env=False
            ) as session:
                response = session.get(url, timeout=100)
                if response.status_code != 200:
                    raise ValueError
                archive = response.content
            if hashlib.sha256(archive).hexdigest() != expected:
                raise ProxyFailure(
                    "engine_checksum_failed", "代理内核下载校验失败，未安装"
                )
            if extension == "gz":
                executable = gzip.decompress(archive)
            else:
                with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                    executable = bundle.read(
                        next(
                            name
                            for name in bundle.namelist()
                            if name.lower().endswith(".exe")
                        )
                    )
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = destination.with_suffix(".part")
            staging.write_bytes(executable)
            staging.chmod(0o700)
            staging.replace(destination)
        except ProxyFailure:
            raise
        except Exception:
            raise ProxyFailure(
                "engine_download_failed", "代理内核下载失败，请检查前置代理或网络"
            ) from None


def proxy_node(endpoint, name):
    node = {
        "name": name,
        "type": "socks5" if endpoint.scheme.startswith("socks5") else "http",
        "server": endpoint.host,
        "port": endpoint.port,
    }
    if endpoint.username or endpoint.password:
        node.update(username=endpoint.username, password=endpoint.password)
    if endpoint.scheme == "https":
        node.update(tls=True, sni=endpoint.host, **{"skip-cert-verify": False})
    return node


def build_config(selected, front, port, controller, password, secret):
    nodes = [proxy_node(front, "FRONT")]
    route = "FRONT"
    if selected:
        task = proxy_node(selected, "TASK")
        task["dialer-proxy"] = "FRONT"
        nodes.append(task)
        route = "TASK"
    return {
        "mixed-port": port,
        "bind-address": "127.0.0.1",
        "allow-lan": False,
        "authentication": [f"muse:{password}"],
        "external-controller": f"127.0.0.1:{controller}",
        "secret": secret,
        "mode": "rule",
        "log-level": "silent",
        "ipv6": False,
        "proxies": nodes,
        "rules": [f"MATCH,{route}"],
    }


def free_port():
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class ProxyChain:
    def __init__(self, selected, front):
        self.process = None
        self.directory = None
        self.url = ""
        binary = engine_path()
        if not binary.is_file():
            raise ProxyFailure(
                "proxy_engine_missing",
                "前置代理需要代理链内核，请先点击“安装代理链内核”",
            )
        try:
            with START_LOCK:
                session_root = data_root() / "proxy-sessions"
                session_root.mkdir(parents=True, exist_ok=True)
                self.directory = Path(
                    tempfile.mkdtemp(prefix="muse-", dir=session_root)
                )
                self.directory.chmod(0o700)
                port, controller = free_port(), free_port()
                while port == controller:
                    controller = free_port()
                password, secret = secrets.token_hex(24), secrets.token_hex(24)
                config = self.directory / "config.json"
                config.write_text(
                    json.dumps(
                        build_config(
                            selected, front, port, controller, password, secret
                        )
                    ),
                    encoding="utf-8",
                )
                config.chmod(0o600)
                self.process = subprocess.Popen(
                    [str(binary), "-d", str(self.directory), "-f", str(config)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                deadline = time.monotonic() + 10
                with requests.Session(trust_env=False) as session:
                    while self.process.poll() is None and time.monotonic() < deadline:
                        try:
                            response = session.get(
                                f"http://127.0.0.1:{controller}/version",
                                headers={"Authorization": f"Bearer {secret}"},
                                timeout=0.3,
                            )
                            if response.status_code == 200 and isinstance(
                                response.json().get("version"), str
                            ):
                                self.url = f"http://muse:{password}@127.0.0.1:{port}"
                                return
                        except (requests.exceptions.RequestException, ValueError):
                            pass
                        time.sleep(0.05)
            raise ProxyFailure(
                "proxy_engine_start_failed", "代理链内核未就绪；未回退直连"
            )
        except Exception:
            self.close()
            raise ProxyFailure(
                "proxy_engine_start_failed", "代理链内核启动失败；未回退直连"
            ) from None

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.directory:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None


class ProxyRoute:
    def __init__(self, config, session_key, emit):
        self.chain = None
        selected, self.isolated = resolve_proxy(
            config.proxy, config.proxy_mode, session_key
        )
        self.enabled = bool(selected or config.front_proxy_enabled)
        self.mode = config.proxy_mode
        self.label = (
            "前置 → 注册代理"
            if selected and config.front_proxy_enabled
            else "前置代理"
            if config.front_proxy_enabled
            else "注册代理"
            if selected
            else "直连"
        )
        self.url = selected.url() if selected else ""
        if config.proxy_mode == "dynamic" and selected:
            emit(
                "动态代理已按任务隔离 session"
                if self.isolated
                else "未识别 sticky session 参数；供应商可能逐请求换 IP，建议在凭据中使用 {session}",
                "info" if self.isolated else "warning",
            )
        if config.front_proxy_enabled:
            self.chain = ProxyChain(selected, parse_proxy(config.front_proxy))
            self.url = self.chain.url
        emit(f"连接路由：{self.label}；全任务复用同一代理配置", "info")

    def close(self):
        if self.chain:
            self.chain.close()
            self.chain = None


def exit_ip(session, timeout=20):
    try:
        response = session.get("https://api.ipify.org?format=json", timeout=timeout)
        if response.status_code != 200:
            raise ValueError
        address = response.json()["ip"]
        if not isinstance(address, str):
            raise ValueError
        return str(ipaddress.ip_address(address))
    except Exception:
        raise ProxyFailure(
            "proxy_check_failed",
            "出口 IP 查询失败；请检查代理协议、认证、地区与前置链路，未提交账号创建",
        ) from None


def probe_proxy(config):
    route = ProxyRoute(config, secrets.token_hex(16), lambda *args: None)
    start = time.monotonic()
    try:
        with requests.Session(
            proxy=route.url or None,
            trust_env=False,
            impersonate="chrome142",
            timeout=20,
        ) as session:
            first = exit_ip(session)
            try:
                response = session.get("https://muse.ai/", timeout=20)
                status = response.status_code
            except requests.exceptions.RequestException:
                raise ProxyFailure(
                    "proxy_target_unreachable",
                    "出口可查询，但 Muse 首页连接失败；请检查注册代理与前置链路",
                ) from None
            last = exit_ip(session)
        if first != last:
            raise ProxyFailure(
                "proxy_ip_changed",
                "测试期间出口 IP 发生变化，请配置 sticky session 后重试",
            )
        return {
            "connected": status == 200,
            "exit_ip": first,
            "homepage_status": status,
            "elapsed_ms": round((time.monotonic() - start) * 1000),
            "route": route.label,
            "session_isolated": route.isolated,
            "message": "两次出口采样一致，Muse 首页 HTTP 200"
            if status == 200
            else f"代理可连接，Muse 首页返回 HTTP {status}",
            "warning": "未识别 sticky session 参数，无法固定供应商出口"
            if config.proxy_mode == "dynamic" and config.proxy and not route.isolated
            else "",
        }
    finally:
        route.close()
