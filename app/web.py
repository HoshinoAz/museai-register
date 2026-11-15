import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import BatchInput, OtpInput, ProxyInput
from .proxy_engine import (
    VERSION,
    ProxyFailure,
    engine_path,
    install_engine,
    probe_proxy,
)
from .service import TaskManager
from .storage import Storage

ROOT = Path(__file__).resolve().parents[1]


def create_app(data_dir=None, manager_factory=None):
    @asynccontextmanager
    async def lifespan(application):
        storage = Storage(
            Path(data_dir or os.environ.get("MUSE_DATA_DIR", ROOT / "data"))
        )
        workers = max(1, min(5, int(os.environ.get("MUSE_WORKERS", "1"))))
        manager = (
            manager_factory(storage)
            if manager_factory
            else TaskManager(storage, workers=workers)
        )
        application.state.manager = manager
        application.state.storage = storage
        try:
            yield
        finally:
            manager.close()
            storage.close()

    application = FastAPI(
        title="Muse Register",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    @application.middleware("http")
    async def local_web_guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if request.headers.get("x-muse-client") != "1" or (
                origin
                and origin != f"{request.url.scheme}://{request.headers.get('host')}"
            ):
                return JSONResponse(
                    {"detail": "请通过本机 Muse 网页访问"}, status_code=403
                )
            if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                return JSONResponse({"detail": "请求来源无效"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default payload echoes input, including proxy passwords.
        errors = [
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in error.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    def manager(request):
        return request.app.state.manager

    @application.get("/api/health")
    def health(request: Request):
        return {
            "status": "ok",
            "workers": manager(request).workers,
            "mode": "pure_http",
            "upstream_commit": "be34c5e4893e5d109a15ffe46b14b416f60e0abf",
            "proxy_support": "dynamic_and_front",
            "proxy_engine": {"installed": engine_path().is_file(), "version": VERSION},
        }

    @application.post("/api/proxy/test")
    def test_proxy(payload: ProxyInput):
        try:
            return probe_proxy(payload)
        except ProxyFailure as error:
            return JSONResponse(
                {"detail": str(error), "code": error.code}, status_code=502
            )

    @application.post("/api/proxy/install")
    def install_proxy(payload: ProxyInput):
        try:
            install_engine(payload.front_proxy if payload.front_proxy_enabled else "")
            return {"installed": True, "version": VERSION}
        except ProxyFailure as error:
            return JSONResponse(
                {"detail": str(error), "code": error.code}, status_code=502
            )

    @application.get("/api/tasks")
    def list_tasks(request: Request):
        return {"tasks": manager(request).list()}

    @application.post("/api/tasks", status_code=201)
    def create_tasks(payload: BatchInput, request: Request):
        try:
            return {"tasks": manager(request).create(payload.accounts)}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @application.get("/api/tasks/{task_id}")
    def get_task(task_id: str, request: Request):
        try:
            return manager(request).get(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @application.post("/api/tasks/{task_id}/otp")
    def submit_otp(task_id: str, payload: OtpInput, request: Request):
        try:
            manager(request).submit_otp(task_id, payload.code)
            return {"accepted": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @application.post("/api/tasks/{task_id}/resend")
    def resend(task_id: str, request: Request):
        try:
            manager(request).resend_otp(task_id)
            return {"accepted": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @application.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str, request: Request):
        try:
            manager(request).cancel(task_id)
            return {"accepted": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @application.get("/api/tasks/{task_id}/credentials")
    def credentials(task_id: str, request: Request):
        try:
            public = manager(request).get(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None
        try:
            data = request.app.state.storage.credentials(task_id)
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None
        if not data:
            raise HTTPException(404, "该任务尚无可导出的凭据")
        return {
            **data,
            "registered": public["registered"],
            "hatch_access_verified": public["verified"],
            "status": public["status"],
            "invite_status": public["invite_status"],
            "own_invite": public["own_invite"],
        }

    @application.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return application
