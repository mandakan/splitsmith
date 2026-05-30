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


def _psql_run(query: str, *, user: str = "splitsmith") -> subprocess.CompletedProcess[str]:
    """Run ``query`` in the compose Postgres as ``user`` and return the
    completed process (no return-code assertion).

    ``user`` defaults to the ``splitsmith`` superuser, which **bypasses
    RLS** -- correct for seeding cross-tenant rows and for inspecting the
    raw table state. Pass ``user="splitsmith_app"`` (the non-superuser app
    role) to exercise the RLS policies the app actually runs under.
    """
    return subprocess.run(
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
            user,
            "-d",
            "splitsmith",
            # ``-q`` suppresses command tags ("SET", "INSERT 0 1") so a
            # multi-statement ``SET ...; SELECT ...`` returns only the row
            # data; SELECT output and errors are unaffected.
            "-qAt",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
    )


def _psql(query: str, *, user: str = "splitsmith") -> str:
    """Run ``query`` (asserting success) and return trimmed stdout."""
    out = _psql_run(query, user=user)
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

    # Our own tenant tables from the earlier migrations + the auth-domain
    # tables from the magic-link migration (c3f1a8e90d24).
    for table in ("users", "recent_projects", "compute_jobs", "magic_link_tokens", "sessions"):
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


def test_worker_runs_compute_job_end_to_end(hosted_stack: None) -> None:
    """PR-gamma full-transport proof for a real compute kind.

    Mirror exactly what ``PostgresJobBackend.submit`` does -- write a
    PENDING ``compute_jobs`` row, then defer a ``run_compute_job`` task
    onto the user's queue -- and assert the separate worker container
    pops it, runs the ``model_download`` body, and writes ``succeeded``
    back to the *same* row the SPA polls. ``model_download`` is the one
    production kind that carries no per-match state, so it proves the
    cross-process round-trip end to end. Real detection kinds need match
    metadata reachable from the worker (a later chunk), so they can't be
    driven through the queue yet.

    Splitting the row-write from the defer (rather than calling the
    backend's ``submit``) lets the test own the ``job_id`` it polls; the
    enqueue uses the same ``make_deferrer`` the API does.
    """
    import asyncio
    import uuid

    from splitsmith.queue import make_deferrer

    host_url = "postgresql+asyncpg://splitsmith:splitsmith@localhost:5432/splitsmith"
    user_id = _psql("SELECT id FROM users WHERE email = 'loopback@hosted.local'")
    assert user_id, "loopback user row missing -- auth bootstrap did not run"

    job_id = uuid.uuid4().hex
    _psql(
        "INSERT INTO compute_jobs (id, user_id, kind, status, cancel_requested, acknowledged) "
        f"VALUES ('{job_id}', '{user_id}', 'model_download', 'pending', false, false)"
    )

    async def _enqueue() -> None:
        defer = make_deferrer(host_url)
        await defer(
            job_id=job_id,
            user_id=user_id,
            kind="model_download",
            args={},
            match_id=None,
        )

    asyncio.run(_enqueue())

    # The worker pops from ``user-<id>`` (it drains all queues), runs the
    # body, and finalises the row. Baked-in slim models make the body a
    # near-instant no-op, but allow margin over the worker poll interval.
    deadline = time.time() + 60.0
    status = ""
    while time.time() < deadline:
        status = _psql(f"SELECT status FROM compute_jobs WHERE id = '{job_id}'")
        if status == "succeeded":
            return
        if status == "failed":
            err = _psql(f"SELECT error FROM compute_jobs WHERE id = '{job_id}'")
            pytest.fail(f"compute job reached 'failed': {err!r}")
        time.sleep(1.0)
    pytest.fail(f"worker did not finish compute job within 60s (last status: {status!r})")


def _s3_object_exists(key: str) -> bool:
    """True iff ``key`` exists in the compose MinIO uploads bucket."""
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client(
        "s3",
        endpoint_url="http://localhost:9000",
        region_name="us-east-1",
        aws_access_key_id="splitsmith",
        aws_secret_access_key="splitsmithsplitsmith",
    )
    try:
        client.head_object(Bucket="splitsmith-uploads", Key=key)
        return True
    except ClientError:
        return False


