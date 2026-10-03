"""Docker-compose proof for hosted connection pooling (#1178).

Three things the SQLite suite cannot show, all against the compose
stack's live Postgres and the API + worker containers that run on it:

1. The #423 crash does not come back: a sync handler and an async handler
   both touch the database in the API process, the worker boots against
   the same database, and neither container logs "attached to a different
   loop". (The worker-*job* repro is
   ``test_hosted_docker_smoke.test_worker_runs_compute_job_end_to_end``.)
2. Pooling is real, on both of the API's engines: forty requests, half
   through a sync handler (the ``DbRunner`` loop's engine) and half through
   an async one (uvicorn's loop's engine), start almost no new Postgres
   sessions (``pg_stat_database.sessions``). NullPool, or either loop left
   on the NullPool fallback, starts one per request on that path, twenty
   or more in all. The oldest API connection's ``backend_start`` must also
   survive the run.
3. A connection the server closes while idle (Neon's Activity Monitor does
   this after five minutes) costs nothing: ``pg_terminate_backend`` on the
   API's idle connections, and the next requests still answer 200 because
   ``pool_pre_ping`` finds the dead connection at checkout and replaces it.
   Without pre_ping the first request after the termination answers 500.

Run with ``uv run pytest -m docker -n0 tests/test_pooling_docker.py -v``.
"""

from __future__ import annotations

import subprocess
import time

import httpx
import pytest

from .test_hosted_docker_smoke import API_BASE, _compose, _compose_env, _magic_link_login, _psql

pytestmark = pytest.mark.docker

# Postgres flushes a backend's pending statistics (the session counter
# included) at most once a second while it idles; wait longer than that
# before reading ``pg_stat_database`` so a read sees every session that
# has started.
_STATS_FLUSH_S = 2.0
WORKER_CONNECT_TIMEOUT_S = 60.0


