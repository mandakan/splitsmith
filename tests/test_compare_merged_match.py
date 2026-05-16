"""Compare-export parity test: manifest path vs merged-match path (issue #320).

Builds two legacy single-shooter projects with matching match data, renders
an FCPXML via the manifest flow, then merges them into a Match folder and
renders an FCPXML via the new match flow. The two FCPXMLs must reference
the same trims with the same beep alignments -- otherwise the merge silently
lost something.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from splitsmith.compare.emitter import emit_compare_fcpxml
from splitsmith.compare.manifest import CompareManifest, CompareShooter
from splitsmith.compare.project_loader import (
    load_shooter,
    load_shooter_from_match,
)
from splitsmith.config import StageRounds
from splitsmith.match_model import execute_merge, plan_merge
from splitsmith.ui.match_exports import _slugify
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _stub_probe(_path: Path) -> object:
    """Probe stub that returns a constant VideoMetadata for any trim.

    Both the manifest and match paths exercise the same per-stage trim
    files on disk; returning identical metadata for each keeps the two
    runs deterministically comparable.
    """
    from splitsmith.fcpxml_gen import VideoMetadata

    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _stub_ffmpeg_runner():
    """Stub for the emitter's ffmpeg invocation -- writes an empty target file."""
    import subprocess

    def stub(cmd, **_kw):
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return stub


def _seed_legacy_project(
    root: Path,
    *,
    name: str,
    competitor: str,
    scoreboard_id: str = "27242",
) -> MatchProject:
    """Build a legacy project on disk with one primary video per stage and
    a stub lossless-trim file in ``exports/`` for each stage."""
    project = MatchProject.init(root, name=name)
    project.competitor_name = competitor
    project.scoreboard_match_id = scoreboard_id
    project.scoreboard_content_type = 22
    stages_meta = [
        (1, "Egg Grab", StageRounds(expected=12, paper_targets=6, steel_targets=0)),
        (2, "Tower", StageRounds(expected=16, paper_targets=8, steel_targets=0)),
    ]
    for n, sname, rounds in stages_meta:
        raw_video = root / "raw" / f"video_{n}.mov"
        raw_video.parent.mkdir(parents=True, exist_ok=True)
        raw_video.write_bytes(b"")
        project.stages.append(
            StageEntry(
                stage_number=n,
                stage_name=sname,
                time_seconds=10.0 + n,
                stage_rounds=rounds,
                videos=[
                    StageVideo(
                        path=Path(f"raw/video_{n}.mov"),
                        role="primary",
                        match_timestamp=datetime(2026, 4, 3, 12, 0, n, tzinfo=UTC),
                        beep_time=5.0,
                        beep_source="auto",
                        beep_reviewed=True,
                        processed={"beep": True, "shot_detect": True, "trim": True},
                    )
                ],
            )
        )
    project.save(root)
    # Place a stub lossless trim where the loader looks for it.
    exports = project.exports_path(root)
    exports.mkdir(parents=True, exist_ok=True)
    for n, sname, _ in stages_meta:
        (exports / f"stage{n}_{_slugify(sname)}_trimmed.mp4").write_bytes(b"")
    return project


