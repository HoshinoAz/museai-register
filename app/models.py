import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .proxies import parse_proxy


class ProxyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proxy: str = Field(default="", max_length=2048)
    proxy_mode: Literal["fixed", "dynamic"] = "fixed"
    front_proxy_enabled: bool = False
    front_proxy: str = Field(default="", max_length=2048)
    proxy_check: bool = True

    @field_validator("proxy", "front_proxy")
    @classmethod
    def proxy_shape(cls, value):
        endpoint = parse_proxy(value)
        return endpoint.url() if endpoint else ""

    @model_validator(mode="after")
    def front_required(self):
        if self.front_proxy_enabled and not self.front_proxy:
            raise ValueError("启用前置代理时必须填写前置代理地址")
        return self


class AccountInput(ProxyInput):
    email: str = Field(min_length=3, max_length=254)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    date_of_birth: date
    otp_url: str = Field(default="", max_length=2048)
    invite_code: str = Field(default="", max_length=100)
    language: str = Field(default="ja", pattern=r"^(ja|en-US|zh-CN)$")
    otp_timeout: int = Field(default=180, ge=30, le=600)
    request_timeout: int = Field(default=30, ge=5, le=60)
    consent: bool

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("邮箱格式无效")
        local, domain = value.rsplit("@", 1)
        return local + "@" + domain.lower()

    @field_validator("first_name", "last_name", "invite_code")
    @classmethod
    def printable(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("字段不能包含控制字符")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def adult_date(cls, value):
        today = date.today()
        age = (
            today.year
            - value.year
            - ((today.month, today.day) < (value.month, value.day))
        )
        if value.year < 1900 or value > today or age < 18:
            raise ValueError("请填写年满 18 周岁的有效生日")
        return value

    @field_validator("otp_url")
    @classmethod
    def url_shape(cls, value):
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.fragment
            ):
                raise ValueError
            if any(char.isspace() for char in value) or "\\" in value:
                raise ValueError
            if parsed.username or parsed.password:
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("请填写有效的完整 URL") from None
        return value

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value):
        if value is not True:
            raise ValueError("请确认注册及动态条款提交")
        return value


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accounts: list[AccountInput] = Field(min_length=1, max_length=50)


class OtpInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(pattern=r"^[0-9]{6}$")