def _api_addrs() -> str:
    """The API container's addresses as a SQL ``inet`` list.

    The API and the worker both connect as ``splitsmith_app`` and neither
    sets ``application_name`` (Procrastinate's psycopg pool included: its
    LISTEN connection shows an empty name), so the role cannot tell them
    apart. The client address can. Left unfiltered, the worker's long-lived
    LISTEN connection would satisfy the ``backend_start`` assertions
    against NullPool whenever the worker happened to be up, and the idle
    test would terminate it.
    """
    cid = subprocess.run(
        _compose("ps", "-q", "splitsmith"), capture_output=True, text=True, env=_compose_env(), check=True
    ).stdout.strip()
    assert cid, "no running container for the compose service 'splitsmith'"
    ips = subprocess.run(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", cid],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert ips, f"container {cid} has no network address"
    return ", ".join(f"'{ip}'::inet" for ip in ips)


def _app_conns(addrs: str, *, api: bool) -> str:
    """``FROM ... WHERE`` selecting the API's (``api=True``) or every other
    container's (the worker's) ``splitsmith_app`` connections."""
    op = "IN" if api else "NOT IN"
    return (
        "FROM pg_stat_activity WHERE usename = 'splitsmith_app' "
        f"AND datname = 'splitsmith' AND client_addr {op} ({addrs})"
    )


def _wait_for_worker_connection(addrs: str) -> None:
    """Block until the worker holds a database connection.

    ``hosted_stack`` waits for the API's health only; the worker starts
    after it. Its log is evidence about #423 only once it has touched the
    database."""
    deadline = time.time() + WORKER_CONNECT_TIMEOUT_S
    while time.time() < deadline:
        if int(_psql(f"SELECT count(*) {_app_conns(addrs, api=False)}")) >= 1:
            return
        time.sleep(1.0)
    pytest.fail(f"the worker never connected to Postgres within {WORKER_CONNECT_TIMEOUT_S:.0f}s")


def _sessions_started() -> int:
    """Postgres 16's counter of sessions ever started on the database."""
    time.sleep(_STATS_FLUSH_S)
    return int(_psql("SELECT sessions FROM pg_stat_database WHERE datname = 'splitsmith'"))


def _container_logs(service: str) -> str:
    out = subprocess.run(
        _compose("logs", "--no-color", service), capture_output=True, text=True, env=_compose_env()
    )
    return out.stdout + out.stderr


def _ping(cookies: dict[str, str], path: str) -> None:
    r = httpx.get(f"{API_BASE}{path}", cookies=cookies, timeout=10.0)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"


def _signed_in_with_a_match(email: str) -> tuple[dict[str, str], str, str]:
    """Sign in and create a match; return ``(cookies, sync_path, async_path)``.

    The two paths reach the API's two engines, which is the point:
    ``GET /api/matches/{id}/match/stages`` is a plain ``def`` handler whose
    ``state.match()`` loads the match doc through ``run_sync`` -- the
    ``DbRunner`` loop's engine. ``GET /api/me/recent-projects`` is
    ``async def`` and queries on uvicorn's loop. (``/api/me`` is a ``def``
    handler too, but its only database work is the session lookup in the
    async auth dependency, so it never reaches the DbRunner.)
    """
    _, secret = _magic_link_login(email)
    cookies = {"splitsmith_session": secret}
    resp = httpx.post(
        f"{API_BASE}/api/match/create-manual",
        json={
            "name": "Pooling Match",
            "stages": [{"stage_number": 1, "stage_name": "Stage 1"}],
            "primary_shooter": {"name": "Test Shooter"},
        },
        cookies=cookies,
        timeout=30.0,
    )
    assert resp.status_code == 200, resp.text
    match_id = resp.json()["match_id"]
    return cookies, f"/api/matches/{match_id}/match/stages", "/api/me/recent-projects?detail=true"


def test_sync_and_async_handlers_share_pooled_connections_without_loop_errors(hosted_stack: None) -> None:
    cookies, sync_path, async_path = _signed_in_with_a_match("pooling@example.com")
    addrs = _api_addrs()
    api_conns = _app_conns(addrs, api=True)
    # Before measuring: the worker's boot sessions must not land inside the
    # counted window, and its log below must be one that saw the database.
    _wait_for_worker_connection(addrs)

    # Warm both engines' pools before counting.
    _ping(cookies, sync_path)
    _ping(cookies, async_path)
    oldest_before = _psql(f"SELECT min(backend_start) {api_conns}")
    assert oldest_before, "the API holds no connection after a request; the pool is not pooling"

    sessions_before = _sessions_started()
    t0 = time.time()
    for _ in range(20):
        _ping(cookies, sync_path)
        _ping(cookies, async_path)
    elapsed = time.time() - t0
    sessions_after = _sessions_started()

    # The window holds two ``_psql`` probes (the two counter reads, each its
    # own session) plus slack for a pool growing by one connection. An
    # engine on the NullPool fallback starts at least one session per
    # request it serves: twenty or more here. ``min(backend_start)`` below
    # cannot see that case -- the surviving pooled engine keeps the oldest
    # connection alive -- which is why this counter is the pooling proof.
    probes = 2
    started = sessions_after - sessions_before
    assert started <= probes + 2, (
        f"{started} Postgres sessions started over 40 requests ({probes} are this test's probes); "
        "an API engine is opening a connection per request instead of reusing its pool"
    )
    oldest_after = _psql(f"SELECT min(backend_start) {api_conns}")
    assert (
        oldest_after == oldest_before
    ), "the oldest API connection was replaced during the run; connections are not being reused"

    # Tripwires, not proof: the sessions counter above is the pooling
    # assertion. These catch a pool that grows without bound or a
    # pathological slowdown (40 requests used to pay a fresh handshake
    # each, ~5 ms on the compose network, ~200 ms against Neon).
    count = int(_psql(f"SELECT count(*) {api_conns}"))
    assert count <= 30, f"{count} connections: more than two full pools (2 x (5 + 10))"
    assert elapsed < 20.0

    # Compose service names (docker-compose.yml): the API is ``splitsmith``,
    # the queue drainer is ``worker``.
    for service in ("splitsmith", "worker"):
        logs = _container_logs(service)
        assert "attached to a different loop" not in logs, f"{service} logged the #423 crash"


def test_idle_connection_closed_by_the_server_is_reconnected_silently(hosted_stack: None) -> None:
    """Neon's Activity Monitor closes idle connections after five minutes.
    ``pool_pre_ping`` must turn that into a reconnect, not a 500 (removing
    it makes the first request below answer 500)."""
    cookies, sync_path, async_path = _signed_in_with_a_match("pooling-idle@example.com")
    api_conns = _app_conns(_api_addrs(), api=True)
    _ping(cookies, sync_path)
    _ping(cookies, async_path)

    terminated = _psql(f"SELECT count(pg_terminate_backend(pid)) {api_conns} AND state = 'idle'")
    assert int(terminated) >= 1, "nothing to terminate: the pool kept no idle connection"

    # The next checkout on each engine pings, finds the connection dead,
    # reconnects.
    _ping(cookies, sync_path)
    _ping(cookies, async_path)
    logs = _container_logs("splitsmith")
    assert "attached to a different loop" not in logs
