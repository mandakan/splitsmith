"""Fixture source paths move by directory substitution, never by guess.

78 of 161 fixtures point at directories this consolidation retires. The
rewrite has to be mechanical and auditable: a prefix it does not know
about is reported, never rewritten to something plausible.
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
        return importlib.import_module("migrate_fixtures_raw_root")
    finally:
        sys.path.pop(0)


MAPPING = {
    "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw": (
        "/Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345/raw"
    ),
    "/Volumes/mathias/skytte/video/raw/tallmilan-2026/martin/handheld": (
        "/Volumes/X9/matches/tallmilan-2026/shooters/s_36ed6e4e/raw"
    ),
}


def test_rewrites_a_legacy_project_path_keeping_the_filename() -> None:
    mod = _mod()
    outcome = mod.rewrite_source_video(
        "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov", MAPPING
    )
    assert outcome.status == "rewritten"
    assert outcome.rewritten == (
        "/Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345/raw/IMG_1295.mov"
    )


def test_rewrites_a_share_path_to_the_shooter_raw_form() -> None:
    mod = _mod()
    outcome = mod.rewrite_source_video(
        "/Volumes/mathias/skytte/video/raw/tallmilan-2026/martin/handheld/martin_stage_4.MOV",
        MAPPING,
    )
    assert outcome.status == "rewritten"
    assert outcome.rewritten == (
        "/Volumes/X9/matches/tallmilan-2026/shooters/s_36ed6e4e/raw/martin_stage_4.MOV"
    )


def test_a_path_already_in_canonical_form_is_left_alone() -> None:
    mod = _mod()
    canonical = "/Volumes/X9/matches/hfo-masters-2026/shooters/s_f88d8aa0/raw/IMG_9001.MOV"
    outcome = mod.rewrite_source_video(canonical, MAPPING)
    assert outcome.status == "already_canonical"
    assert outcome.rewritten is None


def test_an_unknown_prefix_is_reported_never_guessed() -> None:
    mod = _mod()
    outcome = mod.rewrite_source_video("/Users/mathias/Downloads/Gone/IMG_0001.MOV", MAPPING)
    assert outcome.status == "unmapped"
    assert outcome.rewritten is None


def test_the_run_is_idempotent_over_a_fixture_tree(tmp_path: Path) -> None:
    mod = _mod()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    doc = {
        "stage_number": 1,
        "source_video": "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov",
    }
    target = fixtures / "stage-shots-vads-stage1-s0fe3d797.json"
    target.write_text(json.dumps(doc))

    first = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=False)
    second = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=False)

    assert first.rewritten == 1
    assert second.rewritten == 0
    assert second.already_canonical == 1
    written = json.loads(target.read_text())
    assert written["source_video"] == (
        "/Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345/raw/IMG_1295.mov"
    )
    assert written["stage_number"] == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    mod = _mod()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    original = "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov"
    target = fixtures / "stage-shots-vads-stage1-s0fe3d797.json"
    target.write_text(json.dumps({"source_video": original}))

    report = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=True)

    assert report.rewritten == 1
    assert json.loads(target.read_text())["source_video"] == original


def test_longest_prefix_wins_when_overlapping_keys_match() -> None:
    """Mapping may contain both match-level and shooter-level entries.

    When both a shorter and longer prefix match the same input path,
    the longest one wins (via max(matches, key=len)).
    """
    mod = _mod()
    # This mapping has overlapping prefixes: the longer one is more specific.
    overlapping_mapping = {
        "/Volumes/X9/matches/rally-2026": "/Volumes/X9/matches/rally-2026/fixed",
        "/Volumes/X9/matches/rally-2026/raw/shooter1": (
            "/Volumes/X9/matches/rally-2026/shooters/s_abc123/raw"
        ),
    }
    # Input matches both prefixes, but the longer one should win.
    outcome = mod.rewrite_source_video(
        "/Volumes/X9/matches/rally-2026/raw/shooter1/IMG_0001.mov", overlapping_mapping
    )
    assert outcome.status == "rewritten"
    # Should use the destination of the longer (more specific) prefix.
    assert outcome.rewritten == "/Volumes/X9/matches/rally-2026/shooters/s_abc123/raw/IMG_0001.mov"


def test_path_boundary_matching_not_bare_string_prefix() -> None:
    """Matching is on path boundaries, not bare string prefixes.

    A mapping for /path/to/raw/2026-tallmilan should NOT match an input
    under /path/to/raw/2026-tallmilan-extra/, even though the string is
    a prefix. The boundary logic (parent.startswith(f"{old}/")) prevents
    this.
    """
    mod = _mod()
    # Mapping keyed on a directory whose NAME is a string prefix of another.
    boundary_mapping = {
        "/Volumes/X9/raw/2026-tallmilan": ("/Volumes/X9/matches/tallmilan-2026/shooters/s_deadbeef/raw"),
    }
    # Input is under a DIFFERENT directory that shares a name prefix.
    outcome = mod.rewrite_source_video("/Volumes/X9/raw/2026-tallmilan-extra/IMG_1234.mov", boundary_mapping)
    # Should be unmapped because the path boundary check rejects bare prefix match.
    assert outcome.status == "unmapped"
    assert outcome.rewritten is None


def test_a_fixture_that_falls_out_of_the_arithmetic_is_counted_and_named(tmp_path: Path) -> None:
    """rewritten + already_canonical + unmapped must account for every fixture.

    Malformed JSON, a non-dict document and an empty ``source_video`` were
    skipped with no counter at all, so a fixture could fall out of the
    corpus while the plan's ``== 161`` arithmetic still added up.
    """
    mod = _mod()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "stage-shots-broken-stage1-s0fe3d797.json").write_text("{not json")
    (fixtures / "stage-shots-list-stage2-s0fe3d797.json").write_text(json.dumps([1, 2, 3]))
    (fixtures / "stage-shots-empty-stage3-s0fe3d797.json").write_text(json.dumps({"source_video": ""}))
    (fixtures / "stage-shots-good-stage4-s0fe3d797.json").write_text(
        json.dumps({"source_video": "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov"})
    )

    report = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=True)

    assert report.skipped == 3
    assert sorted(report.skipped_files) == [
        "stage-shots-broken-stage1-s0fe3d797.json",
        "stage-shots-empty-stage3-s0fe3d797.json",
        "stage-shots-list-stage2-s0fe3d797.json",
    ]
    total = report.rewritten + report.already_canonical + report.unmapped + report.skipped
    assert total == 4
