"""The Windows probe's import sweep and run summary (scripts/ci/windows_probe.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "windows_probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("windows_probe", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["windows_probe"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("windows_probe", None)


def _junit(path: Path, cases: str) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )


def test_sweep_blames_the_module_that_raised_not_its_importer(probe, tmp_path):
    pkg = tmp_path / "fakepkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ok.py").write_text("X = 1\n", encoding="utf-8")
    (pkg / "locks.py").write_text("import os\nimport no_such_module_xyz\n", encoding="utf-8")
    (pkg / "sub" / "user.py").write_text("from .. import locks\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        result = probe.sweep(pkg, "fakepkg")
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in sys.modules if m == "fakepkg" or m.startswith("fakepkg.")]:
            del sys.modules[name]

    assert result.imported == 3  # fakepkg, fakepkg.ok, fakepkg.sub
    failed = {f.module: f for f in result.failures}
    assert set(failed) == {"fakepkg.locks", "fakepkg.sub.user"}
    for failure in failed.values():
        assert failure.error == "ModuleNotFoundError: No module named 'no_such_module_xyz'"
        assert failure.origin == "fakepkg/locks.py:2"


def test_summary_groups_failures_and_shows_collection_errors(probe, tmp_path):
    junit = tmp_path / "junit"
    junit.mkdir()
    busy = '<failure message="PermissionError: [WinError 32] in use">tb</failure>'
    _junit(
        junit / "shard-1.xml",
        f'<testcase classname="tests.test_a" name="test_one">{busy}</testcase>'
        f'<testcase classname="tests.test_a" name="test_two">{busy}</testcase>'
        '<testcase classname="tests.test_a" name="test_three"/>',
    )
    _junit(
        junit / "shard-2.xml",
        f'<testcase classname="tests.test_b" name="test_four">{busy}</testcase>'
        '<testcase classname="tests.test_b" name="test_five">'
        '<skipped message="ffmpeg not on PATH"/></testcase>'
        '<testcase classname="" name="tests.test_c">'
        '<error message="collection failure">'
        "tests/test_c.py:3: in &lt;module&gt;\n    import fcntl\n"
        "E   ModuleNotFoundError: No module named 'fcntl'"
        "</error></testcase>",
    )

    text = probe.summarize(junit_dir=junit, imports=None, logs_dir=None)

    assert "| 1 | 3 | 1 | 1 | 2 |" in text  # passed, failed, errors, skipped, shards
    assert "| 3 | PermissionError: [WinError 32] in use | tests.test_a::test_one," in text
    assert "ModuleNotFoundError: No module named 'fcntl'" in text
    assert "ffmpeg not on PATH" in text


def test_summary_shows_the_log_tail_when_a_shard_wrote_no_report(probe, tmp_path):
    junit = tmp_path / "junit"
    junit.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "shard-3.log").write_text(
        "noise\n"
        "ImportError while loading conftest 'tests/conftest.py'.\n"
        "E   ModuleNotFoundError: No module named 'fcntl'\n",
        encoding="utf-8",
    )

    text = probe.summarize(junit_dir=junit, imports=None, logs_dir=logs)

    assert "shard-3" in text
    assert "ImportError while loading conftest" in text


def test_summary_groups_import_failures_by_origin(probe, tmp_path):
    imports = tmp_path / "imports.json"
    imports.write_text(
        '{"imported": 10, "failures": ['
        '{"module": "splitsmith.trim", "error": "ModuleNotFoundError: x",'
        ' "origin": "splitsmith/trim.py:29"},'
        '{"module": "splitsmith.ui.server", "error": "ModuleNotFoundError: x",'
        ' "origin": "splitsmith/trim.py:29"}'
        "]}",
        encoding="utf-8",
    )

    text = probe.summarize(junit_dir=None, imports=imports, logs_dir=None)

    assert "10 imported, 2 failed" in text
    assert "| `splitsmith/trim.py:29` | ModuleNotFoundError: x | 2 |" in text
