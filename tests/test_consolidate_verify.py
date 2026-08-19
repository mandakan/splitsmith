"""Verification is what stands between the migration and rm -rf.

Each predicate returns findings, never a bool: the report has to say
which document, which shooter, which byte count, or it cannot be read by
a human deciding whether to delete 3 GB of originals.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _match(root: Path, shooters: dict[str, dict]) -> Path:
    (root / "shooters").mkdir(parents=True)
    (root / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    for slug, spec in shooters.items():
        shooter = root / "shooters" / slug
        shooter.mkdir()
        project = {"schema_version": 2, "name": "M", "stages": []}
        if spec.get("token"):
            project["shooter_token"] = spec["token"]
        (shooter / "project.json").write_text(json.dumps(project))
        (shooter / "audit").mkdir()
        audit_content = spec.get("audit_content", {})
        for name in spec.get("audits", []):
            content = audit_content.get(name, json.dumps({"doc": name}))
            (shooter / "audit" / name).write_text(content)
        (shooter / "trimmed").mkdir()
        sizes = spec.get("trimmed_sizes") or [64] * spec.get("trimmed", 0)
        for index, size in enumerate(sizes):
            (shooter / "trimmed" / f"stage{index + 1}_trimmed.mp4").write_bytes(b"0" * size)
        (shooter / "raw").mkdir()
    return root


def _legacy(root: Path, *, token: str | None = None, audits: list[str] | None = None) -> Path:
    """A single-shooter legacy project: no match.json, no shooters/ dir."""
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    for name in audits or []:
        (root / "audit" / name).write_text(json.dumps({"doc": name}))
    (root / "trimmed").mkdir()
    (root / "raw").mkdir()
    return root


def test_a_lost_document_is_reported_by_name(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json", "stage2.json"]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json"]}})
    )

    findings = mod.verify_documents_survived(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "stage2.json" in findings[0].detail


def test_documents_that_all_survived_report_nothing(tmp_path: Path) -> None:
    mod = _mod()
    spec = {"s_a": {"token": "s97dcec94", "audits": ["stage1.json", "stage2.json"]}}
    before = mod.inventory_project(_match(tmp_path / "before", spec))
    after = mod.inventory_project(_match(tmp_path / "after", spec))

    assert mod.verify_documents_survived(before, after) == []


def test_shrunk_media_is_reported_with_both_byte_counts(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 3}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed": 1}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert "192" in findings[0].detail and "64" in findings[0].detail


def test_a_broken_link_after_migration_is_a_finding(tmp_path: Path) -> None:
    mod = _mod()
    root = _match(tmp_path / "after", {"s_a": {"token": "t", "audits": []}})
    (root / "shooters" / "s_a" / "raw" / "IMG_1.MOV").symlink_to(tmp_path / "gone" / "IMG_1.MOV")

    findings = mod.verify_no_broken_links(mod.inventory_project(root))

    assert len(findings) == 1
    assert "IMG_1.MOV" in findings[0].detail


def test_a_dropped_shooter_token_is_a_finding(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(_match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": []}}))
    after = mod.inventory_project(_match(tmp_path / "after", {"s_a": {"audits": []}}))

    findings = mod.verify_tokens_preserved(before, after)

    assert len(findings) == 1
    assert "s97dcec94" in findings[0].detail


def test_a_same_name_different_hash_document_is_replaced_not_lost(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(
            tmp_path / "before",
            {
                "s_a": {
                    "token": "t",
                    "audits": ["stage1.json"],
                    "audit_content": {"stage1.json": '{"v": 1}'},
                }
            },
        )
    )
    after = mod.inventory_project(
        _match(
            tmp_path / "after",
            {
                "s_a": {
                    "token": "t",
                    "audits": ["stage1.json"],
                    "audit_content": {"stage1.json": '{"v": 2}'},
                }
            },
        )
    )

    replaced = mod.verify_documents_replaced(before, after)
    survived = mod.verify_documents_survived(before, after)

    assert survived == []
    assert len(replaced) == 1
    assert replaced[0].subject == "t"
    assert "stage1.json" in replaced[0].detail


def test_a_same_name_same_hash_document_reports_nothing(tmp_path: Path) -> None:
    mod = _mod()
    spec = {
        "s_a": {
            "token": "t",
            "audits": ["stage1.json"],
            "audit_content": {"stage1.json": '{"v": 1}'},
        }
    }
    before = mod.inventory_project(_match(tmp_path / "before", spec))
    after = mod.inventory_project(_match(tmp_path / "after", spec))

    assert mod.verify_documents_replaced(before, after) == []
    assert mod.verify_documents_survived(before, after) == []


def test_a_vanished_shooter_is_a_media_finding_with_byte_counts(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 2}})
    )
    after = mod.inventory_project(_match(tmp_path / "after", {}))

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "t"
    assert "128" in findings[0].detail


def test_a_renamed_relocated_project_pairs_by_shooter_token(tmp_path: Path) -> None:
    """The migration's whole point is that projects move and get renamed.

    A legacy directory becomes a shooter inside a merged match under a new
    name, on a different volume. Pairing on the directory basename finds
    nothing, which is how a project's documents can vanish under a clean
    report.
    """
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "blacksmith-2026", token="s97dcec94"))]
    after = [
        mod.inventory_project(
            _match(
                tmp_path / "x9" / "blacksmith-handgun-open-2026",
                {
                    "s_ce10fa76": {"token": "s97dcec94", "audits": []},
                    "s_9540b345": {"token": "s9540b345", "audits": []},
                },
            )
        )
    ]

    pairs = mod.pair_projects(before, after)

    assert len(pairs) == 1
    assert pairs[0][1] is not None
    assert pairs[0][1].root.name == "blacksmith-handgun-open-2026"


def test_projects_pair_by_match_id_when_both_sides_have_one(tmp_path: Path) -> None:
    mod = _mod()
    before_root = _match(tmp_path / "home" / "oden-cup-2026", {"s_a": {"token": "t", "audits": []}})
    after_root = _match(tmp_path / "x9" / "oden-cup-2026-final", {"s_a": {"token": "t", "audits": []}})
    before = [mod.inventory_project(before_root)]
    after = [mod.inventory_project(after_root)]

    pairs = mod.pair_projects(before, after)

    assert pairs[0][1] is not None
    assert pairs[0][1].root == after_root


def test_a_project_missing_from_the_after_inventory_is_unpaired(tmp_path: Path) -> None:
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "gone-2026", token="s0fe3d797"))]
    after = [
        mod.inventory_project(_match(tmp_path / "x9" / "other-2026", {"s_a": {"token": "t", "audits": []}}))
    ]

    pairs = mod.pair_projects(before, after)

    assert pairs[0][1] is None
    finding = mod.unpaired_project_finding(pairs[0][0])
    assert finding.check == "project_paired"
    assert "gone-2026" in finding.subject
    assert "s0fe3d797" in finding.detail


def test_a_project_that_stayed_put_pairs_by_name(tmp_path: Path) -> None:
    """Neither side has a match_id or a token; the name is all there is."""
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "tallmilan-2025"))]
    after = [mod.inventory_project(_legacy(tmp_path / "x9" / "tallmilan-2025"))]

    assert mod.pair_projects(before, after)[0][1] is not None


def test_a_shrunk_media_count_at_equal_bytes_is_a_finding(tmp_path: Path) -> None:
    """Bytes alone cannot see one clip replacing two of half the size."""
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed_sizes": [64, 64]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed_sizes": [128]}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert "2 file" in findings[0].detail and "1 file" in findings[0].detail


def test_shrunk_media_reports_counts_and_bytes_together(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 3}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed": 1}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    detail = findings[0].detail
    assert "3 file" in detail and "192" in detail
    assert "1 file" in detail and "64" in detail


def test_a_shooter_that_vanished_entirely_loses_its_documents(tmp_path: Path) -> None:
    """The within-project twin of an unpaired project.

    Every other test reuses the same slug on both sides, so the slug
    fallback always pairs and this branch never runs.
    """
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json"]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_b": {"token": "s36ed6e4e", "audits": ["stage1.json"]}})
    )

    findings = mod.verify_documents_survived(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "no counterpart shooter" in findings[0].detail


def test_a_shooter_that_vanished_entirely_loses_its_token(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(_match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": []}}))
    after = mod.inventory_project(_match(tmp_path / "after", {"s_b": {"token": "s36ed6e4e", "audits": []}}))

    findings = mod.verify_tokens_preserved(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "s97dcec94" in findings[0].detail


def test_a_recorded_undeletable_reconcile_is_a_finding(tmp_path: Path) -> None:
    """The central safety rule has to reach the report a human reads."""
    mod = _mod()
    record = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged" / "shooters" / "s_a",
        applied=False,
        action_count=0,
        violations=[
            mod.SafetyViolation(source=tmp_path / "legacy", document="IMG_1.MOV", reason="unlinked raw")
        ],
        deletable=False,
    )

    findings = mod.verify_reconcile_records([record])

    assert len(findings) == 1
    assert findings[0].check == "reconcile_deletable"
    assert "legacy" in findings[0].subject
    assert "IMG_1.MOV" in findings[0].detail


def test_a_recorded_deletable_reconcile_reports_nothing(tmp_path: Path) -> None:
    mod = _mod()
    record = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged",
        applied=True,
        action_count=2,
        violations=[],
        deletable=True,
    )

    assert mod.verify_reconcile_records([record]) == []


def test_a_malformed_project_json_names_the_offending_file(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy(tmp_path / "broken-2026")
    (root / "project.json").write_text("{not json")

    with pytest.raises(mod.MalformedProjectError) as excinfo:
        mod.inventory_project(root)

    assert str(root / "project.json") in str(excinfo.value)


def test_a_malformed_match_json_names_the_offending_file(tmp_path: Path) -> None:
    mod = _mod()
    root = _match(tmp_path / "broken-match", {"s_a": {"token": "t", "audits": []}})
    (root / "match.json").write_text("{not json")

    with pytest.raises(mod.MalformedProjectError) as excinfo:
        mod.inventory_project(root)

    assert str(root / "match.json") in str(excinfo.value)
