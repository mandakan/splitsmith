"""Tests for scripts/railway_up_verified.sh (issue #863).

The script wraps ``railway up --ci`` and, when the CLI exits non-zero
(log-stream flake), falls back to polling the deployment status instead of
failing the job outright. These tests drive it with a stubbed ``railway``
binary on PATH.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "railway_up_verified.sh"

DEPLOYMENT_ID = "37c37314-5565-48f8-8d14-11b9a04d9b54"
BUILD_LOGS_LINE = (
    "  Build Logs: https://railway.com/project/e77bded4-ddb2-430c-816f-156f2b6fe36a"
    f"/service/256c099f-511f-47c1-a290-7b3adb1b6d60?id={DEPLOYMENT_ID}&\n"
)

STUB_TEMPLATE = """#!/usr/bin/env bash
# Stubbed railway CLI driven by env vars / files set up by the test.
case "$1" in
  up)
    printf 'Indexing...\\nUploading...\\n'
    if [ "${STUB_UP_PRINTS_URL:-1}" = "1" ]; then
      printf '%s' "$STUB_BUILD_LOGS_LINE"
    fi
    printf 'CI mode enabled\\n'
    if [ "${STUB_UP_EXIT:-0}" != "0" ]; then
      printf 'Failed to stream build logs: Failed to retrieve build log\\n' >&2
    fi
    exit "${STUB_UP_EXIT:-0}"
    ;;
  deployment)
    # Pop the next status from the sequence file; repeat the last one forever.
    status=$(head -n1 "$STUB_STATUS_FILE")
    if [ "$(wc -l < "$STUB_STATUS_FILE")" -gt 1 ]; then
      tail -n +2 "$STUB_STATUS_FILE" > "$STUB_STATUS_FILE.tmp"
      mv "$STUB_STATUS_FILE.tmp" "$STUB_STATUS_FILE"
    fi
    printf '[{"id": "%s", "status": "%s"}]\\n' "$STUB_DEPLOYMENT_ID" "$status"
    ;;
  *)
    echo "unexpected railway subcommand: $*" >&2
    exit 64
    ;;
esac
"""


def run_script(
    tmp_path: Path,
    *,
    up_exit: int = 0,
    prints_url: bool = True,
    statuses: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "bin" / "railway"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(STUB_TEMPLATE)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    status_file = tmp_path / "statuses"
    status_file.write_text("\n".join(statuses or ["SUCCESS"]) + "\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub.parent}:{env['PATH']}",
            "STUB_UP_EXIT": str(up_exit),
            "STUB_UP_PRINTS_URL": "1" if prints_url else "0",
            "STUB_BUILD_LOGS_LINE": BUILD_LOGS_LINE,
            "STUB_STATUS_FILE": str(status_file),
            "STUB_DEPLOYMENT_ID": DEPLOYMENT_ID,
            "RAILWAY_VERIFY_POLL_INTERVAL": "0",
            "RAILWAY_VERIFY_TIMEOUT": "5",
        }
    )
    return subprocess.run(
        [str(SCRIPT), "serve"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_up_success_skips_poll(tmp_path: Path) -> None:
    result = run_script(tmp_path, up_exit=0)
    assert result.returncode == 0
    assert "polling deployment" not in result.stdout


def test_stream_flake_with_successful_deploy_goes_green(tmp_path: Path) -> None:
    result = run_script(tmp_path, up_exit=1, statuses=["BUILDING", "DEPLOYING", "SUCCESS"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert DEPLOYMENT_ID in result.stdout


def test_sleeping_counts_as_success(tmp_path: Path) -> None:
    # Scale-to-zero services report SLEEPING right after a successful deploy.
    result = run_script(tmp_path, up_exit=1, statuses=["SLEEPING"])
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_deploy_failure_stays_red(tmp_path: Path) -> None:
    result = run_script(tmp_path, up_exit=1, statuses=["BUILDING", "FAILED"])
    assert result.returncode == 1


def test_crashed_deploy_stays_red(tmp_path: Path) -> None:
    result = run_script(tmp_path, up_exit=1, statuses=["CRASHED"])
    assert result.returncode == 1


def test_no_deployment_id_propagates_up_exit(tmp_path: Path) -> None:
    # If railway up died before printing the Build Logs URL there is no
    # deployment to poll; the original failure must surface.
    result = run_script(tmp_path, up_exit=7, prints_url=False)
    assert result.returncode == 7


def test_poll_timeout_fails(tmp_path: Path) -> None:
    result = run_script(tmp_path, up_exit=1, statuses=["BUILDING"])
    assert result.returncode == 1
    assert "Timed out" in result.stdout + result.stderr


@pytest.mark.parametrize("missing_arg", [[]])
def test_requires_service_argument(tmp_path: Path, missing_arg: list[str]) -> None:
    result = subprocess.run(
        [str(SCRIPT), *missing_arg],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
