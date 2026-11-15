import hashlib
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

SESSION_MARKER = re.compile(
    r"(?P<prefix>(?:^|[-_])(?:lsid|sessid|session|sid)[-_=])(?P<value>[A-Za-z0-9]+)",
    re.I,
)


@dataclass(frozen=True)
class ProxyEndpoint:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    def url(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        auth = (
            f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
            if self.username or self.password
            else ""
        )
        return urlunsplit((self.scheme, f"{auth}{host}:{self.port}", "", "", ""))


def parse_proxy(value):
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        parts = value.split(":", 3)
        if len(parts) not in (2, 4):
            raise ValueError(
                "代理格式应为完整 URL、host:port 或 host:port:username:password"
            )
        host, port = parts[:2]
        auth = (
            f"{quote(parts[2], safe='')}:{quote(parts[3], safe='')}@"
            if len(parts) == 4
            else ""
        )
        value = f"http://{auth}{host}:{port}"
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https", "socks5", "socks5h"}
            or not parsed.hostname
            or not parsed.port
        ):
            raise ValueError
        if (
            parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or any(c.isspace() for c in value)
        ):
            raise ValueError
        username, password = (
            unquote(parsed.username or ""),
            unquote(parsed.password or ""),
        )
        if any(ord(c) < 32 or ord(c) == 127 for c in username + password):
            raise ValueError
        scheme = parsed.scheme
        endpoint = (parsed.hostname.lower(), parsed.port)
        if endpoint in {
            ("gate.kookeey.info", 1000),
            ("geo.iprocket.io", 59999),
            ("prem.country.iprocket.io", 9595),
        }:
            scheme = "socks5h"
        return ProxyEndpoint(scheme, parsed.hostname, parsed.port, username, password)
    except ValueError:
        raise ValueError(
            "代理 URL 必须包含有效主机与端口，且不能含路径、查询或控制字符"
        ) from None


def resolve_proxy(value, mode, session_key):
    endpoint = parse_proxy(value)
    if not endpoint or mode != "dynamic":
        return endpoint, False

    def replace(component):
        if "{session}" in component:
            return component.replace(
                "{session}", hashlib.sha256(session_key.encode()).hexdigest()[:16]
            )

        def substitute(match):
            previous = match.group("value")
            digest = hashlib.sha256(
                (session_key + "\0" + component).encode()
            ).hexdigest()
            token = (
                str(int(digest, 16) % (10 ** len(previous))).zfill(len(previous))
                if previous.isdigit()
                else (digest * (len(previous) // 64 + 1))[: len(previous)]
            )
            return match.group("prefix") + token

        return SESSION_MARKER.sub(substitute, component)

    username, password = replace(endpoint.username), replace(endpoint.password)
    isolated = (username, password) != (endpoint.username, endpoint.password)
    return ProxyEndpoint(
        endpoint.scheme, endpoint.host, endpoint.port, username, password
    ), isolated
