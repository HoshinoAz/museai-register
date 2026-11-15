import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Standalone Muse HTTP console")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, 6),
        default=1,
        help="Concurrent registration tasks, not web server processes",
    )
    args = parser.parse_args()
    os.environ["MUSE_WORKERS"] = str(args.workers)
    uvicorn.run(
        "app.web:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
