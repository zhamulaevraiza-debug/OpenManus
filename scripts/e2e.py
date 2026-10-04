"""Run the end-to-end tests against the real product.

Starts the fake OpenAI-compatible model (``tests/fake_llm``) and the web server
(``web_main.py``, serving the built UI from ``web/dist``) with a fresh temporary data
and configuration directory, runs the Playwright suite in ``web/e2e`` (desktop and
mobile projects) and stops everything again.

    python scripts/e2e.py                  # build the UI if needed, run all e2e tests
    python scripts/e2e.py -- --project=mobile -g "files"   # arguments for Playwright
    python scripts/e2e.py --screenshots    # regenerate docs/screenshots/*.png
    python scripts/e2e.py --serve          # only start the stack (manual testing)

Requires Node.js (``npx``) on PATH and the web dependencies installed (``npm ci`` in
``web/``). Playwright browsers are taken from ``PLAYWRIGHT_BROWSERS_PATH`` when set.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from typing import Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DIST_DIR = WEB_DIR / "dist"
SCREENSHOTS_DIR = ROOT / "docs" / "screenshots"

ADMIN_USERNAME = "admin"
FAKE_API_KEY = "sk-e2e-fake-key-0123456789"
FAKE_MODEL = "fake-gpt"
# The fake model ignores the model name; the screenshots show a familiar one.
SCREENSHOT_MODEL = "gpt-4o-mini"
# Agent replies to "slow" requests are delayed so that tests can stop or reload a run.
SLOW_SECONDS = 3.0
STARTUP_TIMEOUT = 90.0
STOP_TIMEOUT = 15.0
LOG_TAIL_LINES = 60

CONFIG_TOML = """\
[llm]
model = "{model}"
base_url = "{base_url}"
api_key = "{api_key}"
max_tokens = 2048
temperature = 0.0

[browser]
headless = true

[runtime]
max_steps = 8

[team]
max_plan_steps = 4
"""


class Service:
    """A background process with its output captured in a log file."""

    def __init__(
        self, name: str, args: Sequence[str], env: Dict[str, str], log_path: Path
    ):
        self.name = name
        self.log_path = log_path
        self._log = log_path.open("wb")
        self.process = subprocess.Popen(
            list(args),
            cwd=ROOT,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def wait_healthy(self, url: str, timeout: float = STARTUP_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"{self.name} exited with code {self.process.returncode}"
                )
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.25)
        raise RuntimeError(f"{self.name} did not become healthy at {url}")

    def stop(self) -> None:
        """SIGTERM the process group, then SIGKILL it after a grace period."""
        if self.process.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                self.process.wait()
        self._log.close()

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass

    def tail(self, lines: int = LOG_TAIL_LINES) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(args: Sequence[str], cwd: Path, env: Optional[Dict[str, str]] = None) -> int:
    print(f"$ {' '.join(args)}", flush=True)
    return subprocess.call(list(args), cwd=cwd, env=env)


def _ensure_web_build(force: bool) -> None:
    if not force and (DIST_DIR / "index.html").exists():
        return
    if _run(["npm", "run", "build"], WEB_DIR) != 0:
        raise RuntimeError("building the web UI failed")


def _base_env() -> Dict[str, str]:
    """The current environment without OpenManus settings that could interfere."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OPENMANUS_") and not key.startswith("E2E_")
    }


def _write_config(config_dir: Path, base_url: str, model: str) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        CONFIG_TOML.format(model=model, base_url=base_url, api_key=FAKE_API_KEY),
        encoding="utf-8",
    )
    (config_dir / "mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")


def _server_env(
    work_dir: Path, port: int, llm_base_url: str, admin_password: str
) -> Dict[str, str]:
    env = _base_env()
    env.update(
        {
            "OPENMANUS_DATA_DIR": str(work_dir / "data"),
            "OPENMANUS_CONFIG_DIR": str(work_dir / "config"),
            "OPENMANUS_LOG_DIR": str(work_dir / "logs"),
            "OPENMANUS_WORKSPACE_ROOT": str(work_dir / "workspace"),
            "OPENMANUS_STATIC_DIR": str(DIST_DIR),
            "OPENMANUS_HOST": "127.0.0.1",
            "OPENMANUS_PORT": str(port),
            "OPENMANUS_SECRET_KEY": secrets.token_urlsafe(32),
            "OPENMANUS_ADMIN_USERNAME": ADMIN_USERNAME,
            "OPENMANUS_ADMIN_PASSWORD": admin_password,
            "OPENMANUS_LLM_BASE_URL": llm_base_url,
            "OPENMANUS_COOKIE_SECURE": "false",
            "OPENMANUS_BROWSER_HEADLESS": "true",
            # The browsing test opens the fake LLM's page on 127.0.0.1.
            "OPENMANUS_ALLOW_PRIVATE_NETWORK": "true",
            "OPENMANUS_MAX_CONCURRENT_RUNS": "8",
            "OPENMANUS_MAX_RUNS_PER_USER": "20",
            "OPENMANUS_RUN_TIMEOUT": "180",
            "OPENMANUS_HUMAN_INPUT_TIMEOUT": "180",
            "ANONYMIZED_TELEMETRY": "false",  # browser-use usage reporting
        }
    )
    return env


