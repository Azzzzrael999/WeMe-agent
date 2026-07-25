"""Day 1 skeleton for the external video provider boundary."""

from __future__ import annotations

import json
import os
import signal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from typing import Any


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "VideoProviderMock/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self._write_json(
                HTTPStatus.OK,
                {"status": "UP", "service": "video-provider-mock"},
            )
            return

        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"code": "NOT_FOUND", "message": "Route not found"},
        )

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the stdlib access log while avoiding request bodies or credentials.
        super().log_message(format, *args)

    def _write_json(self, status: HTTPStatus, payload: dict[str, str]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str = "0.0.0.0", port: int = 8010) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), HealthHandler)


def main() -> None:
    port = int(os.environ.get("PORT", "8010"))
    server = create_server(port=port)

    def stop_server(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