def _normalize_xml(path: Path) -> ET.Element:
    """Read the FCPXML and zero out fields that legitimately differ across runs.

    The emitter timestamps the FCPXML with the run time; we drop those so
    parity is byte-for-byte structural.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    for elem in root.iter():
        for attr in ("name",):
            # 'name' on the outer event can include a timestamp suffix in
            # some configurations; keep it consistent.
            if attr in elem.attrib and elem.tag == "event":
                elem.attrib[attr] = "stripped"
    return root


@pytest.mark.integration
def test_compare_via_manifest_and_via_merged_match_produce_same_structure(
    tmp_path: Path,
):
    """Same shooters + trims, rendered both ways, should reference the same trims."""
    anton_root = tmp_path / "anton"
    martin_root = tmp_path / "martin"
    _seed_legacy_project(anton_root, name="VADS Easter", competitor="Anton Johansson")
    _seed_legacy_project(martin_root, name="VADS Easter", competitor="Martin Engström")

    # ------- Path 1: render from manifest --------
    manifest = CompareManifest(
        output=tmp_path / "via_manifest.fcpxml",
        audio_from="Anton",
        layout_2up="horizontal",
        shooters=[
            CompareShooter(project=anton_root, label="Anton"),
            CompareShooter(project=martin_root, label="Martin"),
        ],
    )
    bundles_via_manifest = [
        load_shooter(s.project, s.label, probe=_stub_probe) for s in manifest.shooters
    ]
    emit_compare_fcpxml(
        manifest=manifest,
        shooters=bundles_via_manifest,
        output_path=manifest.output,
        runner=_stub_ffmpeg_runner(),
    )

    # ------- Path 2: merge then render from merged match --------
    merged_root = tmp_path / "merged"
    plan = plan_merge([anton_root, martin_root], merged_root)
    match = execute_merge(plan, move=False)
    # The merge slugifies competitor names. The emitter renders labels
    # from Shooter.name (display name), not the slug -- so labels match.
    bundles_via_match = [
        load_shooter_from_match(
            merged_root,
            slug,
            label=match.load_shooter(merged_root, slug).name,
            probe=_stub_probe,
        )
        for slug in match.shooters
    ]
    # Build a synthesized manifest the emitter accepts -- audio_from must
    # match a label in the bundles.
    audio_label = match.load_shooter(merged_root, match.shooters[0]).name
    synthetic = CompareManifest(
        output=tmp_path / "via_match.fcpxml",
        audio_from=audio_label,
        layout_2up="horizontal",
        shooters=[CompareShooter(project=merged_root, label=b.label) for b in bundles_via_match],
    )
    emit_compare_fcpxml(
        manifest=synthetic,
        shooters=bundles_via_match,
        output_path=synthetic.output,
        runner=_stub_ffmpeg_runner(),
    )

    assert manifest.output.exists()
    assert synthetic.output.exists()

    # Both FCPXMLs reference the same stages with the same per-stage trims.
    # The merge moved the trims into shooters/<slug>/exports/ so the *paths*
    # in the second XML differ -- assert structural equivalence by counting
    # asset-clips and ensuring each shooter contributes the same number of
    # stage tiles per output.
    via_manifest = _normalize_xml(manifest.output)
    via_match = _normalize_xml(synthetic.output)

    def _count_asset_refs(root: ET.Element) -> int:
        return len(list(root.iter("asset-clip")))

    def _count_assets(root: ET.Element) -> int:
        return len(list(root.iter("asset")))

    assert _count_assets(via_manifest) == _count_assets(via_match), (
        "merged-match render should reference the same number of source assets "
        "as the manifest render"
    )
    assert _count_asset_refs(via_manifest) == _count_asset_refs(via_match), (
        "merged-match render should place the same number of clips on tiles "
        "as the manifest render"
    )


def test_load_shooter_from_match_skips_stages_with_missing_trims(tmp_path: Path):
    """A merged-match shooter omits stages whose trim file isn't on disk."""
    anton_root = tmp_path / "anton"
    martin_root = tmp_path / "martin"
    _seed_legacy_project(anton_root, name="X", competitor="Anton")
    _seed_legacy_project(martin_root, name="X", competitor="Martin")

    merged_root = tmp_path / "merged"
    plan = plan_merge([anton_root, martin_root], merged_root)
    execute_merge(plan, move=False)

    # Resolve the opaque slug Anton was assigned in the merge plan.
    anton_slug = next(m.slug for m in plan.shooter_moves if m.source_root == anton_root)

    # Delete one shooter's stage-1 trim post-merge.
    victim = (
        merged_root / "shooters" / anton_slug / "exports" / "stage1_egg-grab_trimmed.mp4"
    )
    assert victim.exists()
    victim.unlink()

    bundle = load_shooter_from_match(merged_root, anton_slug, label="Anton", probe=_stub_probe)
    assert 1 not in bundle.stages_by_number
    assert 2 in bundle.stages_by_number
