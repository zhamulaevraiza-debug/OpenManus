"""Shared fixtures for tool tests."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Tuple

import pytest

from app.context import RunContext, reset_run, set_run


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def run_ctx(workspace: Path) -> Iterator[RunContext]:
    """An active RunContext whose workspace is a fresh temporary directory."""
    ctx = RunContext(run_id="test-run", workspace=workspace)
    token = set_run(ctx)
    try:
        yield ctx
    finally:
        reset_run(token)


@pytest.fixture
def allow_private_network(monkeypatch):
    monkeypatch.setenv("OPENMANUS_ALLOW_PRIVATE_NETWORK", "true")


@pytest.fixture
def block_private_network(monkeypatch):
    monkeypatch.delenv("OPENMANUS_ALLOW_PRIVATE_NETWORK", raising=False)


Route = Tuple[int, Dict[str, str], bytes]


class LocalSite:
    """A tiny HTTP server on 127.0.0.1 with configurable routes."""

    def __init__(self) -> None:
        self.routes: Dict[str, Callable[[], Route]] = {}
        self.requests: List[Tuple[str, Dict[str, str]]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                site.requests.append((self.path, dict(self.headers)))
                factory = site.routes.get(self.path.split("?")[0])
                status, headers, body = (
                    factory() if factory else (404, {}, b"not found")
                )
                self.send_response(status)
                headers = {"Content-Type": "text/html; charset=utf-8", **headers}
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def html(self, path: str, body: str) -> None:
        self.routes[path] = lambda: (200, {}, body.encode())

    def redirect(self, path: str, location: str) -> None:
        self.routes[path] = lambda: (302, {"Location": location}, b"")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def local_site() -> Iterator[LocalSite]:
    site = LocalSite()
    try:
        yield site
    finally:
        site.close()
