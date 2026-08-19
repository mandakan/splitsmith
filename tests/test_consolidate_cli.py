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


def _shooter(
    root: Path, *, audits: dict[str, str], token: str | None = None, scoreboard_match_id: str | None = None
) -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    if scoreboard_match_id is not None:
        project["scoreboard_match_id"] = scoreboard_match_id
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


def _write_rename_map(report_dir: Path, mapping: dict[str, str]) -> Path:
    """Declare which after-project each before-project is expected to land in."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "rename-map.json"
    path.write_text(json.dumps(mapping, indent=2) + "\n")
    return path


def _write_reconcile_log(report_dir: Path, records: list[dict]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "reconcile-log.json"
    path.write_text(json.dumps(records, indent=2) + "\n")
    return path


def _clean_log(report_dir: Path, tmp_path: Path) -> Path:
    """One applied, deletable record -- the shape that satisfies the gate."""
    return _write_reconcile_log(
        report_dir,
        [
            {
                "source": str(tmp_path / "legacy"),
                "destination": str(tmp_path / "merged"),
                "applied": True,
                "action_count": 0,
                "violations": [],
                "deletable": True,
            }
        ],
    )


def _covering_log(report_dir: Path, sources: list[Path]) -> Path:
    """One applied, deletable record per source -- satisfies per-project coverage."""
    return _write_reconcile_log(
        report_dir,
        [
            {
                "source": str(source),
                "destination": str(source),
                "applied": True,
                "action_count": 0,
                "violations": [],
                "deletable": True,
            }
            for source in sources
        ],
    )


def _verify(cli, report_dir: Path, *, rename_map: Path, reconcile_log: Path | None = None) -> Path:
    """Run ``verify`` over the ``before``/``after`` labels, return the report path."""
    import argparse

    cli.cmd_verify(
        argparse.Namespace(
            before="before",
            after="after",
            report_dir=report_dir,
            rename_map=rename_map,
            reconcile_log=reconcile_log,
        )
    )
    return report_dir / "verify-before-vs-after.json"


def _merged_match(
    root: Path, *, slug: str, token: str, audits: dict[str, str], match_id: str = "m-1"
) -> Path:
    (root / "shooters").mkdir(parents=True)
    (root / "match.json").write_text(json.dumps({"match_id": match_id, "name": "M"}))
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
    rename_map = _write_rename_map(report_dir, {"match": "match"})
    log = _clean_log(report_dir, tmp_path)

    try:
        verify_path = _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)
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
    rename_map = _write_rename_map(report_dir, {"bofors-bombardment-2026": "bofors-bombardment-2026"})
    log = _clean_log(report_dir, tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    checks = {finding["check"] for finding in report["blocking"]}
    assert "project_destination_present" in checks
    assert any("bofors-bombardment-2026" in finding["subject"] for finding in report["blocking"])


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
        argparse.Namespace(
            source=source, destination=destination, apply=False, report_dir=report_dir, reconcile_log=None
        )
    )

    records = json.loads((report_dir / cli.RECONCILE_LOG_NAME).read_text())
    assert len(records) == 1
    assert records[0]["deletable"] is False
    assert records[0]["violations"][0]["document"] == "IMG_9001.MOV"

    _write_inventory(report_dir, "before", [])
    _write_inventory(report_dir, "after", [])
    rename_map = _write_rename_map(report_dir, {})

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map)

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


# --- Project identity is declared, never inferred ----------------------------


def test_verify_blocks_a_lost_project_that_merely_shares_a_shooter_token(tmp_path: Path) -> None:
    """The demonstrated failure: a token identifies a shooter, not a match.

    Legacy ``bofors-bombardment-2026`` has no bofors match in the after
    inventory at all. Because Mathias (token ``s97dcec94``) also shoots
    blacksmith, token-pairing handed it ``blacksmith-handgun-open-2026``
    as its counterpart; audit docs are generically named ``stageN.json``
    so every name appeared present, and verify reported "0 blocking
    finding(s)" and exited 0 over a project whose data is gone. Four
    tokens span ten matches, so this is not an edge case.
    """
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    lost = _shooter(
        tmp_path / "before" / "bofors-bombardment-2026",
        audits={"stage1.json": json.dumps({"stage": 1, "match": "bofors"})},
        token="s97dcec94",
    )
    survivor = _merged_match(
        tmp_path / "after" / "blacksmith-handgun-open-2026",
        slug="s_ce10fa76",
        token="s97dcec94",
        audits={"stage1.json": json.dumps({"stage": 1, "match": "blacksmith"})},
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(lost)])
    _write_inventory(report_dir, "after", [lib.inventory_project(survivor)])
    rename_map = _write_rename_map(
        report_dir,
        {
            "bofors-bombardment-2026": "bofors-bombardment-2026",
            "blacksmith-2026": "blacksmith-handgun-open-2026",
        },
    )
    log = _clean_log(report_dir, tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    checks = {finding["check"] for finding in report["blocking"]}
    assert "project_destination_present" in checks
    assert any("bofors-bombardment-2026" in finding["detail"] for finding in report["blocking"])


def test_verify_blocks_a_before_project_absent_from_the_rename_map(tmp_path: Path) -> None:
    """An unmapped project is never a silent skip: the map has to name it."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    stranger = _shooter(tmp_path / "before" / "tallmilan-2026-janne", audits={"stage1.json": "{}"})
    survivor = _merged_match(
        tmp_path / "after" / "tallmilan-2026", slug="s_a", token="t1", audits={"stage1.json": "{}"}
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(stranger)])
    _write_inventory(report_dir, "after", [lib.inventory_project(survivor)])
    rename_map = _write_rename_map(report_dir, {"tallmilan-2026": "tallmilan-2026"})
    log = _clean_log(report_dir, tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(finding["check"] == "project_mapped" for finding in report["blocking"])
    assert any("tallmilan-2026-janne" in finding["subject"] for finding in report["blocking"])


def test_verify_resolves_a_renamed_project_through_the_map(tmp_path: Path) -> None:
    """A legacy directory consolidated into a differently-named merged match."""
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
    rename_map = _write_rename_map(report_dir, {"blacksmith-2026": "blacksmith-handgun-open-2026"})
    log = _covering_log(report_dir, [tmp_path / "home" / "blacksmith-2026"])

    verify_path = _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert json.loads(verify_path.read_text())["blocking"] == []


def test_verify_compares_the_shooters_of_a_project_resolved_through_the_map(tmp_path: Path) -> None:
    """The resolution has to be worth something: a doc lost in the move is named."""
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
    rename_map = _write_rename_map(report_dir, {"blacksmith-2026": "blacksmith-handgun-open-2026"})
    log = _clean_log(report_dir, tmp_path)

    with pytest.raises(SystemExit):
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(
        finding["check"] == "documents_survived" and "stage2.json" in finding["detail"]
        for finding in report["blocking"]
    )


def test_three_before_projects_may_declare_the_same_destination(tmp_path: Path) -> None:
    """Three legacy blacksmith projects become three shooters in one match."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    before = [
        lib.inventory_project(_shooter(tmp_path / "home" / name, audits={"stage1.json": "{}"}, token=token))
        for name, token in (
            ("blacksmith-2026", "s97dcec94"),
            ("blacksmith-handgun-2026-anton", "s36ed6e4e"),
            ("blacksmith-handgun-2026-martin", "s0fe3d797"),
        )
    ]
    merged = tmp_path / "x9" / "blacksmith-handgun-open-2026"
    (merged / "shooters").mkdir(parents=True)
    (merged / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    for slug, token in (
        ("s_ce10fa76", "s97dcec94"),
        ("s_46039db3", "s36ed6e4e"),
        ("s_b3d21334", "s0fe3d797"),
    ):
        _shooter(merged / "shooters" / slug, audits={"stage1.json": "{}"}, token=token)

    _write_inventory(report_dir, "before", before)
    _write_inventory(report_dir, "after", [lib.inventory_project(merged)])
    rename_map = _write_rename_map(
        report_dir,
        {
            "blacksmith-2026": "blacksmith-handgun-open-2026",
            "blacksmith-handgun-2026-anton": "blacksmith-handgun-open-2026",
            "blacksmith-handgun-2026-martin": "blacksmith-handgun-open-2026",
        },
    )
    log = _covering_log(
        report_dir,
        [
            tmp_path / "home" / "blacksmith-2026",
            tmp_path / "home" / "blacksmith-handgun-2026-anton",
            tmp_path / "home" / "blacksmith-handgun-2026-martin",
        ],
    )

    verify_path = _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert json.loads(verify_path.read_text())["blocking"] == []


# --- The reconcile log is a gate, and its absence is not a pass --------------


def test_verify_blocks_when_the_reconcile_log_is_missing(tmp_path: Path) -> None:
    """Deleting the log used to disable the deletability check entirely."""
    cli = _cli()
    report_dir = tmp_path / "reports"
    _write_inventory(report_dir, "before", [])
    _write_inventory(report_dir, "after", [])
    rename_map = _write_rename_map(report_dir, {})

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=report_dir / "absent.json")

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(finding["check"] == "reconcile_log_present" for finding in report["blocking"])


def test_verify_blocks_when_the_reconcile_log_is_empty(tmp_path: Path) -> None:
    """ "No records" must never read as "all clear"."""
    cli = _cli()
    report_dir = tmp_path / "reports"
    _write_inventory(report_dir, "before", [])
    _write_inventory(report_dir, "after", [])
    rename_map = _write_rename_map(report_dir, {})
    log = _write_reconcile_log(report_dir, [])

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(finding["check"] == "reconcile_log_present" for finding in report["blocking"])


def test_verify_blocks_a_reconcile_that_was_planned_but_never_applied(tmp_path: Path) -> None:
    """A clean plan copies nothing until --apply runs; the gate must know."""
    import argparse

    cli = _cli()
    report_dir = tmp_path / "reports"
    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}", "stage2.json": "{}"})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})

    cli.cmd_reconcile(
        argparse.Namespace(
            source=source, destination=destination, apply=False, report_dir=report_dir, reconcile_log=None
        )
    )

    _write_inventory(report_dir, "before", [])
    _write_inventory(report_dir, "after", [])
    rename_map = _write_rename_map(report_dir, {})

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(finding["check"] == "reconcile_applied" for finding in report["blocking"])
    assert any("legacy" in finding["subject"] for finding in report["blocking"])


def test_a_later_reconcile_of_the_same_pair_supersedes_the_earlier_one(tmp_path: Path) -> None:
    """Fixing a violation and re-running must clear the gate, not stack up.

    The log used to accumulate, so the stale ``deletable: false`` record
    blocked forever and the only escape was deleting the log -- which
    silently disabled the check.
    """
    import argparse

    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"
    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}"}, token="s97dcec94")
    (source / "raw" / "IMG_9001.MOV").write_bytes(b"0" * 32)
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"}, token="s97dcec94")

    def reconcile(apply: bool) -> None:
        cli.cmd_reconcile(
            argparse.Namespace(
                source=source,
                destination=destination,
                apply=apply,
                report_dir=report_dir,
                reconcile_log=None,
            )
        )

    reconcile(apply=False)
    records = json.loads((report_dir / cli.RECONCILE_LOG_NAME).read_text())
    assert len(records) == 1 and records[0]["deletable"] is False

    # Fix the violation the way the migration does: the raw file reaches
    # the destination, so the source no longer holds anything unique.
    (destination / "raw" / "IMG_9001.MOV").write_bytes(b"0" * 32)
    reconcile(apply=True)

    records = json.loads((report_dir / cli.RECONCILE_LOG_NAME).read_text())
    assert len(records) == 1
    assert records[0]["deletable"] is True
    assert records[0]["applied"] is True

    _write_inventory(report_dir, "before", [lib.inventory_project(source)])
    _write_inventory(report_dir, "after", [lib.inventory_project(destination)])
    rename_map = _write_rename_map(report_dir, {"legacy": "merged"})

    verify_path = _verify(cli, report_dir, rename_map=rename_map)

    assert json.loads(verify_path.read_text())["blocking"] == []


# --- Coverage round: a clean report has to prove coverage, not just consistency --


def test_verify_blocks_a_rename_map_entry_with_no_before_project(tmp_path: Path) -> None:
    """Omitting a --root at inventory time makes a project invisible, not absent.

    ``resolve_projects`` used to walk only the before-projects it was
    handed, so a map entry for a project the inventory never saw --
    because ``--root ~/Splitsmith`` was left off phase 0, say -- was
    silently nothing. Task 17 would then delete a project verify never
    even looked at.
    """
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    real = _shooter(tmp_path / "before" / "real-2026", audits={"stage1.json": "{}"}, token="t1")
    after_real = _merged_match(
        tmp_path / "after" / "real-2026", slug="s_a", token="t1", audits={"stage1.json": "{}"}
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(real)])
    _write_inventory(report_dir, "after", [lib.inventory_project(after_real)])
    rename_map = _write_rename_map(report_dir, {"real-2026": "real-2026", "oden-cup-2026": "oden-cup-2026"})
    log = _covering_log(report_dir, [tmp_path / "before" / "real-2026"])

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(
        f["check"] == "rename_map_unmatched" and "oden-cup-2026" in f["subject"] for f in report["blocking"]
    )


def test_inventory_refuses_to_overwrite_an_existing_label_without_force(tmp_path: Path) -> None:
    import argparse

    cli = _cli()
    report_dir = tmp_path / "reports"
    root = tmp_path / "matches"
    _shooter(root / "solo-2026", audits={"stage1.json": "{}"})

    cli.cmd_inventory(argparse.Namespace(label="phase0", root=[root], report_dir=report_dir, force=False))

    with pytest.raises(SystemExit) as excinfo:
        cli.cmd_inventory(argparse.Namespace(label="phase0", root=[root], report_dir=report_dir, force=False))
    assert excinfo.value.code == 1

    cli.cmd_inventory(argparse.Namespace(label="phase0", root=[root], report_dir=report_dir, force=True))


def test_verify_blocks_an_empty_before_inventory(tmp_path: Path) -> None:
    """A truncated ``before.json`` decodes to ``[]`` exactly like a real empty corpus would."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    survivor = _merged_match(
        tmp_path / "after" / "real-2026", slug="s_a", token="t1", audits={"stage1.json": "{}"}
    )
    _write_inventory(report_dir, "before", [])
    _write_inventory(report_dir, "after", [lib.inventory_project(survivor)])
    rename_map = _write_rename_map(report_dir, {})
    log = _clean_log(report_dir, tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(f["check"] == "before_inventory_nonempty" for f in report["blocking"])


def test_verify_blocks_when_no_reconcile_record_covers_a_relocated_project(tmp_path: Path) -> None:
    """One unrelated applied+deletable record must not satisfy the gate for every project."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    legacy = _shooter(tmp_path / "home" / "blacksmith-2026", audits={"stage1.json": "{}"}, token="t1")
    merged = _merged_match(
        tmp_path / "x9" / "blacksmith-handgun-open-2026",
        slug="s_a",
        token="t1",
        audits={"stage1.json": "{}"},
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(legacy)])
    _write_inventory(report_dir, "after", [lib.inventory_project(merged)])
    rename_map = _write_rename_map(report_dir, {"blacksmith-2026": "blacksmith-handgun-open-2026"})
    log = _covering_log(report_dir, [tmp_path / "unrelated"])

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(
        f["check"] == "reconcile_covers_project" and "blacksmith-2026" in f["subject"]
        for f in report["blocking"]
    )


def test_verify_blocks_a_wrong_map_entry_that_shares_a_token_but_not_a_match_id(tmp_path: Path) -> None:
    """A wrong-but-existing map entry must not resolve on name + shared token alone.

    ``blacksmith-2026`` and ``ess-black-handgun-2026`` are different real
    events (scoreboard 27046 vs 25460) that happen to share a competitor
    -- and so a shooter_token -- and identically-named ``stageN.json``
    audit docs. scoreboard_match_id/match_id is the one thing the rename
    map cannot forge.
    """
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    legacy = _shooter(
        tmp_path / "home" / "blacksmith-2026",
        audits={"stage1.json": "{}"},
        token="t1",
        scoreboard_match_id="27046",
    )
    wrong_destination = _merged_match(
        tmp_path / "x9" / "ess-black-handgun-2026",
        slug="s_a",
        token="t1",
        audits={"stage1.json": "{}"},
        match_id="25460",
    )

    _write_inventory(report_dir, "before", [lib.inventory_project(legacy)])
    _write_inventory(report_dir, "after", [lib.inventory_project(wrong_destination)])
    rename_map = _write_rename_map(report_dir, {"blacksmith-2026": "ess-black-handgun-2026"})
    log = _covering_log(report_dir, [tmp_path / "home" / "blacksmith-2026"])

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(
        f["check"] == "project_identity_mismatch" and "27046" in f["detail"] and "25460" in f["detail"]
        for f in report["blocking"]
    )


def test_verify_blocks_when_a_source_only_raw_file_vanishes(tmp_path: Path) -> None:
    """``plan_reconcile`` already refuses to call this deletable; ``verify`` must agree independently."""
    cli, lib = _cli(), _lib()
    report_dir = tmp_path / "reports"

    before_root = _shooter(tmp_path / "before" / "match" / "s_a", audits={"stage1.json": "{}"}, token="t1")
    (before_root / "raw" / "IMG_9001.MOV").write_bytes(b"0" * 32)
    before = lib.ProjectInventory(root=tmp_path / "before" / "match", kind="legacy", shooters=[])
    before.shooters.append(lib.inventory_project(before_root).shooters[0])

    after_root = _shooter(tmp_path / "after" / "match" / "s_a", audits={"stage1.json": "{}"}, token="t1")
    after = lib.ProjectInventory(root=tmp_path / "after" / "match", kind="legacy", shooters=[])
    after.shooters.append(lib.inventory_project(after_root).shooters[0])

    _write_inventory(report_dir, "before", [before])
    _write_inventory(report_dir, "after", [after])
    rename_map = _write_rename_map(report_dir, {"match": "match"})
    log = _clean_log(report_dir, tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        _verify(cli, report_dir, rename_map=rename_map, reconcile_log=log)

    assert excinfo.value.code == 1
    report = json.loads((report_dir / "verify-before-vs-after.json").read_text())
    assert any(
        f["check"] == "raw_files_survived" and "IMG_9001.MOV" in f["detail"] for f in report["blocking"]
    )
