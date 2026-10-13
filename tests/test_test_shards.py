"""CI splits the suite across parallel jobs with ``SPLITSMITH_TEST_SHARD=i/N``
(``tests/conftest.py``). A split that loses a test, runs one twice, or
disagrees between xdist workers would turn a red suite green, so it is
pinned by running pytest in a subprocess, like the integration gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest

REPO_ROOT = Path(__file__).resolve().parents[1]
# A few hundred quick tests across several files.
_TARGETS = ["tests/test_events.py", "tests/test_sync_reconcile.py", "tests/test_media_check.py"]


def _collect(shard: str | None) -> list[str]:
    env = {**os.environ}
    env.pop("SPLITSMITH_REQUIRE_INTEGRATION", None)
    if shard is None:
        env.pop("SPLITSMITH_TEST_SHARD", None)
    else:
        env["SPLITSMITH_TEST_SHARD"] = shard
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", "--collect-only"]
    argv += _TARGETS
    out = subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    return [line for line in out.stdout.splitlines() if "::" in line]


def test_the_shards_cover_every_test_exactly_once() -> None:
    every = _collect(None)
    shards = [_collect(f"{i}/3") for i in (1, 2, 3)]
    assert sorted(t for s in shards for t in s) == sorted(every)
    assert all(shards), "a shard with no tests"


def test_a_test_lands_in_the_same_shard_in_every_process() -> None:
    """Python's own ``hash`` is salted per process; xdist workers would then
    disagree on the split, and xdist refuses a run whose workers collect
    different tests. The split must be a stable function of the test id."""
    nodeid = "tests/test_events.py::test_something[case-1]"
    first = [i for i in (1, 2, 3, 4) if conftest.in_shard(nodeid, i, 4)]
    again = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests import conftest; "
            f"print([i for i in (1, 2, 3, 4) if conftest.in_shard({nodeid!r}, i, 4)])",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert len(first) == 1
    assert again.stdout.strip() == str(first)


@pytest.mark.parametrize("bad", ["0/4", "5/4", "2", "a/b", "1/0"])
def test_a_malformed_shard_is_an_error_not_a_full_run(bad: str) -> None:
    with pytest.raises(pytest.UsageError):
        conftest.parse_shard(bad)
