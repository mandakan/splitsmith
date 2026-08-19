"""The inventory is the only record of what existed before deletion.

Every later comparison -- did a doc survive, did media shrink, is a link
broken -- reads this. It must describe a legacy single-shooter project
and a merged multi-shooter match in the same shape, so the reconciler
does not care which it was handed.
"""

from __future__ import annotations

import hashlib
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


def _legacy_project(root: Path, *, token: str | None = "s97dcec94") -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "Tallmilan 2026", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    (root / "audit" / "stage1.json").write_text('{"stage_number": 1}')
    (root / "audit" / "stage1.json.bak").write_text('{"stage_number": 1, "stale": true}')
    (root / "trimmed").mkdir()
    (root / "trimmed" / "stage1_trimmed.mp4").write_bytes(b"0" * 128)
    (root / "raw").mkdir()
    return root


def test_inventories_a_legacy_project_as_a_single_shooter(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy_project(tmp_path / "tallmilan-2026")

    inv = mod.inventory_project(root)

    assert inv.kind == "legacy"
    assert len(inv.shooters) == 1
    shooter = inv.shooters[0]
    assert shooter.shooter_token == "s97dcec94"
    assert set(shooter.audit_docs) == {"stage1.json"}, ".bak files are not audit docs"
    assert shooter.audit_docs["stage1.json"] == hashlib.sha256(b'{"stage_number": 1}').hexdigest()
    assert shooter.media_counts["trimmed"] == 1
    assert shooter.media_bytes["trimmed"] == 128


def test_inventories_a_merged_match_shooter_by_shooter(tmp_path: Path) -> None:
    mod = _mod()
    match_root = tmp_path / "tallmilan-2026-merged"
    (match_root / "shooters").mkdir(parents=True)
    (match_root / "match.json").write_text(
        json.dumps({"schema_version": 4, "match_id": "tallmilan-2026-abc", "name": "Tallmilan 2026"})
    )
    _legacy_project(match_root / "shooters" / "s_aaa", token="s97dcec94")
    _legacy_project(match_root / "shooters" / "s_bbb", token="s36ed6e4e")

    inv = mod.inventory_project(match_root)

    assert inv.kind == "match"
    assert inv.match_id == "tallmilan-2026-abc"
    assert sorted(s.slug for s in inv.shooters) == ["s_aaa", "s_bbb"]


def test_records_broken_symlinks_by_name(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy_project(tmp_path / "blacksmith-2026")
    (root / "raw" / "IMG_2979.MOV").symlink_to(tmp_path / "gone" / "IMG_2979.MOV")
    real = tmp_path / "present.MOV"
    real.write_bytes(b"x")
    (root / "raw" / "IMG_2986.MOV").symlink_to(real)

    inv = mod.inventory_project(root)

    shooter = inv.shooters[0]
    assert shooter.broken_links == ["IMG_2979.MOV"]
    assert shooter.link_targets["IMG_2986.MOV"] == str(real)


def test_records_real_files_in_raw_directory(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy_project(tmp_path / "stockton-2026")
    # Add a real file (not a symlink) to raw/
    (root / "raw" / "footage.MOV").write_bytes(b"x" * 256)
    # Add a symlink to raw/ to verify they are not mixed
    real_external = tmp_path / "external.MOV"
    real_external.write_bytes(b"y" * 512)
    (root / "raw" / "linked.MOV").symlink_to(real_external)

    inv = mod.inventory_project(root)

    shooter = inv.shooters[0]
    assert shooter.raw_files["footage.MOV"] == 256
    assert "linked.MOV" in shooter.link_targets
    assert "linked.MOV" not in shooter.raw_files
    assert "footage.MOV" not in shooter.link_targets
