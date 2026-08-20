"""The four reconciliation rules, and the guard that outranks them.

Measured facts these encode: the merged copies hold newer audit docs
than their legacy sources (more audit_events, later timestamps), the X9
copies hold the media the home copies had stripped, and
blacksmith-handgun-open-2026's mathias shooter is missing 7 of 8 audit
docs that legacy blacksmith-2026 still has. A migration that assumes
containment destroys those 7.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _shooter(root: Path, *, audits: dict[str, str], token: str | None = None, trimmed: int = 0) -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    for name, body in audits.items():
        (root / "audit" / name).write_text(body)
    (root / "trimmed").mkdir()
    for index in range(trimmed):
        (root / "trimmed" / f"stage{index + 1}_trimmed.mp4").write_bytes(b"0" * 64)
    return root


def test_destination_wins_where_both_sides_have_the_doc(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "legacy", audits={"stage1.json": '{"v": "old"}'})
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "merged", audits={"stage1.json": '{"v": "new"}'})
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    assert [a for a in plan.actions if a.kind == "copy_audit_doc"] == []
    assert plan.deletable is True


def test_source_fills_a_gap_the_destination_has(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(
            tmp_path / "legacy",
            audits={f"stage{n}.json": json.dumps({"stage_number": n}) for n in range(1, 9)},
        )
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "merged", audits={"stage4.json": json.dumps({"stage_number": 4})})
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    copied = sorted(a.destination.name for a in plan.actions if a.kind == "copy_audit_doc")
    assert copied == [f"stage{n}.json" for n in (1, 2, 3, 5, 6, 7, 8)]
    assert plan.deletable is True, "after the copies, nothing is left behind"


def test_media_is_unioned_with_the_destination_winning_collisions(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "x9", audits={"stage1.json": "{}"}, trimmed=3)
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "merged", audits={"stage1.json": "{}"}, trimmed=1)
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    copied = sorted(a.destination.name for a in plan.actions if a.kind == "copy_media")
    assert copied == ["stage2_trimmed.mp4", "stage3_trimmed.mp4"]


def test_shooter_token_is_carried_over_when_the_destination_lacks_one(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "home", audits={"stage1.json": "{}"}, token="s97dcec94")
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "x9", audits={"stage1.json": "{}"}, token=None)
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    token_actions = [a for a in plan.actions if a.kind == "set_shooter_token"]
    assert len(token_actions) == 1
    assert token_actions[0].detail == "s97dcec94"


def test_an_existing_destination_token_is_never_overwritten(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "home", audits={"stage1.json": "{}"}, token="s97dcec94")
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "x9", audits={"stage1.json": "{}"}, token="s36ed6e4e")
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    assert [a for a in plan.actions if a.kind == "set_shooter_token"] == []


def test_a_bak_only_document_does_not_count_as_a_counterpart(tmp_path: Path) -> None:
    mod = _mod()
    source_root = _shooter(tmp_path / "legacy", audits={"stage1.json": '{"real": true}'})
    dest_root = _shooter(tmp_path / "merged", audits={})
    (dest_root / "audit" / "stage1.json.bak").write_text('{"stale": true}')

    plan = mod.plan_reconcile(
        mod.inventory_project(source_root).shooters[0],
        mod.inventory_project(dest_root).shooters[0],
    )

    copied = [a for a in plan.actions if a.kind == "copy_audit_doc"]
    assert len(copied) == 1
    assert copied[0].destination.name == "stage1.json"


def test_unlinked_raw_footage_missing_from_the_destination_blocks_deletion(tmp_path: Path) -> None:
    """A real file sitting in raw/ (not a symlink) is content, same as an audit doc.

    plan_reconcile only ever moves audit docs and media -- it never copies
    raw/ files -- so a source-only raw file can never be "scheduled for
    copy" the way an audit doc can. It must always surface as a violation
    when the destination doesn't already have it.
    """
    mod = _mod()
    source_root = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}"})
    (source_root / "raw").mkdir()
    (source_root / "raw" / "clip.mp4").write_bytes(b"0" * 32)
    dest_root = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})
    (dest_root / "raw").mkdir()

    plan = mod.plan_reconcile(
        mod.inventory_project(source_root).shooters[0],
        mod.inventory_project(dest_root).shooters[0],
    )

    assert plan.deletable is False
    assert any(v.document == "clip.mp4" for v in plan.violations)
