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


def _write_inventory(report_dir: Path, label: str, projects: list) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{label}.json").write_text(
        json.dumps([json.loads(p.model_dump_json()) for p in projects], indent=2) + "\n"
    )


def _verify(cli, report_dir: Path) -> Path:
    """Run ``verify`` over the ``before``/``after`` labels, return the report path."""
    import argparse

    cli.cmd_verify(argparse.Namespace(before="before", after="after", report_dir=report_dir))
    return report_dir / "verify-before-vs-after.json"


def _merged_match(root: Path, *, slug: str, token: str, audits: dict[str, str]) -> Path:
    (root / "shooters").mkdir(parents=True)
    (root / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    _shooter(root / "shooters" / slug, audits=audits, token=token)
    return root


def test_verify_reports_a_replaced_document_without_blocking(tmp_path: Path) -> None:
    """A before/after pair differing only by a replaced document must not block.

    Deviation from the brief: ``verify_documents_replaced`` did not exist
    when it was written, so it is not part of the blocking decision --
    the reconciliation rule deliberately lets the destination win where
    both sides hold a document, so a hash mismatch alone must never fail
    the migration's gate. It must still show up in the report, in a
    clearly separate, non-blocking section.
    """
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    before_root = tmp_path / "before" / "match" / "s_a"
    _shooter(before_root, audits={"stage1.json": json.dumps({"v": 1})}, token="t1")
    before = lib.ProjectInventory(root=tmp_path / "before" / "match", kind="legacy", shooters=[])
    before.shooters.append(lib.inventory_project(before_root).shooters[0])

    after_root = tmp_path / "after" / "match" / "s_a"
    _shooter(after_root, audits={"stage1.json": json.dumps({"v": 2})}, token="t1")
    after = lib.ProjectInventory(root=tmp_path / "after" / "match", kind="legacy", shooters=[])
    after.shooters.append(lib.inventory_project(after_root).shooters[0])

    _write_inventory(report_dir, "before", [before])
    _write_inventory(report_dir, "after", [after])

    try:
        verify_path = _verify(cli, report_dir)
    except SystemExit as exc:
        pytest.fail(f"verify must not block on a replaced-only document, exited with {exc.code}")

    report = json.loads(verify_path.read_text())
    assert report["blocking"] == []
    assert len(report["replaced"]) == 1
    assert "stage1.json" in report["replaced"][0]["detail"]


def test_verify_blocks_when_a_before_project_is_absent_from_the_after_inventory(tmp_path: Path) -> None:
    """The reviewer's scenario: a project with content, gone, reported clean.

    Pairing by directory basename let every project the migration
    actually reshapes fall out of the comparison, so a ``before`` holding
    a project with an audit doc, media bytes and an unlinked raw file
    could be verified against an ``after`` containing none of it and
    still print "0 blocking finding(s)" and exit 0. Task 17 deletes the
    originals on the strength of that report.
    """
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    source = _shooter(tmp_path / "before" / "bofors-bombardment-2026", audits={"stage1.json": "{}"})
    (source / "trimmed" / "stage1_trimmed.mp4").write_bytes(b"0" * 999)
    (source / "raw" / "IMG_9001.MOV").write_bytes(b"0" * 32)
    before = lib.inventory_project(source)

    survivor = _shooter(tmp_path / "after" / "unrelated-2026", audits={"stage1.json": "{}"}, token="t9")
    after = lib.inventory_project(survivor)

    _write_inventory(report_dir, "before", [before])
    _write_inventory(report_dir, "after", [after])

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    checks = {finding["check"] for finding in report["blocking"]}
    assert "project_paired" in checks
    assert any("bofors-bombardment-2026" in finding["subject"] for finding in report["blocking"])


def test_verify_pairs_a_renamed_relocated_project_by_shooter_token(tmp_path: Path) -> None:
    """Same data, new name, new home, inside a merged match: still one project."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    legacy = _shooter(
        tmp_path / "home" / "blacksmith-2026",
        audits={"stage1.json": "{}", "stage2.json": "{}"},
        token="s97dcec94",
    )
    merged = _merged_match(
        tmp_path / "x9" / "blacksmith-handgun-open-2026",
        slug="s_ce10fa76",
        token="s97dcec94",
        audits={"stage1.json": "{}", "stage2.json": "{}"},
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(legacy)])
    _write_inventory(report_dir, "after", [lib.inventory_project(merged)])

    verify_path = _verify(cli, report_dir)

    assert json.loads(verify_path.read_text())["blocking"] == []


def test_verify_compares_a_renamed_project_rather_than_skipping_it(tmp_path: Path) -> None:
    """The pairing has to be worth something: a doc lost in the move is named."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    legacy = _shooter(
        tmp_path / "home" / "blacksmith-2026",
        audits={"stage1.json": "{}", "stage2.json": "{}"},
        token="s97dcec94",
    )
    merged = _merged_match(
        tmp_path / "x9" / "blacksmith-handgun-open-2026",
        slug="s_ce10fa76",
        token="s97dcec94",
        audits={"stage1.json": "{}"},
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(legacy)])
    _write_inventory(report_dir, "after", [lib.inventory_project(merged)])

    with pytest.raises(SystemExit):
        _verify(cli, report_dir)

    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(
        finding["check"] == "documents_survived" and "stage2.json" in finding["detail"]
        for finding in report["blocking"]
    )


