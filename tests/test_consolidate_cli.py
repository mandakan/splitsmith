"""The CLI is the only thing allowed to touch the filesystem.

The rules were proven in isolation; what these pin is that apply
executes exactly what the plan said, refuses to run when the plan has a
safety violation, and preserves nanosecond mtimes -- an mtime that
changes re-uploads every trimmed mp4 in a synced match, because
sync/plan.py skips on (size, mtime_ns) with no content-hash fallback.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _cli():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_matches")
    finally:
        sys.path.pop(0)


def _lib():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _shooter(root: Path, *, audits: dict[str, str], token: str | None = None) -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    for name, body in audits.items():
        (root / "audit" / name).write_text(body)
    (root / "trimmed").mkdir()
    (root / "raw").mkdir()
    return root


def test_apply_copies_the_missing_documents(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "legacy", audits={f"stage{n}.json": json.dumps({"n": n}) for n in (1, 2)})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": json.dumps({"n": 1})})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=False)

    assert (destination / "audit" / "stage2.json").exists()
    assert json.loads((destination / "audit" / "stage2.json").read_text()) == {"n": 2}


def test_apply_sets_the_shooter_token_without_disturbing_the_rest(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "home", audits={"stage1.json": "{}"}, token="s97dcec94")
    destination = _shooter(tmp_path / "x9", audits={"stage1.json": "{}"})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=False)

    doc = json.loads((destination / "project.json").read_text())
    assert doc["shooter_token"] == "s97dcec94"
    assert doc["name"] == "M"
    assert doc["schema_version"] == 2


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}", "stage2.json": "{}"})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=True)

    assert not (destination / "audit" / "stage2.json").exists()


def test_reconcile_refuses_a_match_root_with_several_shooters(tmp_path: Path) -> None:
    """Passing a match root would silently reconcile against shooters[0].

    The destination of a reconcile is always one shooter. A match root
    holding three of them is a caller mistake, and picking the first is
    the worst possible response to it.
    """
    cli, lib = _cli(), _lib()
    match_root = tmp_path / "merged"
    (match_root / "shooters").mkdir(parents=True)
    (match_root / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    _shooter(match_root / "shooters" / "s_a", audits={"stage1.json": "{}"})
    _shooter(match_root / "shooters" / "s_b", audits={"stage1.json": "{}"})

    with pytest.raises(cli.AmbiguousShooterError) as excinfo:
        cli.single_shooter(lib.inventory_project(match_root))

    assert "s_a" in str(excinfo.value)


def test_single_shooter_accepts_a_shooter_directory(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    root = _shooter(tmp_path / "s_a", audits={"stage1.json": "{}"}, token="s97dcec94")

    shooter = cli.single_shooter(lib.inventory_project(root))

    assert shooter.shooter_token == "s97dcec94"


def test_apply_refuses_a_plan_carrying_a_safety_violation(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    plan = lib.ReconcilePlan(
        actions=[],
        violations=[lib.SafetyViolation(source=tmp_path, document="stage3.json", reason="unscheduled")],
        deletable=False,
    )

    with pytest.raises(cli.UnsafePlanError):
        cli.apply_reconcile(plan, dry_run=False)


def test_copied_media_keeps_its_nanosecond_mtime(tmp_path: Path) -> None:
    """A changed mtime re-uploads the file to the hosted instance."""
    import os

    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "x9", audits={"stage1.json": "{}"})
    clip = source / "trimmed" / "stage1_trimmed.mp4"
    clip.write_bytes(b"0" * 64)
    os.utime(clip, ns=(1700000000123456789, 1700000000123456789))
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=False)

    copied = destination / "trimmed" / "stage1_trimmed.mp4"
    assert copied.stat().st_mtime_ns == clip.stat().st_mtime_ns


def test_verify_reports_a_replaced_document_without_blocking(tmp_path: Path) -> None:
    """A before/after pair differing only by a replaced document must not block.

    Deviation from the brief: ``verify_documents_replaced`` did not exist
    when it was written, so it is not part of the blocking decision --
    the reconciliation rule deliberately lets the destination win where
    both sides hold a document, so a hash mismatch alone must never fail
    the migration's gate. It must still show up in the report, in a
    clearly separate, non-blocking section.
    """
    import argparse
    import uuid

    cli, lib = _cli(), _lib()

    # cmd_verify pairs projects by root.name, so before/after must share it
    # even though the shooters themselves live under distinct tmp_path trees.
    before_root = tmp_path / "before" / "match" / "s_a"
    _shooter(before_root, audits={"stage1.json": json.dumps({"v": 1})}, token="t1")
    before = lib.ProjectInventory(root=tmp_path / "before" / "match", kind="legacy", shooters=[])
    before.shooters.append(lib.inventory_project(before_root).shooters[0])

    after_root = tmp_path / "after" / "match" / "s_a"
    _shooter(after_root, audits={"stage1.json": json.dumps({"v": 2})}, token="t1")
    after = lib.ProjectInventory(root=tmp_path / "after" / "match", kind="legacy", shooters=[])
    after.shooters.append(lib.inventory_project(after_root).shooters[0])

    label = f"pytest-{uuid.uuid4().hex}"
    before_label, after_label = f"{label}-before", f"{label}-after"

    report_dir = cli.REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    before_path = report_dir / f"{before_label}.json"
    after_path = report_dir / f"{after_label}.json"
    verify_path = report_dir / f"verify-{before_label}-vs-{after_label}.json"
    before_path.write_text(json.dumps([json.loads(before.model_dump_json())], indent=2))
    after_path.write_text(json.dumps([json.loads(after.model_dump_json())], indent=2))

    try:
        args = argparse.Namespace(before=before_label, after=after_label)
        try:
            cli.cmd_verify(args)
        except SystemExit as exc:
            pytest.fail(f"verify must not block on a replaced-only document, exited with {exc.code}")

        report = json.loads(verify_path.read_text())
        assert report["blocking"] == []
        assert len(report["replaced"]) == 1
        assert "stage1.json" in report["replaced"][0]["detail"]
    finally:
        for path in (before_path, after_path, verify_path):
            path.unlink(missing_ok=True)
