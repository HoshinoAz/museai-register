import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from curl_cffi import requests

from .protocol import MuseError, ProtocolClient, ProtocolFailure, select_otp

TERMINAL = {"succeeded", "partial", "failed", "cancelled", "interrupted"}


def now():
    return datetime.now(timezone.utc).isoformat()


class Cancelled(Exception):
    pass


@dataclass
class Runtime:
    config: object
    public: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    code: str | None = None
    resend: bool = False
    resend_count: int = 0
    last_send: float = 0
    otp_after: datetime | None = None


class TaskManager:
    def __init__(
        self,
        storage,
        workers=1,
        client_factory=ProtocolClient,
        otp_session_factory=requests.Session,
    ):
        self.storage = storage
        self.workers = workers
        self.client_factory = client_factory
        self.otp_session_factory = otp_session_factory
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="muse-http"
        )
        self.tasks = {}
        self.records = {item["id"]: item for item in storage.history()}
        for item in self.records.values():
            if item["status"] not in TERMINAL:
                if item.get("stage") == "completion" and not item["registered"]:
                    item["error_code"] = "creation_uncertain"
                item.update(
                    status="interrupted",
                    message="服务已重启；原会话已中断，不会自动重提注册",
                    updated_at=now(),
                )
                storage.save(item)

    def create(self, configs):
        with self.lock:
            emails = [item.email.casefold() for item in configs]
            if len(set(emails)) != len(emails):
                raise ValueError("本批次存在重复邮箱")
            occupied = {
                item["email"].casefold()
                for item in self.records.values()
                if item["status"] not in TERMINAL
                or item["registered"]
                or item.get("error_code") == "creation_uncertain"
            }
            if any(email in occupied for email in emails):
                raise ValueError("邮箱已有进行中的任务或已创建的账号，请先查看历史结果")
            created = []
            batch_id = uuid.uuid4().hex
            for config in configs:
                task_id = uuid.uuid4().hex
                public = {
                    "id": task_id,
                    "email": config.email,
                    "status": "queued",
                    "stage": "queued",
                    "message": "等待执行",
                    "registered": False,
                    "verified": False,
                    "has_credentials": False,
                    "otp_mode": "auto" if config.otp_url else "manual",
                    "invite_status": "not_requested",
                    "own_invite": None,
                    "error_code": None,
                    "created_at": now(),
                    "updated_at": now(),
                    "events": [],
                }
                public["batch_id"] = batch_id
                runtime = Runtime(config, public)
                self.tasks[task_id] = runtime
                self.records[task_id] = public
                self.storage.save(public)
                created.append(copy.deepcopy(public))
            for item in created:
                self.pool.submit(self._run, self.tasks[item["id"]])
            return created

    def list(self):
        with self.lock:
            return copy.deepcopy(
                sorted(
                    self.records.values(),
                    key=lambda item: item["created_at"],
                    reverse=True,
                )
            )

    def get(self, task_id):
        with self.lock:
            return copy.deepcopy(self.records[task_id])

    def _update(self, task, **values):
        with self.lock:
            task.public.update(values, updated_at=now())
            self.storage.save(task.public)

    def _emit(self, task, message, kind="info"):
        with self.lock:
            task.public["events"].append(
                {
                    "at": now(),
                    "stage": task.public["stage"],
                    "kind": kind,
                    "message": message,
                }
            )
            task.public["events"] = task.public["events"][-160:]
            task.public["updated_at"] = now()
            self.storage.save(task.public)

    def _stage(self, task, stage, message):
        self._guard(task)
        self._update(task, status="running", stage=stage, message=message)
        self._emit(task, message)

    @staticmethod
    def _guard(task):
        if task.cancel.is_set():
            raise Cancelled

    def submit_otp(self, task_id, code):
        with self.lock:
            task = self.tasks.get(task_id)
            if (
                not task
                or task.public["status"] != "waiting_otp"
                or task.cancel.is_set()
            ):
                raise ValueError("任务当前不接受验证码")
            with task.condition:
                if task.code:
                    raise ValueError("验证码正在处理，请稍候")
                task.code = code
                task.condition.notify_all()

    def resend_otp(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if (
                not task
                or task.public["status"] != "waiting_otp"
                or task.cancel.is_set()
            ):
                raise ValueError("任务当前不能重发验证码")
            with task.condition:
                if task.resend or task.code or task.resend_count >= 2:
                    raise ValueError("重发请求正在处理或已达两次上限")
                remaining = 45 - (time.monotonic() - task.last_send)
                if remaining > 0:
                    raise ValueError(f"请在 {int(remaining) + 1} 秒后重发")
                task.resend = True
                task.condition.notify_all()

    def cancel(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.public["status"] in TERMINAL:
                raise ValueError("任务已经结束")
            task.cancel.set()
            with task.condition:
                task.condition.notify_all()
            self._update(task, message="正在停止；已发出的 HTTP 请求需等待返回")

    def _save_credentials(self, task, client, meta):
        credentials = client.credentials(task.config.email, meta)
        credentials["exported_at"] = now()
        credentials["task_id"] = task.public["id"]
        with self.lock:
            task.public["has_credentials"] = True
            self.storage.save(task.public, credentials)

    def _stop_batch(self, failed_task):
        with self.lock:
            for task in self.tasks.values():
                if (
                    task is failed_task
                    or task.public["batch_id"] != failed_task.public["batch_id"]
                    or task.public["status"] in TERMINAL
                ):
                    continue
                task.cancel.set()
                with task.condition:
                    task.condition.notify_all()
                self._emit(
                    task,
                    "同批任务未完成，已停止后续任务；已发出的请求等待返回",
                    "warning",
                )

    def _wait_code(self, task, client, deadline, seen, otp_session):
        self._update(
            task,
            status="waiting_otp",
            stage="otp",
            message="等待邮箱验证码，可手动输入"
            if task.config.otp_url
            else "请输入邮箱收到的 6 位验证码",
        )
        next_poll = 0
        source_warned = False
        while time.monotonic() < deadline:
            self._guard(task)
            with task.condition:
                code, task.code = task.code, None
                resend, task.resend = task.resend, False
            if code:
                if code not in seen:
                    return code
                self._emit(task, "该验证码已提交过，请使用新验证码", "warning")
            if resend:
                task.resend_count += 1
                task.otp_after = datetime.now(timezone.utc)
                task.last_send = time.monotonic()
                client.resend_otp()
                self._emit(task, "服务端已确认重发验证码")
            if otp_session and time.monotonic() >= next_poll:
                try:
                    remaining = max(0.1, deadline - time.monotonic())
                    response = otp_session.get(
                        task.config.otp_url,
                        timeout=min(10, remaining),
                        headers={"accept": "application/json"},
                    )
                    records = response.json() if response.status_code == 200 else None
                    code = select_otp(records, task.otp_after)
                    if code and code not in seen:
                        self._guard(task)
                        return code
                    if not isinstance(records, list) and not source_warned:
                        self._emit(
                            task,
                            "接码源未返回有效 JSON 列表；可手动输入验证码",
                            "warning",
                        )
                        source_warned = True
                except Exception:
                    if not source_warned:
                        self._emit(
                            task, "接码源暂时不可用；继续等待，也可手动输入", "warning"
                        )
                        source_warned = True
                next_poll = time.monotonic() + 2
            with task.condition:
                if not task.code and not task.resend and not task.cancel.is_set():
                    task.condition.wait(timeout=0.4)
        raise ProtocolFailure("otp_timeout", "等待验证码超时；未提交账号创建")

    def _run(self, task):
        client = otp_session = None
        meta = None
        try:
            self._stage(task, "bootstrap", "初始化独立 HTTP 会话")
            client = self.client_factory(
                task.config,
                lambda message, kind="info": self._emit(task, message, kind),
            )
            response = client.open_home()
            if response.status_code != 200:
                raise ProtocolFailure(
                    "homepage_unavailable", "首页未返回 HTTP 200，未发送验证码"
                )
            self._guard(task)
            client.restart(resume=False)
            self._stage(task, "send_otp", "发送邮箱验证码")
            task.otp_after = datetime.now(timezone.utc)
            task.last_send = time.monotonic()
            data = client.send_otp(task.config.email)
            if (
                data.get("flow") != "register"
                or data.get("has_muse_account") is not False
                or data.get("registration_state") != "otp_sent"
            ):
                raise ProtocolFailure(
                    "not_registration_flow", "服务端未确认新账号注册流程，已停止"
                )
            if task.config.otp_url:
                otp_session = self.otp_session_factory()
            deadline = time.monotonic() + task.config.otp_timeout
            seen = set()
            for attempt in range(3):
                code = self._wait_code(task, client, deadline, seen, otp_session)
                seen.add(code)
                self._stage(task, "confirm_otp", "校验邮箱验证码")
                try:
                    data = client.confirm_otp(code)
                    break
                except MuseError:
                    if (
                        client.s.last_error
                        not in {
                            "invalidOtp",
                            "invalidOTP",
                            "incorrectOtp",
                            "otpInvalid",
                            "invalid_otp",
                            "otp_mismatch",
                        }
                        or attempt == 2
                    ):
                        raise
                    self._emit(task, "验证码被拒绝，请输入最新验证码", "warning")
            if data.get("registration_state") != "completion_required":
                raise ProtocolFailure(
                    "unexpected_otp_state", "验证码响应未进入资料完善阶段，已停止"
                )
            self._stage(task, "completion", "提交姓名、生日与动态条款版本（仅一次）")
            data = client.register_completion(
                task.config.date_of_birth.isoformat(),
                task.config.first_name,
                task.config.last_name,
            )
            self._update(task, registered=True)
            meta = data.get("auth_meta_session")
            meta = meta if isinstance(meta, dict) else {}
            self._save_credentials(task, client, meta)
            if not meta.get("frl_access_token") or not meta.get("frl_account_id"):
                raise ProtocolFailure(
                    "meta_session_missing", "创建请求已获确认，但缺少会话引导凭据"
                )
            self._stage(task, "activation", "引导 Meta → Muse 会话，解析动态披露")
            onboarding = client.activate_session(meta["frl_access_token"])
            self._save_credentials(task, client, meta)
            self._stage(task, "disclosure", "提交服务端返回的披露条款（仅一次）")
            response = client.accept_tos(onboarding)
            if response.json().get("success") is not True:
                raise ProtocolFailure(
                    "tos_acceptance_unconfirmed", "条款接口未明确返回 success=true"
                )
            self._stage(task, "validation", "验证 Hatch 接口访问与会话 Cookie")
            client.validate_access()
            self._update(task, verified=True)
            self._save_credentials(task, client, meta)
            if task.config.invite_code:
                self._stage(task, "invite", "唤醒 VM 并兑换邀请码")
                reason, own = client.redeem_checked(task.config.invite_code)
                self._update(task, invite_status=reason, own_invite=own)
                self._save_credentials(task, client, meta)
                if reason != "redeemed":
                    self._emit(task, f"账号会话已验证；邀请码返回 {reason}", "warning")
            self._update(
                task,
                status="succeeded",
                stage="done",
                message="账号已创建，Hatch 会话访问已验证",
            )
            self._emit(task, "完成；可导出加密保存的账号凭据", "success")
        except Cancelled:
            self._update(
                task,
                status="cancelled",
                message="任务已停止"
                + ("；已创建的账号与凭据已保留" if task.public["registered"] else ""),
            )
            self._emit(task, task.public["message"], "warning")
        except Exception as error:
            if isinstance(error, ProtocolFailure):
                code, message = error.code, str(error)
            elif isinstance(error, MuseError):
                code = getattr(getattr(client, "s", None), "last_error", None)
                if str(error) == "region_waitlist":
                    code, message = (
                        "region_waitlist",
                        "账号已创建，但服务端返回地区候补状态；未验证访问权限",
                    )
                else:
                    code = code or "protocol_unconfirmed"
                    message = f"服务端未确认当前步骤（{code}）；未自动重提"
            else:
                code = (
                    "transport_error"
                    if isinstance(error, requests.exceptions.RequestException)
                    else "response_unresolved"
                )
                message = (
                    "网络请求失败；服务端是否收到请求可能未知，未自动重提"
                    if code == "transport_error"
                    else "响应未能按预期解析，已保留现有结果并停止"
                )
            if (
                task.public["stage"] == "completion"
                and getattr(client, "completion_sent", False)
                and not task.public["registered"]
                and code in {"transport_error", "response_unresolved"}
            ):
                code, message = (
                    "creation_uncertain",
                    "账号创建请求已发出，但结果未知；请检查邮箱，勿重复注册",
                )
            self._update(
                task,
                status="partial" if task.public["registered"] else "failed",
                error_code=code,
                message=message,
            )
            self._emit(task, message, "error")
            self._stop_batch(task)
        finally:
            if client:
                if meta is not None:
                    try:
                        self._save_credentials(task, client, meta)
                    except Exception:
                        self._emit(task, "最终凭据保存失败，请检查本地磁盘", "error")
                client.close()
            if otp_session:
                otp_session.close()

    def close(self):
        with self.lock:
            active = [
                task.public["id"]
                for task in self.tasks.values()
                if task.public["status"] not in TERMINAL
            ]
        for task_id in active:
            try:
                self.cancel(task_id)
            except ValueError:
                pass
        self.pool.shutdown(wait=True)
