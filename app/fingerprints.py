import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from uuid import UUID

from curl_cffi import Curl

SCHEMA = 1
DEFAULT_BROWSER = "Chrome 152"
PROFILES = ("chrome152", "chrome152_macos")
LANGUAGES = {
    "ja": "ja-JP,ja;q=0.9",
    "en-US": "en-US,en;q=0.9",
    "zh-CN": "zh-CN,zh;q=0.9",
}


class FingerprintFailure(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BrowserIdentity:
    seed: str = field(repr=False)
    tls_profile: str
    language: str

    def __post_init__(self):
        if (
            len(self.seed) != 64
            or self.seed.lower() != self.seed
            or any(char not in "0123456789abcdef" for char in self.seed)
            or self.tls_profile not in PROFILES
            or self.language not in LANGUAGES
        ):
            raise FingerprintFailure("fingerprint_invalid", "账号协议指纹无效，已停止")

    @property
    def profile_id(self):
        digest = hmac.new(
            bytes.fromhex(self.seed), b"muse-http-identity-v1", hashlib.sha256
        ).digest()
        return str(UUID(bytes=digest[:16], version=4))

    @property
    def platform(self):
        return "macOS" if self.tls_profile.endswith("_macos") else "Windows"

    def headers(self):
        system = (
            "Macintosh; Intel Mac OS X 10_15_7"
            if self.platform == "macOS"
            else "Windows NT 10.0; Win64; x64"
        )
        return {
            "user-agent": f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{self.platform}"',
            "accept-language": LANGUAGES[self.language],
        }

    def public(self):
        details = {
            "schema": SCHEMA,
            "id": self.profile_id,
            "browser": DEFAULT_BROWSER,
            "tls_profile": self.tls_profile,
            "platform": self.platform,
            "language": self.language,
        }
        canonical = json.dumps(
            details | {"headers": self.headers()}, sort_keys=True, separators=(",", ":")
        )
        return details | {"sha256": hashlib.sha256(canonical.encode()).hexdigest()}

    def record(self):
        return {
            "schema": SCHEMA,
            "seed": self.seed,
            "tls_profile": self.tls_profile,
            "language": self.language,
            "sha256": self.public()["sha256"],
        }

    def check_runtime(self):
        curl = Curl()
        try:
            if curl.impersonate(self.tls_profile, default_headers=False) != 0:
                raise ValueError
        except Exception:
            raise FingerprintFailure(
                "fingerprint_unsupported",
                "当前 curl_cffi 不支持 Chrome 152，请重新运行 setup.bat 更新依赖；未降级旧版",
            ) from None
        finally:
            curl.close()


def create_identity(language="ja", preference="auto", seed=None):
    seed = seed if seed is not None else secrets.token_hex(32)
    if preference == "auto":
        preference = PROFILES[
            int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(PROFILES)
        ]
    return BrowserIdentity(seed, preference, language)


def restore_identity(record):
    try:
        if record["schema"] != SCHEMA:
            raise ValueError
        identity = BrowserIdentity(
            record["seed"], record["tls_profile"], record["language"]
        )
        if record["sha256"] != identity.public()["sha256"]:
            raise ValueError
        return identity
    except (KeyError, TypeError, ValueError, FingerprintFailure):
        raise FingerprintFailure(
            "fingerprint_restore_failed",
            "已保存的账号协议指纹无法读取；未重新生成或更换身份",
        ) from None
