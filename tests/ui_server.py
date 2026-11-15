"""Local-only UI test server. Protocol traffic is entirely scripted."""

import tempfile

import uvicorn

from app.service import TaskManager
from app.web import create_app
from tests.fixtures import OtpSession, client_factory


def factory(storage):
    return TaskManager(
        storage, client_factory=client_factory(), otp_session_factory=OtpSession
    )


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="muse-ui-fixture-") as directory:
        uvicorn.run(
            create_app(directory, factory),
            host="127.0.0.1",
            port=8788,
            access_log=False,
        )