def _playwright_env(
    base_url: str, fake_url: str, model: str, admin_password: str, output_dir: Path
) -> Dict[str, str]:
    env = _base_env()
    env.update(
        {
            "E2E_BASE_URL": base_url,
            "E2E_FAKE_LLM_URL": fake_url,
            "E2E_ADMIN_USERNAME": ADMIN_USERNAME,
            "E2E_ADMIN_PASSWORD": admin_password,
            "E2E_LLM_API_KEY": FAKE_API_KEY,
            "E2E_LLM_MODEL": model,
            "E2E_OUTPUT_DIR": str(output_dir),
        }
    )
    return env


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the OpenManus end-to-end tests (fake LLM + web server + Playwright).",
        epilog="Arguments after '--' are passed to 'playwright test'.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--screenshots",
        action="store_true",
        help=f"capture the documentation screenshots into {SCREENSHOTS_DIR}",
    )
    mode.add_argument(
        "--serve",
        action="store_true",
        help="only start the fake LLM and the server, until interrupted",
    )
    parser.add_argument(
        "--build", action="store_true", help="rebuild the web UI even if built"
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the temporary data directory"
    )
    parser.add_argument("--port", type=int, default=0, help="web server port")
    parser.add_argument("playwright_args", nargs="*", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _interrupt_on_sigterm() -> None:
    """Handle SIGTERM like Ctrl+C so that the services are always stopped."""

    def handler(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    _interrupt_on_sigterm()
    if shutil.which("npx") is None:
        print("npx (Node.js) is required on PATH", file=sys.stderr)
        return 2
    _ensure_web_build(args.build)

    work_dir = Path(tempfile.mkdtemp(prefix="openmanus-e2e-"))
    fake_port, web_port = _free_port(), args.port or _free_port()
    fake_url = f"http://127.0.0.1:{fake_port}"
    base_url = f"http://127.0.0.1:{web_port}"
    admin_password = secrets.token_urlsafe(16)
    model = SCREENSHOT_MODEL if args.screenshots else FAKE_MODEL
    _write_config(work_dir / "config", f"{fake_url}/v1", model)

    exit_code = 1
    services: List[Service] = []
    with ExitStack() as stack:
        stack.callback(lambda: [service.stop() for service in reversed(services)])
        try:
            fake = Service(
                "fake LLM",
                [
                    sys.executable,
                    "-m",
                    "tests.fake_llm.server",
                    "--port",
                    str(fake_port),
                    "--slow-seconds",
                    str(SLOW_SECONDS),
                    "--log-file",
                    str(work_dir / "fake-llm.jsonl"),
                ],
                _base_env(),
                work_dir / "fake-llm.log",
            )
            services.append(fake)
            fake.wait_healthy(f"{fake_url}/health")

            server = Service(
                "web server",
                [sys.executable, "web_main.py"],
                _server_env(work_dir, web_port, f"{fake_url}/v1", admin_password),
                work_dir / "server.log",
            )
            services.append(server)
            server.wait_healthy(f"{base_url}/api/health")
            print(f"OpenManus is running at {base_url} (fake LLM at {fake_url})")

            if args.serve:
                print(f"Sign in as {ADMIN_USERNAME} / {admin_password}; Ctrl+C stops.")
                while all(service.process.poll() is None for service in services):
                    time.sleep(1)
                return 1

            playwright = ["npx", "playwright", "test"]
            if args.screenshots:
                SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                playwright += ["--project=screenshots"]
            env = _playwright_env(
                base_url, fake_url, model, admin_password, work_dir / "playwright"
            )
            if args.screenshots:
                env["E2E_SCREENSHOTS_DIR"] = str(SCREENSHOTS_DIR)
            exit_code = _run(playwright + args.playwright_args, WEB_DIR, env)
        except KeyboardInterrupt:
            exit_code = 130
        except RuntimeError as e:
            print(f"e2e: {e}", file=sys.stderr)
            exit_code = 1
        finally:
            if exit_code not in (0, 130):
                for service in services:
                    print(f"\n----- {service.name} log ({service.log_path}) -----")
                    print(service.tail())

    if args.keep or exit_code not in (0, 130):
        print(f"Kept the test data in {work_dir}")
    else:
        shutil.rmtree(work_dir, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
