"""Self-hosted worker agent runtime.

A ``splitsmith agent`` process runs on someone else's box (a home NAS, a spare
server) and lends its compute to the hosted fleet. It is the self-hosted twin
of ``splitsmith worker`` (see ``docs/saas-readiness/04-compute-backends.md``),
but with two differences that matter:

1. It does not carry the hosted server's secrets. It bootstraps them once, at
   registration time, by exchanging a one-time token (shown once by the admin
   UI) for a credential bundle - the Postgres URL, the public server URL, and
   the S3 bundle when object storage is configured. The bundle is cached in
   ``agent.json`` (mode 0600) in the state dir so restarts skip registration.

2. It does not hold a Postgres connection open between jobs. Instead it keeps a
   cheap long-lived SSE channel to the server and drains the queue only when the
   server pushes a ``wake``. Between drains it holds nothing against the
   database, so a scale-to-zero Neon compute can actually suspend - the same
   reason ``splitsmith worker --one-shot`` exists, but push-driven instead of
   cron-driven.

The runtime is two cooperating coroutines joined by an :class:`asyncio.Event`:

- the *reader* keeps the SSE channel connected (reconnecting with exponential
  backoff), parses wake / enabled / disabled / replaced frames, and flips the
  wake event;
- the *drainer* waits on that event and runs one ``run_worker(..., wait=False)``
  drain per wake. A wake that lands mid-drain re-arms the event, so exactly one
  more drain follows - never lost, never concurrent.

Everything network-facing takes an injectable ``httpx`` transport and the SSE
parsing is a pure function, so the whole thing is unit-testable without a
server (see ``tests/test_agent.py``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_STATE_FILENAME = "agent.json"

# SSE channel read timeout headroom over the server's keepalive cadence: the
# server emits a ``: ka`` comment well inside this window, so a silent read that
# blows past it means the connection is dead, not merely idle.
_CHANNEL_TIMEOUT = httpx.Timeout(10.0, read=90.0)

_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 60.0
# A connection that stayed up longer than this is treated as healthy, so the
# next disconnect restarts backoff from the floor instead of the last ceiling.
_HEALTHY_UPTIME = 60.0


class AgentState(BaseModel):
    """Registered-agent identity + credential bundle, cached as ``agent.json``.

    ``credentials`` is the raw bundle returned by ``POST /api/workers/register``:
    ``{"database_url": ..., "public_url": ..., "s3": {...} | None}``. It is kept
    as an opaque dict so a server-side addition to the bundle does not require an
    agent redeploy to round-trip.
    """

    server_url: str
    worker_id: str
    worker_token: str
    credentials: dict

    @classmethod
    def load(cls, state_dir: Path) -> AgentState | None:
        """Load ``agent.json`` from ``state_dir``; return ``None`` if absent."""
        path = Path(state_dir) / _STATE_FILENAME
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, state_dir: Path) -> None:
        """Write ``agent.json`` to ``state_dir`` with owner-only (0600) perms.

        The bundle carries the Postgres URL and S3 secret key, so the file must
        never be group/world readable. We create with 0600 directly (rather than
        write-then-chmod) so the secret is never briefly visible under a lax
        umask, and re-chmod an existing file in case it predates this code.
        """
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _STATE_FILENAME
        payload = self.model_dump_json()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        path.chmod(0o600)


def _agent_version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - version is cosmetic metadata, never fatal
        return "unknown"


def register(
    server_url: str,
    token: str,
    state_dir: Path,
    *,
    concurrency: int = 1,
    transport: httpx.BaseTransport | None = None,
) -> AgentState:
    """Exchange a one-time registration token for a credential bundle.

    Synchronous by design: this runs once at bootstrap, before the async
    runtime starts. Posts ``{token, info}`` to ``/api/workers/register`` where
    ``info`` is advisory box metadata the admin UI can surface. On success it
    persists and returns the :class:`AgentState`; any non-2xx is turned into a
    clear :class:`RuntimeError` (the server answers a bad/spent token with a
    uniform 404, so there is nothing more specific to say).
    """
    info = {
        "agent_version": _agent_version(),
        "hostname": socket.gethostname(),
        "concurrency": concurrency,
    }
    with httpx.Client(transport=transport, timeout=30.0) as client:
        response = client.post(f"{server_url}/api/workers/register", json={"token": token, "info": info})
    if response.status_code // 100 != 2:
        raise RuntimeError(
            f"agent registration failed ({response.status_code}): the token may be "
            "invalid, already used, or the server URL is wrong."
        )
    body = response.json()
    state = AgentState(
        server_url=server_url,
        worker_id=body["worker_id"],
        worker_token=body["worker_token"],
        credentials=body["credentials"],
    )
    state.save(state_dir)
    return state


def apply_credentials(state: AgentState) -> None:
    """Push the registered bundle into the process environment.

    ``run_worker`` and ``build_worker_state`` read the DB URL, public URL and
    ``SPLITSMITH_S3_*`` from ``os.environ``, so the bundle has to land there
    before the drain loop starts. ``SPLITSMITH_PUBLIC_URL`` is the server's URL
    (the agent's view of home); ``SPLITSMITH_EMAIL_BACKEND`` is a setdefault so
    a box-local override wins over the ``console`` default.
    """
    creds = state.credentials
    os.environ["SPLITSMITH_MODE"] = "hosted"
    os.environ["SPLITSMITH_DATABASE_URL"] = creds["database_url"]
    os.environ["SPLITSMITH_PUBLIC_URL"] = state.server_url
    os.environ.setdefault("SPLITSMITH_EMAIL_BACKEND", "console")

    s3 = creds.get("s3")
    if s3:
        os.environ["SPLITSMITH_S3_BUCKET"] = s3["bucket"]
        os.environ["SPLITSMITH_S3_ENDPOINT_URL"] = s3["endpoint_url"] or ""
        os.environ["SPLITSMITH_S3_REGION"] = s3["region"]
        os.environ["SPLITSMITH_S3_ACCESS_KEY_ID"] = s3["access_key_id"]
        os.environ["SPLITSMITH_S3_SECRET_ACCESS_KEY"] = s3["secret_access_key"]


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


class _SSEParser:
    """Incremental SSE parser: feed byte chunks, get back event names.

    Kept stateful (buffer + pending event name survive across ``feed`` calls) so
    an event split across two network chunks is reassembled. Only the ``event:``
    field is meaningful to the agent - ``data:`` payloads are always ``{}`` and
    comment lines (``: ka`` keepalives) are dropped. An event is dispatched when
    its terminating blank line arrives.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._event: str | None = None

    def feed(self, chunk: bytes | str) -> list[str]:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        self._buffer += chunk
        events: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line == "":
                if self._event is not None:
                    events.append(self._event)
                    self._event = None
            elif line.startswith(":"):
                continue  # comment / keepalive
            elif line.startswith("event:"):
                self._event = line[len("event:") :].strip()
            # data: / id: / retry: lines carry nothing the agent acts on
        return events


def parse_sse_events(chunks: Iterable[bytes | str]) -> Iterable[str]:
    """Pure helper: parse a finite iterable of SSE chunks into event names."""
    parser = _SSEParser()
    for chunk in chunks:
        yield from parser.feed(chunk)


# ---------------------------------------------------------------------------
# Wake coordination + the two coroutines
# ---------------------------------------------------------------------------


class _WakeCoordinator:
    """Shared state between the reader and the drainer.

    ``wake_event`` is the one-slot signal the drainer waits on; ``disabled`` is
    the admin toggle. A ``wake`` while disabled is dropped; ``enabled`` clears
    the flag *and* fires a catch-up wake so a job queued during the disabled
    window is not stranded until the next push.
    """

    def __init__(self) -> None:
        self.wake_event = asyncio.Event()
        self.disabled = False

    def dispatch(self, name: str) -> str | None:
        """Apply one SSE event; return ``"reconnect"`` to force a reconnect."""
        if name == "wake":
            if not self.disabled:
                self.wake_event.set()
        elif name == "enabled":
            self.disabled = False
            self.wake_event.set()
        elif name == "disabled":
            self.disabled = True
        elif name == "replaced":
            return "reconnect"
        return None


async def _reader_loop(
    client: httpx.AsyncClient,
    server_url: str,
    worker_token: str,
    coordinator: _WakeCoordinator,
    stop_event: asyncio.Event,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_connections: int | None = None,
) -> None:
    """Keep the SSE wake channel connected and translate frames into wakes.

    Reconnects with exponential backoff (1s..60s), resetting the backoff to the
    floor after any connection that stayed healthy past ``_HEALTHY_UPTIME``. A
    ``404`` means the worker row was revoked or deleted - there is nothing to
    reconnect to, so it raises ``SystemExit(3)``. ``max_connections`` caps the
    reconnect count for tests; production leaves it ``None`` (run forever).
    """
    backoff = _BACKOFF_MIN
    connections = 0
    while not stop_event.is_set():
        if max_connections is not None and connections >= max_connections:
            return
        connections += 1
        connected_at: float | None = None
        try:
            async with client.stream(
                "GET",
                f"{server_url}/api/workers/channel",
                headers={"Authorization": f"Bearer {worker_token}"},
                timeout=_CHANNEL_TIMEOUT,
            ) as response:
                if response.status_code == 404:
                    await response.aread()
                    logger.error("worker channel returned 404: token revoked or worker deleted; exiting")
                    raise SystemExit(3)
                response.raise_for_status()
                connected_at = time.monotonic()
                parser = _SSEParser()
                async for chunk in response.aiter_bytes():
                    if _feed_and_dispatch(parser, chunk, coordinator):
                        logger.info("worker replaced by another connection; reconnecting")
                        break
        except SystemExit:
            # Unblock the drainer so it can observe stop_event and exit too.
            stop_event.set()
            coordinator.wake_event.set()
            raise
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("worker channel error (%s); reconnecting", exc)

        if connected_at is not None and (time.monotonic() - connected_at) > _HEALTHY_UPTIME:
            backoff = _BACKOFF_MIN
        if stop_event.is_set() or (max_connections is not None and connections >= max_connections):
            return
        await sleep(backoff)
        backoff = min(backoff * 2, _BACKOFF_MAX)


def _feed_and_dispatch(parser: _SSEParser, chunk: bytes, coordinator: _WakeCoordinator) -> bool:
    """Feed one chunk through the parser; return True if a reconnect was signalled."""
    for name in parser.feed(chunk):
        if coordinator.dispatch(name) == "reconnect":
            return True
    return False


async def _run_worker_once(db_url: str, concurrency: int) -> None:
    """One one-shot queue drain. Kept module-level so tests can swap it out."""
    from .queue import run_worker

    await run_worker(db_url, concurrency=concurrency, wait=False)


async def _drain_loop(
    wake_event: asyncio.Event,
    stop_event: asyncio.Event,
    db_url: str,
    concurrency: int,
    *,
    run: Callable[[str, int], Awaitable[None]],
) -> None:
    """Run exactly one drain per wake, serialised.

    The event is cleared *before* the drain, so a wake that arrives while a
    drain is in flight re-arms it and the loop runs one more drain afterwards -
    never lost, never concurrent. Multiple wakes during one drain collapse into
    a single follow-up (the event is one-slot).
    """
    while not stop_event.is_set():
        await wake_event.wait()
        if stop_event.is_set():
            return
        wake_event.clear()
        await run(db_url, concurrency)


async def run_agent(
    server_url: str,
    *,
    registration_token: str | None,
    state_dir: Path,
    concurrency: int = 1,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Bootstrap credentials (if needed) and run the wake/drain loop forever.

    Loads ``agent.json``; if it is missing and a ``registration_token`` was
    given, registers first; if both are missing it raises (the CLI turns that
    into exit 2). Applies the credential bundle to the environment, then runs
    the reader and drainer concurrently until the reader raises ``SystemExit``
    (worker revoked) or the process is interrupted.
    """
    state = AgentState.load(state_dir)
    if state is None:
        if registration_token is None:
            raise RuntimeError(
                "no agent.json in the state dir and no registration token given: "
                "run once with --token <token> to register this agent."
            )
        state = register(
            server_url, registration_token, state_dir, concurrency=concurrency, transport=transport
        )

    apply_credentials(state)
    db_url = state.credentials["database_url"]

    coordinator = _WakeCoordinator()
    stop_event = asyncio.Event()

    async with httpx.AsyncClient(transport=transport) as client:
        reader_task = asyncio.create_task(
            _reader_loop(client, server_url, state.worker_token, coordinator, stop_event)
        )
        drainer_task = asyncio.create_task(
            _drain_loop(coordinator.wake_event, stop_event, db_url, concurrency, run=_run_worker_once)
        )
        tasks = {reader_task, drainer_task}
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            stop_event.set()
            coordinator.wake_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
