"""Embeddable FastAPI entrypoint for the desktop shell (issue #131).

Spawn the splitsmith UI server as a sidecar process or context manager,
discover the bound port via a structured handshake, and shut it down
cleanly. The existing ``splitsmith ui`` CLI keeps its Ctrl-C summary
UX -- this is a parallel surface that adds no flag noise to the CLI.

Tauri-style env-var launch (the desktop shell from #129 spawns this as
a child process and parses the banner line to learn the bound port and
resolved runtime paths)::

    SPLITSMITH_PROJECT_ROOT=/Users/me/match \\
    SPLITSMITH_FFMPEG=/path/to/bundled/ffmpeg \\
    SPLITSMITH_ARTIFACTS_DIR=/path/to/bundled/artifacts \\
    SPLITSMITH_PORT=0 \\
    python -m splitsmith.ui.embedded
    # -> stderr: SPLITSMITH_READY {"host": "127.0.0.1", "port": 53241, ...}

In-process callers (tests, future Python embedders) use the context
manager::

    from splitsmith.ui.embedded import run_embedded

    with run_embedded(port=0) as handle:
        # handle.base_url, handle.port, handle.artifacts_dir, ...
        requests.get(f"{handle.base_url}/api/health")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from ..runtime import runtime as process_runtime
from .logging_setup import configure_file_logging
from .server import create_app

logger = logging.getLogger(__name__)

READY_PREFIX = "SPLITSMITH_READY"
DEFAULT_STARTUP_TIMEOUT_S = 10.0
DEFAULT_SHUTDOWN_TIMEOUT_S = 30.0

ENV_PROJECT_ROOT = "SPLITSMITH_PROJECT_ROOT"
ENV_PROJECT_NAME = "SPLITSMITH_PROJECT_NAME"
ENV_HOST = "SPLITSMITH_HOST"
ENV_PORT = "SPLITSMITH_PORT"
ENV_READY_FD = "SPLITSMITH_READY_FD"
ENV_LAB_ENABLED = "SPLITSMITH_LAB_ENABLED"


@dataclass(frozen=True)
class ServerHandle:
    """Structured info about a running embedded server.

    ``base_url`` is the convenience HTTP root the caller should hit;
    ``artifacts_dir`` and ``ffmpeg_binary`` reflect the resolved
    :mod:`splitsmith.runtime`, so the desktop shell can verify its env
    overrides made it through end-to-end (acceptance criterion in #131).
    """

    host: str
    port: int
    pid: int
    base_url: str
    artifacts_dir: str
    ffmpeg_binary: str
    log_file: str | None = None

    def as_banner(self) -> str:
        """Render the one-line ``SPLITSMITH_READY {json}`` handshake."""
        return f"{READY_PREFIX} {json.dumps(asdict(self), sort_keys=True)}"


def _pick_free_port(host: str) -> int:
    """Bind, read back, release -- gives uvicorn a known port without holding it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _wait_for_health(base_url: str, *, timeout: float) -> None:
    """Poll ``/api/health`` until 200 or ``timeout`` elapses.

    Uses ``httpx`` (already a dep via FastAPI's TestClient and the
    scoreboard client) so we don't pull in ``requests`` just for this.
    """
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(f"{base_url}/api/health")
            if resp.status_code == 200:
                return
            last_exc = RuntimeError(f"/api/health returned {resp.status_code}")
        except httpx.HTTPError as exc:
            last_exc = exc
        time.sleep(0.05)
    raise TimeoutError(
        f"embedded server did not become healthy within {timeout:.1f}s " f"(last error: {last_exc!r})"
    )


def _emit_banner(handle: ServerHandle, ready_fd: int | None) -> None:
    """Write the handshake line to ``ready_fd`` (if set) or stderr."""
    line = handle.as_banner() + "\n"
    if ready_fd is not None:
        os.write(ready_fd, line.encode("utf-8"))
        return
    sys.stderr.write(line)
    sys.stderr.flush()


@contextmanager
def run_embedded(
    *,
    project_root: Path | None = None,
    project_name: str | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    lab_enabled: bool = False,
    ready_fd: int | None = None,
    log_file: Path | None = None,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_S,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
) -> Iterator[ServerHandle]:
    """Boot uvicorn on a daemon thread for the duration of the context.

    The server uses the same :func:`create_app` factory as the CLI's
    ``serve`` -- this surface is purely additive (per #131's "do not
    modify ``serve()`` / ``_JobAwareServer``" constraint).

    ``port=0`` picks a free port up-front so the handle can carry the
    real value before the server thread starts. The banner is emitted
    after ``/api/health`` returns 200 and *before* the context yields,
    so subprocess parents can synchronously parse it on stderr / fd.
    """
    import uvicorn

    if port == 0:
        port = _pick_free_port(host)

    app = create_app(
        project_root=project_root,
        project_name=project_name,
        lab_enabled=lab_enabled,
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Uvicorn installs SIGINT/SIGTERM handlers in ``server.run()`` by
    # default. From a non-main thread those calls raise ``ValueError``;
    # disable them so the embedded thread starts cleanly and the
    # context manager (or main()) owns signal delivery.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    thread = threading.Thread(target=server.run, name="splitsmith-embedded", daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    rt = process_runtime()
    handle = ServerHandle(
        host=host,
        port=port,
        pid=os.getpid(),
        base_url=base_url,
        artifacts_dir=str(rt.artifacts_dir),
        ffmpeg_binary=rt.ffmpeg_binary,
        log_file=str(log_file) if log_file else None,
    )

    # Expose a stop callback so the in-process /api/shutdown route (issue
    # #369) can ask uvicorn to exit. The CLI's ``splitsmith ui`` path
    # never reaches here, so the route stays drain-only there.
    splitsmith_state = app.state.splitsmith_state
    splitsmith_state.shutdown_handler = lambda: setattr(server, "should_exit", True)

    try:
        _wait_for_health(base_url, timeout=startup_timeout)
        _emit_banner(handle, ready_fd)
        yield handle
    finally:
        # Drain in-flight jobs before flipping ``should_exit``. If
        # /api/shutdown or SIGTERM already kicked the drain, this is a
        # no-op past the first transition.
        splitsmith_state.jobs.begin_shutdown()
        splitsmith_state.jobs.wait_for_drain(shutdown_timeout)
        server.should_exit = True
        thread.join(timeout=shutdown_timeout)
        if thread.is_alive():
            logger.warning(
                "embedded server thread did not stop within %.1fs; " "escalating to force_exit",
                shutdown_timeout,
            )
            server.force_exit = True
            thread.join(timeout=5.0)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(name: str) -> bool:
    raw = os.environ.get(name, "")
    return raw.lower() in {"1", "true", "yes", "on"}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="splitsmith-server",
        description=(
            "Embeddable splitsmith UI server. Flags override env vars; " "env vars override defaults."
        ),
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help=(
            "Directory for the rotated log file. "
            f"Default: <user_config_dir>/logs (env: {'SPLITSMITH_LOG_DIR'})."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help=("Logging level for the file handler. " f"Default: INFO (env: {'SPLITSMITH_LOG_LEVEL'})."),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """``splitsmith-server`` / ``python -m splitsmith.ui.embedded``.

    Reads launch parameters from the environment (so the desktop shell
    can spawn this without a wrapper script):

    * ``SPLITSMITH_PROJECT_ROOT`` -- optional pre-bound match dir.
    * ``SPLITSMITH_PROJECT_NAME`` -- display name override.
    * ``SPLITSMITH_HOST`` / ``SPLITSMITH_PORT`` -- bind address; port=0
      picks free.
    * ``SPLITSMITH_READY_FD`` -- file descriptor to write the banner
      to instead of stderr (the shell typically uses fd 3).
    * ``SPLITSMITH_LAB_ENABLED`` -- truthy values enable the ``/api/lab/*``
      routes.
    * ``SPLITSMITH_LOG_DIR`` / ``SPLITSMITH_LOG_LEVEL`` -- file logging
      destination + level (or pass ``--log-dir`` / ``--log-level``).
    * runtime overrides from :mod:`splitsmith.runtime` apply unchanged.

    SIGTERM and SIGINT trigger a graceful shutdown and exit status 0.
    """
    args = _build_arg_parser().parse_args(argv)

    project_root_raw = os.environ.get(ENV_PROJECT_ROOT)
    project_root = Path(project_root_raw) if project_root_raw else None
    project_name = os.environ.get(ENV_PROJECT_NAME)
    host = os.environ.get(ENV_HOST, "127.0.0.1")
    port = _env_int(ENV_PORT, 0)
    ready_fd_val = os.environ.get(ENV_READY_FD)
    ready_fd = int(ready_fd_val) if ready_fd_val else None
    lab_enabled = _env_bool(ENV_LAB_ENABLED)

    log_file = configure_file_logging(log_dir=args.log_dir, level=args.log_level)
    logger.info("file logging enabled at %s", log_file)

    stop = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("embedded server received signal %d; shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    with run_embedded(
        project_root=project_root,
        project_name=project_name,
        host=host,
        port=port,
        lab_enabled=lab_enabled,
        ready_fd=ready_fd,
        log_file=log_file,
    ):
        # Block until the signal handler flips the event. The context
        # manager's ``__exit__`` takes care of orderly shutdown.
        stop.wait()


if __name__ == "__main__":
    main()
