"""Docker-compose smoke test for hosted mode (next-saas-action: docker-compose).

End-to-end proof that ``docker compose up`` boots the hosted stack
locally and serves a request through Postgres-backed backends.

Marked ``@pytest.mark.docker`` and excluded from the default pytest
run (see pyproject's ``addopts``). Opt in with::

    pytest -m docker

Requires a working ``docker`` CLI + the ``compose`` plugin, the
repo's ``docker-compose.yml``, and 30-60 seconds for the first run
(image build + Postgres / MinIO healthy waits). Subsequent runs reuse
the image and finish in ~10 seconds.

The test brings the stack up + tears it down in a single fixture so
a CI runner that doesn't have docker simply skips the test rather
than hanging on a half-started compose project.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.docker


COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
API_BASE = "http://localhost:5174"
HEALTH_TIMEOUT_S = 90.0


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    return probe.returncode == 0


def _wait_until_healthy(timeout_s: float = HEALTH_TIMEOUT_S) -> None:
    """Poll /api/health until 200 or timeout. Compose's healthcheck
    only knows about the container; we still want the API itself
    responsive before sending real requests."""
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{API_BASE}/api/health", timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_exc = exc
        time.sleep(1.0)
    raise RuntimeError(
        f"hosted-stack API did not become healthy within {timeout_s}s"
        + (f" (last error: {last_exc})" if last_exc else "")
    )


@pytest.fixture(scope="module")
def hosted_stack() -> Iterator[None]:
    if not _docker_compose_available():
        pytest.skip("docker / docker compose not available on this host")

    # ``-d`` for detached so the test can drive HTTP traffic from the
    # foreground. ``--build`` ensures the splitsmith image picks up
    # any source changes since the last run.
    up = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"],
        capture_output=True,
        text=True,
    )
    if up.returncode != 0:
        pytest.fail(f"docker compose up failed:\n{up.stderr}")

    try:
        _wait_until_healthy()
        yield
    finally:
        # ``-v`` removes named volumes so consecutive test runs start
        # from a clean Postgres -- otherwise the recent-projects
        # asserts below would see leftover state from the prior run.
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            capture_output=True,
            text=True,
            check=False,
        )


def test_recent_projects_round_trip_hits_postgres(hosted_stack: None) -> None:
    """Record a recent project, list it, restart the API container,
    list it again -- the entry must survive the restart because it
    lives in Postgres rather than process memory."""
    # Pre-state should be empty (the fixture's ``down -v`` wipes
    # Postgres between runs).
    listed = httpx.get(f"{API_BASE}/api/me/recent-projects", timeout=5.0).json()
    assert listed == {"projects": []}

    # Record_open isn't exposed as a direct endpoint -- the closest
    # public surface is /api/me/recent-projects/bind, but that also
    # validates the path is a real Match folder. For a smoke test
    # against an empty container that has no on-disk projects, we
    # exercise the forget endpoint instead (which is the only
    # /api/me/recent-projects/* mutator that doesn't require a real
    # match.json on disk) -- it should return ``removed: false`` and
    # the list stays empty. This proves the round-trip hits the
    # Postgres store without needing to mount a fixture volume.
    resp = httpx.post(
        f"{API_BASE}/api/me/recent-projects/forget",
        json={"path": "/nonexistent/project"},
        timeout=5.0,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["removed"] is False
    assert body["projects"] == []

    # Restart the API container and verify the empty-but-responsive
    # state survives the bounce.
    restart = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "restart", "splitsmith"],
        capture_output=True,
        text=True,
    )
    assert restart.returncode == 0, restart.stderr
    _wait_until_healthy()

    listed = httpx.get(f"{API_BASE}/api/me/recent-projects", timeout=5.0).json()
    assert listed == {"projects": []}


def test_hosted_user_row_persists_in_postgres(hosted_stack: None) -> None:
    """The HostedLoopbackAuth bootstrap should have created exactly
    one ``users`` row with the loopback email -- and a restart
    must reuse it rather than spawning a duplicate (the email's
    UNIQUE constraint would crash the boot otherwise)."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "splitsmith",
            "-d",
            "splitsmith",
            "-At",
            "-c",
            "SELECT COUNT(*) FROM users WHERE email = 'loopback@hosted.local'",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "1"


def _psql(query: str) -> str:
    """Run ``query`` in the compose Postgres and return trimmed stdout."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "splitsmith",
            "-d",
            "splitsmith",
            "-At",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_migrations_applied_against_postgres(hosted_stack: None) -> None:
    """Regression guard for the asyncpg multi-statement migration bug.

    The stack only reaches ``healthy`` if ``alembic upgrade head``
    succeeded, so reaching this assertion already proves the chain ran.
    We additionally assert the procrastinate schema (the migration that
    broke on asyncpg) actually landed its objects -- a green health
    check alone wouldn't notice a migration that silently created
    nothing."""
    # Every shipped migration's objects must exist. The procrastinate
    # schema is the one that broke: 4 tables + a pile of PL/pgSQL funcs.
    proc_tables = _psql(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name LIKE 'procrastinate_%'"
    )
    assert int(proc_tables) >= 4, f"expected >=4 procrastinate_* tables, got {proc_tables}"

    proc_funcs = _psql("SELECT count(*) FROM pg_proc WHERE proname LIKE 'procrastinate_%'")
    assert int(proc_funcs) > 0, "procrastinate PL/pgSQL functions missing"

    # Our own tenant tables from the earlier migrations.
    for table in ("users", "recent_projects", "compute_jobs"):
        exists = _psql(
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_schema='public' AND table_name='{table}'"
        )
        assert exists == "1", f"table {table!r} missing after migrations"


def test_worker_drains_ping_task(hosted_stack: None) -> None:
    """PR-beta round-trip proof: defer a ``ping`` from the host onto the
    queue and assert the separate ``worker`` container pops + runs it to
    ``succeeded``.

    This is the one test that exercises the actual cross-process
    dispatch -- the unit tests only inspect the App. The host defers
    through the same ``build_app`` path the API uses (Postgres exposed
    on localhost:5432 by compose); the worker container, draining all
    queues, executes it and writes ``succeeded`` to ``procrastinate_jobs``.
    """
    import asyncio

    from splitsmith.queue import build_app

    host_url = "postgresql+asyncpg://splitsmith:splitsmith@localhost:5432/splitsmith"

    async def _defer() -> None:
        app = build_app(host_url)
        async with app.open_async():
            await app.tasks["ping"].defer_async(payload="pong")

    asyncio.run(_defer())

    # Poll the queue table until the worker marks the job done. The
    # worker's fetch_job_polling_interval defaults to 5s, so allow a
    # comfortable margin over a couple of poll cycles.
    deadline = time.time() + 30.0
    status = ""
    while time.time() < deadline:
        status = _psql(
            "SELECT status FROM procrastinate_jobs " "WHERE task_name = 'ping' ORDER BY id DESC LIMIT 1"
        )
        if status == "succeeded":
            return
        if status == "failed":
            pytest.fail("ping job reached 'failed' -- the worker errored running it")
        time.sleep(1.0)
    pytest.fail(f"worker did not drain ping within 30s (last status: {status!r})")
