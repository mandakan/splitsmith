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
        for name in spec.get("audits", []):
            (shooter / "audit" / name).write_text(json.dumps({"doc": name}))
        (shooter / "trimmed").mkdir()
        for index in range(spec.get("trimmed", 0)):
            (shooter / "trimmed" / f"stage{index + 1}_trimmed.mp4").write_bytes(b"0" * 64)
        (shooter / "raw").mkdir()
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
