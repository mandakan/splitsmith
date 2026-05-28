"""Pin the lazy-import contract that keeps local desktop free of
hosted-mode dependencies.

Local-mode entrypoints (``splitsmith ui``, ``splitsmith cli`` --
any path that doesn't set ``SPLITSMITH_MODE=hosted``) must never
pull in:

- ``splitsmith.db`` -- the SQLAlchemy stores
- ``splitsmith.queue`` -- the Procrastinate App + queue helpers
- ``procrastinate`` / ``psycopg`` -- the queue's transitive deps
- ``sqlalchemy`` / ``asyncpg`` / ``aiosqlite`` / ``alembic`` -- the
  DB layer's transitive deps
- ``boto3`` -- the ``S3Storage`` backend
- ``ulid`` -- ``python-ulid`` only used by Postgres-backed stores

If any of these slip into the module-level import chain, a slim
local-mode wheel without the ``[hosted]`` extra would crash on
``import splitsmith.cli`` before the user types anything. This
test catches that drift before it ships.

The check runs in a subprocess so it sees a clean ``sys.modules``;
the in-process test runner already has half the codebase imported.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

# Modules that MUST NOT appear in sys.modules after importing a
# local-mode entrypoint. We match by exact name OR ``name.``
# prefix (so ``sqlalchemy.orm`` is caught when checking ``sqlalchemy``).
FORBIDDEN_PREFIXES = (
    "splitsmith.db",
    "splitsmith.queue",
    "procrastinate",
    "psycopg",
    "sqlalchemy",
    "asyncpg",
    "aiosqlite",
    "alembic",
    "boto3",
    "botocore",
    "ulid",
)


def _run_import_probe(entrypoint_import: str) -> set[str]:
    """Spawn a child python process, run ``entrypoint_import``, return
    the set of forbidden modules that ended up in sys.modules.

    The child writes the offending module names to stdout, one per
    line. An empty stdout means the import was clean.
    """
    script = textwrap.dedent(f"""
        import sys, os
        # Make absolutely sure the child does not think it's in hosted
        # mode (the parent env may have leaked an override).
        os.environ.pop("SPLITSMITH_MODE", None)
        {entrypoint_import}
        forbidden_prefixes = {FORBIDDEN_PREFIXES!r}
        bad = sorted(
            name for name in sys.modules
            if any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)
        )
        for name in bad:
            print(name)
        """)
    env = {**os.environ}
    # Strip mode override; the parent test runner may have set it for
    # other tests' fixtures.
    env.pop("SPLITSMITH_MODE", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return {line for line in result.stdout.splitlines() if line.strip()}


def test_importing_splitsmith_cli_does_not_pull_in_hosted_deps() -> None:
    """``splitsmith.cli`` is the Typer app exposed by the ``splitsmith``
    console-script. A local user typing ``splitsmith ui`` enters
    through here; the import must stay free of hosted-only deps."""
    bad = _run_import_probe("import splitsmith.cli")
    assert not bad, (
        "Local-mode entrypoint pulled in hosted-only modules. "
        f"Found: {sorted(bad)}. Check ``splitsmith.cli`` and any "
        "module it imports for accidental top-level imports of "
        "``splitsmith.db`` / ``splitsmith.queue`` / SQLAlchemy / etc. "
        "Hosted-only code must live behind ``_hosted_mode_active()``."
    )


def test_importing_splitsmith_ui_server_does_not_pull_in_hosted_deps() -> None:
    """``splitsmith.ui.server`` is what ``cli.ui`` lazy-imports to
    boot the local-mode FastAPI app. Its module-level imports must
    stay clean even though ``_apply_hosted_mode_wiring`` (gated by
    ``_hosted_mode_active``) does pull the hosted layer in."""
    bad = _run_import_probe("import splitsmith.ui.server")
    assert not bad, (
        "Hosted-only modules leaked at server module load time. "
        f"Found: {sorted(bad)}. The hosted-mode imports inside "
        "``_apply_hosted_mode_wiring`` must stay inside the function "
        "body, not move to module scope."
    )


def test_queue_module_is_not_eagerly_imported() -> None:
    """Direct guard on ``splitsmith.queue`` specifically -- the
    Procrastinate App is the thing this PR adds, so the regression
    we care most about is some future refactor accidentally
    importing it from the local-mode path."""
    bad = _run_import_probe("import splitsmith")
    queue_leaked = {m for m in bad if m == "splitsmith.queue" or m.startswith("splitsmith.queue.")}
    assert not queue_leaked, (
        "``splitsmith.queue`` leaked into a plain ``import splitsmith``. "
        "It must only be imported behind ``_hosted_mode_active()`` "
        "(same lazy-import pattern as ``splitsmith.db``)."
    )
