"""Tests for the integration-suite skip gate in ``tests/conftest.py`` (#670).

The gate is the load-bearing half of that issue. Installing ffmpeg makes
the integration tests run *today*; the gate is what fails the build the
day they quietly stop running again -- a missing binary, a renamed
marker, a fixture that starts skipping. If the gate itself breaks, the
suite reverts to reporting green while proving nothing, which is exactly
the failure mode being fixed.

So it is tested by running pytest in a subprocess against the real
integration selection with ffmpeg removed from ``PATH``, and asserting
the exit code differs with and without the gate enabled. Asserting on
the hook functions alone would not prove the session actually fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest as gate

REPO_ROOT = Path(__file__).resolve().parents[1]

# One file, one integration test, no other dependencies -- keeps the
# nested pytest run to a couple of seconds.
_TARGET = "tests/test_proxy.py"


def _path_without_ffmpeg() -> str:
    """``PATH`` with every directory containing ffmpeg/ffprobe removed."""
    kept = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not (Path(entry) / "ffmpeg").exists() and not (Path(entry) / "ffprobe").exists()
    ]
    return os.pathsep.join(kept)


def _run_nested_pytest(
    *,
    require: bool,
    with_ffmpeg: bool = False,
    numprocesses: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    if not with_ffmpeg:
        env["PATH"] = _path_without_ffmpeg()
    if require:
        env["SPLITSMITH_REQUIRE_INTEGRATION"] = "1"
    else:
        env.pop("SPLITSMITH_REQUIRE_INTEGRATION", None)
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _TARGET, "-m", "integration"]
    # ``-n0`` and not "omit the flag": the repo's ``addopts`` carries
    # ``-n auto``, so a nested run inherits it unless it is overridden.
    argv += ["-n", "0" if numprocesses is None else str(numprocesses)]
    return subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True)


def test_skipped_integration_test_passes_the_build_without_the_gate() -> None:
    """The pre-#670 behaviour, pinned so the regression is visible.

    ffmpeg absent, the integration test skips, pytest exits 0. This is
    the green-but-proves-nothing state the gate exists to end.
    """
    result = _run_nested_pytest(require=False)
    assert "skipped" in result.stdout
    assert result.returncode == 0, result.stdout


def test_gate_fails_the_build_when_an_integration_test_skips() -> None:
    """Same run, same skip, ``SPLITSMITH_REQUIRE_INTEGRATION`` set -> failure."""
    result = _run_nested_pytest(require=True)
    assert result.returncode != 0, result.stdout
    assert "integration gate FAILED" in result.stdout
    assert "test_transcode_proxy_produces_smaller_valid_mp4" in result.stdout
    # The reason has to reach the log, or the failure is unactionable.
    assert "ffmpeg/ffprobe not available" in result.stdout


@pytest.mark.integration
def test_gate_passes_a_green_run_under_xdist() -> None:
    """The gate must reach the same verdict serially and under ``-n``.

    ``pytest-xdist``'s controller never collects -- ``DSession``
    short-circuits ``pytest_collection`` -- so a gate that counts
    selected items reads 0 in the only process that renders the summary
    and fails a run where every integration test passed. That is a
    permanently red CI, not a flake, so it is pinned here with a real
    parallel session rather than by asserting on the hooks.
    """
    result = _run_nested_pytest(require=True, with_ffmpeg=True, numprocesses=2)
    assert result.returncode == 0, result.stdout
    assert "integration gate FAILED" not in result.stdout
    # And it counts what actually ran, rather than reporting a green
    # gate over zero tests.
    assert "1 integration test(s) ran, 0 skipped" in result.stdout


# --- gate decision logic ----------------------------------------------------
#
# The subprocess tests cover the skip path end to end. These cover the
# arms that are awkward to provoke that way.


@pytest.fixture
def gate_state(monkeypatch: pytest.MonkeyPatch):
    """Reset the gate's module-level accumulators around each test."""
    monkeypatch.setattr(gate, "_skipped_integration", {})
    monkeypatch.setattr(gate, "_integration_selected", 0)
    monkeypatch.setattr(gate, "_reported_integration", set())
    return gate


def test_gate_is_inert_when_the_env_var_is_unset(gate_state, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_REQUIRE_INTEGRATION", raising=False)
    monkeypatch.setattr(gate, "_skipped_integration", {"tests/x.py::t": "Skipped: nope"})
    assert gate._integration_gate_failures() == []


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "OFF", " False "])
def test_falsey_env_values_leave_the_gate_off(
    gate_state, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("SPLITSMITH_REQUIRE_INTEGRATION", value)
    monkeypatch.setattr(gate, "_integration_selected", 0)
    assert gate._integration_gate_failures() == []


def test_gate_fails_when_no_integration_test_was_selected(
    gate_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker rename that empties the selection must not read as success."""
    monkeypatch.setenv("SPLITSMITH_REQUIRE_INTEGRATION", "1")
    monkeypatch.setattr(gate, "_integration_selected", 0)
    problems = gate._integration_gate_failures()
    assert len(problems) == 1
    assert "no test carrying the 'integration' marker was selected" in problems[0]


def test_reported_tests_satisfy_the_gate_when_collection_counted_nothing(
    gate_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The xdist shape: the judging process collected nothing but saw reports.

    ``_integration_selected`` is 0 in an xdist controller because it
    never collects. If the gate reads only that counter, a passing
    parallel run is reported as "no integration test was selected".
    """
    monkeypatch.setenv("SPLITSMITH_REQUIRE_INTEGRATION", "1")
    monkeypatch.setattr(gate, "_integration_selected", 0)
    monkeypatch.setattr(gate, "_reported_integration", {"tests/test_proxy.py::t"})
    assert gate._integration_gate_failures() == []


def test_gate_passes_when_every_selected_integration_test_ran(
    gate_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPLITSMITH_REQUIRE_INTEGRATION", "1")
    monkeypatch.setattr(gate, "_integration_selected", 6)
    assert gate._integration_gate_failures() == []


def test_xfail_is_not_reported_as_a_missing_integration_test(
    gate_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wasxfail`` rides on a skipped report; that is a deliberate
    expectation, not a test that silently failed to run."""
    monkeypatch.setenv("SPLITSMITH_REQUIRE_INTEGRATION", "1")
    monkeypatch.setattr(gate, "_integration_selected", 1)

    class _Report:
        nodeid = "tests/x.py::t"
        skipped = True
        keywords = {"integration": 1}
        longrepr = ("tests/x.py", 1, "Skipped: expected failure")
        wasxfail = "reason"

    gate.pytest_runtest_logreport(_Report())  # type: ignore[arg-type]
    assert gate._integration_gate_failures() == []


def test_non_integration_skips_are_ignored(gate_state, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_REQUIRE_INTEGRATION", "1")
    monkeypatch.setattr(gate, "_integration_selected", 1)

    class _Report:
        nodeid = "tests/x.py::t"
        skipped = True
        keywords = {"docker": 1}
        longrepr = ("tests/x.py", 1, "Skipped: needs docker")

    gate.pytest_runtest_logreport(_Report())  # type: ignore[arg-type]
    assert gate._integration_gate_failures() == []
