"""Docker-compose proof for hosted connection pooling (#1178).

Three things the SQLite suite cannot show, all against the compose
stack's live Postgres and the API + worker containers that run on it:

1. The #423 crash does not come back: a sync handler and an async handler
   both touch the database in the API process, the worker boots against
   the same database, and neither container logs "attached to a different
   loop". (The worker-*job* repro is
   ``test_hosted_docker_smoke.test_worker_runs_compute_job_end_to_end``.)
2. Pooling is real: after twenty requests, at least one of the API's
   connections has a ``backend_start`` from *before* the twentieth
   request. NullPool opened and closed one per call, so every connection
   it left behind (none, usually) was fresh.
3. A connection Neon closes while idle is reconnected silently:
   ``pg_terminate_backend`` on the API's idle connections, then the next
   request still answers 200 (``pool_pre_ping``).

Run with ``uv run pytest -m docker -n0 tests/test_pooling_docker.py -v``.
"""

from __future__ import annotations

import subprocess
import time

import httpx
import pytest

from .test_hosted_docker_smoke import API_BASE, _compose, _compose_env, _magic_link_login, _psql

pytestmark = pytest.mark.docker


def _api_conns() -> str:
    """``FROM ... WHERE`` selecting the API container's own connections.

    The API and the worker both connect as ``splitsmith_app`` and neither
    sets ``application_name`` (Procrastinate's psycopg pool included: its
    LISTEN connection shows an empty name), so the role cannot tell them
    apart. The client address can: filter on the API container's IP. Left
    unfiltered, the worker's long-lived LISTEN connection would satisfy the
    ``backend_start`` assertions against NullPool whenever the worker
    happened to be up, and the idle test would terminate it.
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
    addrs = ", ".join(f"'{ip}'::inet" for ip in ips)
    return (
        "FROM pg_stat_activity WHERE usename = 'splitsmith_app' "
        f"AND datname = 'splitsmith' AND client_addr IN ({addrs})"
    )


def _container_logs(service: str) -> str:
    out = subprocess.run(
        _compose("logs", "--no-color", service), capture_output=True, text=True, env=_compose_env()
    )
    return out.stdout + out.stderr


def _ping(cookies: dict[str, str], path: str) -> None:
    r = httpx.get(f"{API_BASE}{path}", cookies=cookies, timeout=10.0)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"


def test_sync_and_async_handlers_share_pooled_connections_without_loop_errors(hosted_stack: None) -> None:
    _, secret = _magic_link_login("pooling@example.com")
    cookies = {"splitsmith_session": secret}
    api_conns = _api_conns()

    # One async handler and one sync handler, both hitting Postgres.
    _ping(cookies, "/api/me")
    _ping(cookies, "/api/me/recent-projects?detail=true")
    oldest_before = _psql(f"SELECT min(backend_start) {api_conns}")
    assert oldest_before, "the API holds no connection after a request; the pool is not pooling"

    t0 = time.time()
    for _ in range(20):
        _ping(cookies, "/api/me")
        _ping(cookies, "/api/me/recent-projects?detail=true")
    elapsed = time.time() - t0

    count = int(_psql(f"SELECT count(*) {api_conns}"))
    assert count <= 30, f"{count} connections: more than two full pools (2 x (5 + 10))"
    oldest_after = _psql(f"SELECT min(backend_start) {api_conns}")
    assert (
        oldest_after == oldest_before
    ), "the oldest API connection was replaced during the run; connections are not being reused"
    # 40 requests, each of which used to pay a fresh handshake (~5 ms on the
    # compose network, ~200 ms against Neon). Generous bound: the point is
    # the backend_start assertion above, this only catches a pathological
    # regression.
    assert elapsed < 20.0

    # Compose service names (docker-compose.yml): the API is ``splitsmith``,
    # the queue drainer is ``worker``.
    for service in ("splitsmith", "worker"):
        logs = _container_logs(service)
        assert "attached to a different loop" not in logs, f"{service} logged the #423 crash"


def test_idle_connection_closed_by_the_server_is_reconnected_silently(hosted_stack: None) -> None:
    """Neon's Activity Monitor closes idle connections after five minutes.
    ``pool_pre_ping`` must turn that into a reconnect, not a 500."""
    _, secret = _magic_link_login("pooling-idle@example.com")
    cookies = {"splitsmith_session": secret}
    api_conns = _api_conns()
    _ping(cookies, "/api/me")

    terminated = _psql(f"SELECT count(pg_terminate_backend(pid)) {api_conns} AND state = 'idle'")
    assert int(terminated) >= 1, "nothing to terminate: the pool kept no idle connection"

    # The next checkout pings, finds the connection dead, reconnects.
    _ping(cookies, "/api/me")
    _ping(cookies, "/api/me/recent-projects?detail=true")
    logs = _container_logs("splitsmith")
    assert "attached to a different loop" not in logs