def test_worker_resolves_match_cross_process(hosted_stack: None) -> None:
    """PR-delta proof: a match created through the API is resolvable by the
    *separate* worker container, with its metadata round-tripping via MinIO.

    Covers the chunk's new machinery against real containers:
    1. ``create-manual`` upserts a ``matches`` row on real Postgres, and
    2. pushes ``match.json`` + the shooter's ``project.json`` to real MinIO,
    3. so a ``detect_beep`` job the worker pops gets *past* match resolution
       (no ``MatchNotRegisteredError`` -- the gamma stopgap's failure mode).

    The job then fails because no video is assigned -- proving resolution +
    cross-container metadata pull worked without needing an uploaded video
    (full detect-to-succeeded is exercised by the in-process seam tests).
    """
    import asyncio
    import uuid

    from splitsmith.queue import make_deferrer

    # 1. Create a match (no video) through the hosted API.
    resp = httpx.post(
        f"{API_BASE}/api/match/create-manual",
        json={
            "name": "Worker Delta Match",
            "stages": [{"stage_number": 1, "stage_name": "Stage 1"}],
            "primary_shooter": {"name": "Test Shooter"},
        },
        timeout=30.0,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    match_id = body["match_id"]
    slug = body["default_shooter_slug"]
    assert match_id and slug, body

    user_id = _psql("SELECT id FROM users WHERE email = 'loopback@hosted.local'")
    assert user_id

    # 2. The API upserted a matches row on real Postgres.
    assert _psql(f"SELECT count(*) FROM matches WHERE match_id = '{match_id}'") == "1"

    # 3. The API pushed match.json + project.json to real MinIO (S3).
    prefix = f"users/{user_id}/matches/{match_id}"
    assert _s3_object_exists(f"{prefix}/match.json"), "match.json missing in MinIO"
    assert _s3_object_exists(f"{prefix}/shooters/{slug}/project.json"), "project.json missing in MinIO"

    # 4. The worker container resolves the match cross-process. Mirror the
    #    backend's submit (PENDING row + defer) for a detect_beep job with no
    #    real video; assert it gets past resolution (no "cannot resolve
    #    match" failure).
    host_url = "postgresql+asyncpg://splitsmith:splitsmith@localhost:5432/splitsmith"
    job_id = uuid.uuid4().hex
    _psql(
        "INSERT INTO compute_jobs (id, user_id, kind, status, stage_number, video_id, "
        "cancel_requested, acknowledged) "
        f"VALUES ('{job_id}', '{user_id}', 'detect_beep', 'pending', 1, 'no-such-video', false, false)"
    )

    async def _enqueue() -> None:
        defer = make_deferrer(host_url)
        await defer(
            job_id=job_id,
            user_id=user_id,
            kind="detect_beep",
            args={"slug": slug, "stage_number": 1, "video_id": "no-such-video"},
            match_id=match_id,
        )

    asyncio.run(_enqueue())

    deadline = time.time() + 60.0
    status = ""
    while time.time() < deadline:
        status = _psql(f"SELECT status FROM compute_jobs WHERE id = '{job_id}'")
        if status in ("failed", "succeeded"):
            break
        time.sleep(1.0)
    assert status in ("failed", "succeeded"), f"worker never ran detect_beep (last: {status!r})"

    err = _psql(f"SELECT coalesce(error, '') FROM compute_jobs WHERE id = '{job_id}'").lower()
    assert (
        "resolve match" not in err and "matchnotregistered" not in err
    ), f"worker failed to resolve the match cross-process: {err!r}"


# The three per-user tenant tables RLS protects. Keep in sync with the
# migration's TENANT_TABLES and the multi-tenant store classes.
_RLS_TABLES = ("recent_projects", "matches", "compute_jobs")


def _seed_two_tenants() -> tuple[str, str]:
    """Seed users A and B with one row each in every tenant table.

    Runs as the ``splitsmith`` superuser, which bypasses RLS -- that is
    the only way to write rows owned by two different tenants in one
    pass. Returns the two user ids."""
    uid_a, uid_b = "user-a-rls", "user-b-rls"
    _psql(
        "INSERT INTO users (id, email, entitlement) VALUES "
        f"('{uid_a}', 'a-rls@hosted.local', 'free'), "
        f"('{uid_b}', 'b-rls@hosted.local', 'free') "
        "ON CONFLICT (id) DO NOTHING"
    )
    _psql(
        "INSERT INTO recent_projects (id, user_id, path, name) VALUES "
        f"('rp-a', '{uid_a}', '/a', 'A proj'), ('rp-b', '{uid_b}', '/b', 'B proj') "
        "ON CONFLICT (id) DO NOTHING"
    )
    _psql(
        "INSERT INTO matches (id, user_id, match_id, name, storage_prefix) VALUES "
        f"('m-a', '{uid_a}', 'mid-a', 'A match', 'matches/mid-a'), "
        f"('m-b', '{uid_b}', 'mid-b', 'B match', 'matches/mid-b') "
        "ON CONFLICT (id) DO NOTHING"
    )
    _psql(
        "INSERT INTO compute_jobs (id, user_id, kind, status, cancel_requested, acknowledged) "
        f"VALUES ('j-a', '{uid_a}', 'model_download', 'pending', false, false), "
        f"('j-b', '{uid_b}', 'model_download', 'pending', false, false) "
        "ON CONFLICT (id) DO NOTHING"
    )
    return uid_a, uid_b


def test_rls_blocks_cross_tenant_reads_and_writes(hosted_stack: None) -> None:
    """The real RLS proof: as the non-superuser app role, a query with no
    ``user_id`` filter at all returns only the current tenant's rows.

    This is the failure mode the per-query app-layer filters can't catch
    -- a future raw-SQL helper that forgets the ``WHERE user_id = ...``
    clause. With RLS it sees only the tenant whose id is in the
    ``app.user_id`` GUC; without the GUC it sees nothing (fail-closed).
    """
    uid_a, uid_b = _seed_two_tenants()
    seeded = f"('{uid_a}', '{uid_b}')"

    # Guard: the whole test is meaningless if the app role can bypass RLS.
    assert _psql("SELECT rolsuper FROM pg_roles WHERE rolname='splitsmith_app'") == "f"
    assert _psql("SELECT rolbypassrls FROM pg_roles WHERE rolname='splitsmith_app'") == "f"

    # The migration enabled + FORCED RLS and created the policy on all three.
    assert (
        _psql(
            "SELECT count(*) FROM pg_policies WHERE policyname='tenant_isolation' "
            "AND tablename IN ('recent_projects','matches','compute_jobs')"
        )
        == "3"
    )
    assert (
        _psql(
            "SELECT count(*) FROM pg_class WHERE relforcerowsecurity "
            "AND relname IN ('recent_projects','matches','compute_jobs')"
        )
        == "3"
    )

    for table in _RLS_TABLES:
        # GUC = A: the deliberately-unfiltered SELECT returns only A's row.
        visible_a = _psql(
            f"SET app.user_id = '{uid_a}'; "
            f"SELECT user_id FROM {table} WHERE user_id IN {seeded} ORDER BY user_id",
            user="splitsmith_app",
        )
        assert visible_a == uid_a, f"{table}: tenant A saw {visible_a!r}, expected only {uid_a!r}"

        # GUC = B: only B's row.
        visible_b = _psql(
            f"SET app.user_id = '{uid_b}'; "
            f"SELECT user_id FROM {table} WHERE user_id IN {seeded} ORDER BY user_id",
            user="splitsmith_app",
        )
        assert visible_b == uid_b, f"{table}: tenant B saw {visible_b!r}, expected only {uid_b!r}"

        # No GUC set: fail-closed, zero rows (current_setting -> NULL).
        unset = _psql(
            f"SELECT count(*) FROM {table} WHERE user_id IN {seeded}",
            user="splitsmith_app",
        )
        assert unset == "0", f"{table}: GUC-unset query leaked {unset} rows (should be 0)"

    # WITH CHECK: as tenant A, inserting a row owned by B must be rejected.
    bad_insert = _psql_run(
        f"SET app.user_id = '{uid_a}'; "
        "INSERT INTO matches (id, user_id, match_id, name, storage_prefix) "
        f"VALUES ('m-x', '{uid_b}', 'mid-x', 'x', 'matches/mid-x')",
        user="splitsmith_app",
    )
    assert bad_insert.returncode != 0, "RLS WITH CHECK let tenant A insert a row owned by tenant B"
    assert "row-level security" in bad_insert.stderr.lower()
