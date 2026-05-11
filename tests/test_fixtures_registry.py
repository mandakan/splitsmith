"""Tests for the auto-discovered fixture registry.

These exercise discovery against a temp dir so the tests are independent
of how the real ``tests/fixtures/`` corpus grows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.ensemble import fixtures as registry


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """``all_fixtures`` is process-cached; reset it before every test."""
    registry.all_fixtures.cache_clear()
    yield
    registry.all_fixtures.cache_clear()


def _write_audit(
    dir_: Path,
    stem: str,
    *,
    mount: str = "head",
    shooter_id: str = "s00000000",
    n_shots: int = 5,
    expected_rounds: int | None = 5,
    make: str | None = "Insta360",
    model: str | None = "GO 3S",
    write_wav: bool = True,
) -> None:
    payload = {
        "beep_time": 0.5,
        "stage_time_seconds": 30.0,
        "shots": [{"shot_number": i + 1, "time": 1.0 + i * 0.3} for i in range(n_shots)],
        "camera": {"mount": mount, "id": "go3s", "make": make, "model": model, "position": "shooter"},
        "shooter": {"id": shooter_id},
        "stage_rounds": {"expected": expected_rounds} if expected_rounds is not None else {},
    }
    (dir_ / f"{stem}.json").write_text(json.dumps(payload))
    if write_wav:
        (dir_ / f"{stem}.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")


def test_discovery_finds_audit_jsons_and_parses_metadata(tmp_path: Path) -> None:
    _write_audit(tmp_path, "stage-shots-foo-2026-stage1-s12345678", shooter_id="s12345678")
    _write_audit(
        tmp_path,
        "stage-shots-bar-2026-stage7-s00000000",
        mount="hand",
        expected_rounds=12,
        n_shots=12,
        make=None,
        model=None,
    )

    fx = registry.all_fixtures(tmp_path)
    assert len(fx) == 2
    by_stem = {f.stem: f for f in fx}
    foo = by_stem["stage-shots-foo-2026-stage1-s12345678"]
    assert foo.mount == "head"
    assert foo.shooter_id == "s12345678"
    assert foo.match == "foo-2026"
    assert foo.stage_number == 1
    assert foo.expected_rounds == 5
    assert foo.n_audited_shots == 5
    assert foo.audited
    assert foo.is_headcam
    assert foo.camera_class == "headcam"

    bar = by_stem["stage-shots-bar-2026-stage7-s00000000"]
    assert bar.mount == "hand"
    assert bar.match == "bar-2026"
    assert bar.stage_number == 7
    assert bar.camera_class == "handheld"
    assert bar.camera_make is None  # missing metadata propagates as None


def test_discovery_skips_non_audit_json_files(tmp_path: Path) -> None:
    _write_audit(tmp_path, "stage-shots-foo-2026-stage1-s12345678")
    # Non-audit lookalikes that must be ignored.
    (tmp_path / "stage-shots-foo-2026-stage1.json.bak").write_text("{}")
    (tmp_path / "stage-shots-foo-2026-stage1-s12345678-promotion-report.json").write_text("{}")
    (tmp_path / "stage-shots-foo-2026-stage1.peaks-1200.json").write_text("{}")
    (tmp_path / "stage-shots-foo-2026-stage1.json.before-promote").write_text("{}")
    # A real .json that's not an audit (missing beep_time).
    (tmp_path / "stage-shots-bogus-2026-stage1.json").write_text(json.dumps({"shots": []}))
    fx = registry.all_fixtures(tmp_path)
    assert [f.stem for f in fx] == ["stage-shots-foo-2026-stage1-s12345678"]


def test_audited_filter_drops_zero_shot_fixtures(tmp_path: Path) -> None:
    _write_audit(tmp_path, "stage-shots-foo-2026-stage1-s12345678", n_shots=3)
    _write_audit(tmp_path, "stage-shots-foo-2026-stage2-s12345678", n_shots=0)
    fx_all = registry.all_fixtures(tmp_path)
    assert len(fx_all) == 2  # both discovered
    # ``audited`` uses module-level FIXTURES_DIR by default; we have to
    # monkeypatch the discovery to point at the temp dir for the helper
    # to filter against the right set. Easiest: reuse all_fixtures(dir).
    audited = [f for f in registry.all_fixtures(tmp_path) if f.audited]
    assert [f.stem for f in audited] == ["stage-shots-foo-2026-stage1-s12345678"]


def test_filters_by_mount_and_shooter(tmp_path: Path, monkeypatch) -> None:
    _write_audit(tmp_path, "stage-shots-m-2026-stage1-saaaa1111", mount="head", shooter_id="saaaa1111")
    _write_audit(tmp_path, "stage-shots-m-2026-stage2-saaaa1111", mount="hand", shooter_id="saaaa1111")
    _write_audit(tmp_path, "stage-shots-m-2026-stage3-sbbbb2222", mount="head", shooter_id="sbbbb2222")
    monkeypatch.setattr(registry, "FIXTURES_DIR", tmp_path)
    registry.all_fixtures.cache_clear()

    head_a = registry.audited(mount="head", shooter_id="saaaa1111")
    assert [f.stem for f in head_a] == ["stage-shots-m-2026-stage1-saaaa1111"]

    head_all = registry.fixture_stems(mount="head")
    assert head_all == [
        "stage-shots-m-2026-stage1-saaaa1111",
        "stage-shots-m-2026-stage3-sbbbb2222",
    ]


def test_exclude_wrong_clip_strips_known_bad_video_fixtures(
    tmp_path: Path, monkeypatch
) -> None:
    bad = next(iter(registry.WRONG_CLIP_FIXTURES))
    good = "stage-shots-good-2026-stage1-s12345678"
    _write_audit(tmp_path, bad)
    _write_audit(tmp_path, good)
    monkeypatch.setattr(registry, "FIXTURES_DIR", tmp_path)
    registry.all_fixtures.cache_clear()

    without_filter = registry.fixture_stems()
    assert bad in without_filter and good in without_filter
    with_filter = registry.fixture_stems(exclude_wrong_clip=True)
    assert bad not in with_filter and good in with_filter


def test_fixture_stems_is_sorted_for_stable_iteration(tmp_path: Path, monkeypatch) -> None:
    """Discovery returns stems in lexicographic order so downstream code
    that depends on iteration order (folds, train/test splits) gets the
    same answer regardless of filesystem walk order."""
    for stem in (
        "stage-shots-foo-2026-stage3-s12345678",
        "stage-shots-foo-2026-stage1-s12345678",
        "stage-shots-foo-2026-stage2-s12345678",
    ):
        _write_audit(tmp_path, stem)
    monkeypatch.setattr(registry, "FIXTURES_DIR", tmp_path)
    registry.all_fixtures.cache_clear()
    stems = registry.fixture_stems()
    assert stems == sorted(stems)


def test_real_corpus_has_audited_headcam_fixtures() -> None:
    """Anchor test against the actual ``tests/fixtures/`` corpus -- catches
    a regression where discovery silently returns an empty list (e.g.
    if FIXTURES_DIR resolves to the wrong path after a refactor).

    The exact count grows over time, so we only assert lower bounds and
    invariants. The corpus had 13 audited headcam fixtures from shooter
    s97dcec94 at the time this registry shipped (#302)."""
    head_s97 = registry.audited(mount="head", shooter_id="s97dcec94")
    assert len(head_s97) >= 13
    assert all(f.is_headcam for f in head_s97)
    assert all(f.audited for f in head_s97)
    # Every discovered headcam fixture must have a stage number parsed
    # out of its stem -- if this fails the regex needs widening.
    assert all(f.stage_number is not None for f in head_s97)


def test_real_corpus_camera_classes_partition_audited() -> None:
    """Every audited fixture maps to one of the two known camera classes."""
    audited = [f for f in registry.all_fixtures() if f.audited]
    classes = {f.camera_class for f in audited}
    assert classes <= {"headcam", "handheld"}
    # No audited fixture should be missing a mount.
    assert all(f.mount in {"head", "hand"} for f in audited)