def test_reconcile_persists_its_deletable_verdict_and_verify_reads_it(tmp_path: Path) -> None:
    """ "Never delete a source the destination lacks content from" must reach disk.

    The verdict used to exist only as a line on stdout during the
    reconcile, so no artifact a human opens before deleting encoded it.
    """
    import argparse

    cli = _cli()
    report_dir = tmp_path / "reports"

    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}"})
    (source / "raw" / "IMG_9001.MOV").write_bytes(b"0" * 32)
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})

    cli.cmd_reconcile(
        argparse.Namespace(source=source, destination=destination, apply=False, report_dir=report_dir)
    )

    records = json.loads((report_dir / cli.RECONCILE_LOG_NAME).read_text())
    assert len(records) == 1
    assert records[0]["deletable"] is False
    assert records[0]["violations"][0]["document"] == "IMG_9001.MOV"

    _write_inventory(report_dir, "before", [])
    _write_inventory(report_dir, "after", [])

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(finding["check"] == "reconcile_deletable" for finding in report["blocking"])
    assert any("IMG_9001.MOV" in finding["detail"] for finding in report["blocking"])


def test_reconcile_appends_so_a_whole_phase_accumulates(tmp_path: Path) -> None:
    import argparse

    cli = _cli()
    report_dir = tmp_path / "reports"

    for index in (1, 2):
        source = _shooter(tmp_path / f"legacy{index}", audits={"stage1.json": "{}"})
        destination = _shooter(tmp_path / f"merged{index}", audits={"stage1.json": "{}"})
        cli.cmd_reconcile(
            argparse.Namespace(source=source, destination=destination, apply=False, report_dir=report_dir)
        )

    records = json.loads((report_dir / cli.RECONCILE_LOG_NAME).read_text())
    assert [Path(record["source"]).name for record in records] == ["legacy1", "legacy2"]
    assert all(record["deletable"] is True for record in records)
    assert all(record["applied"] is False for record in records)


def test_a_plan_run_does_not_claim_the_source_is_already_deletable(tmp_path, capsys) -> None:
    """Without --apply nothing has been copied, so nothing is deletable yet."""
    import argparse

    cli = _cli()
    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}", "stage2.json": "{}"})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})

    cli.cmd_reconcile(
        argparse.Namespace(
            source=source, destination=destination, apply=False, report_dir=tmp_path / "reports"
        )
    )

    out = capsys.readouterr().out
    assert "deletable_after_apply=True" in out
    assert "deletable=True" not in out


def test_an_applied_reconcile_states_the_verdict_plainly(tmp_path, capsys) -> None:
    import argparse

    cli = _cli()
    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}", "stage2.json": "{}"})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})

    cli.cmd_reconcile(
        argparse.Namespace(
            source=source, destination=destination, apply=True, report_dir=tmp_path / "reports"
        )
    )

    out = capsys.readouterr().out
    assert "deletable=True" in out
    assert (destination / "audit" / "stage2.json").exists()
    records = json.loads((tmp_path / "reports" / cli.RECONCILE_LOG_NAME).read_text())
    assert records[0]["applied"] is True
