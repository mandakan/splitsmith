"""Model builds that verify their inputs and land in their own folder.

The calibration build used to skip a fixture whose WAV or feature cache was
missing (a smaller corpus than asked for, with one log line), trusted a
feature cache whenever its row count matched (a re-cut clip often keeps its
count), and always wrote over the shipped model. These pin the R2 audio
check, the cache check by WAV digest, the per-match held-out figures and the
model bundle.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture()
def build():  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import build_ensemble_artifacts as mod
    finally:
        sys.path.pop(0)
    return mod


@pytest.fixture()
def fc():  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import feature_caches as mod
    finally:
        sys.path.pop(0)
    return mod


# -- audio against the R2 lock -------------------------------------------------


@pytest.fixture()
def lock(build, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    fa = build.fixture_audio
    monkeypatch.setattr(fa, "FIXTURES", tmp_path)
    monkeypatch.setattr(fa, "MANIFEST", tmp_path / "audio.lock.json")

    def write(name: str, data: bytes, *, local: bytes | None = None) -> None:
        files = fa.load_manifest()
        (tmp_path / name).write_bytes(data)
        files[name] = {"sha256": fa.sha256(tmp_path / name), "bytes": len(data)}
        fa.save_manifest(files)
        if local is None:
            (tmp_path / name).unlink()
        else:
            (tmp_path / name).write_bytes(local)

    return write


def test_audio_missing_locally_is_fetched_and_digests_come_back(build, lock, monkeypatch, tmp_path) -> None:
    lock("a.wav", b"A", local=b"A")
    lock("b.wav", b"B")
    fetched = []

    def fake_fetch(_url, only=None):
        fetched.extend(only)
        for n in only:
            (tmp_path / n).write_bytes(b"B")
        return []

    monkeypatch.setattr(build.fixture_audio, "fetch", fake_fetch)
    digests = build.ensure_audio(["a", "b"], log=lambda _m: None)
    assert fetched == ["b.wav"]
    assert digests == {
        "a": build.fixture_audio.sha256(tmp_path / "a.wav"),
        "b": build.fixture_audio.sha256(tmp_path / "b.wav"),
    }


def test_audio_the_lock_does_not_know_stops_the_build(build, lock) -> None:
    lock("a.wav", b"A", local=b"A")
    with pytest.raises(build.BuildError, match="not in audio.lock.json"):
        build.ensure_audio(["a", "ghost"], log=lambda _m: None)


def test_a_local_wav_that_differs_from_the_lock_is_never_overwritten(
    build, lock, monkeypatch, tmp_path
) -> None:
    """A re-cut not pushed yet: fetching would replace the new audio with the old."""
    lock("a.wav", b"old", local=b"new")
    monkeypatch.setattr(
        build.fixture_audio, "fetch", lambda *_a, **_k: pytest.fail("fetched over a local WAV")
    )
    with pytest.raises(build.BuildError, match="differ from audio.lock.json"):
        build.ensure_audio(["a"], log=lambda _m: None)
    assert (tmp_path / "a.wav").read_bytes() == b"new"


def test_a_failed_fetch_stops_the_build(build, lock, monkeypatch) -> None:
    lock("a.wav", b"A")
    monkeypatch.setattr(build.fixture_audio, "fetch", lambda *_a, **_k: ["a.wav: 404"])
    with pytest.raises(build.BuildError, match="could not fetch"):
        build.ensure_audio(["a"], log=lambda _m: None)


# -- corpus by match -------------------------------------------------------------


def test_select_fixtures_reads_the_event_match_and_refuses_unknown_ones(build, monkeypatch, tmp_path) -> None:
    for stem, event in [
        ("stage-shots-blacksmith-2026-stage1-sabc", "blacksmith-handgun-open-2026:1:sabc"),
        ("stage-shots-tallmilan-2026-stage1-sabc", "tallmilan-2026:1:sabc"),
    ]:
        (tmp_path / f"{stem}.json").write_text(json.dumps({"event_id": event}), encoding="utf-8")
    monkeypatch.setattr(build, "FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(
        build,
        "fixture_stems",
        lambda reviewed_only=False: [
            "stage-shots-blacksmith-2026-stage1-sabc",
            "stage-shots-tallmilan-2026-stage1-sabc",
        ],
    )
    assert build.select_fixtures(["blacksmith-handgun-open-2026"], reviewed_only=False) == [
        "stage-shots-blacksmith-2026-stage1-sabc"
    ]
    with pytest.raises(build.BuildError, match="no fixtures for match"):
        build.select_fixtures(["blacksmith-2026"], reviewed_only=False)


# -- feature caches against the WAV ------------------------------------------------


def _cache(fc, stem: str, times: list[float], sha: str | None = None) -> None:
    fc.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    extra = {} if sha is None else {"wav_sha256": np.array(sha)}
    for kind in fc.KINDS:
        np.savez(fc.cache_path(stem, kind), times=np.array(times), payload=np.ones(len(times)), **extra)


def test_cache_state(fc, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fc, "CACHE_DIR", tmp_path)
    never = lambda: pytest.fail("a stamped cache needs no detection")  # noqa: E731

    assert fc.cache_state("gone", "s1", fresh_times=never) == "missing"

    _cache(fc, "stamped", [1.0, 2.0], sha="s1")
    assert fc.cache_state("stamped", "s1", fresh_times=never) == "ok"
    assert fc.cache_state("stamped", "s2", fresh_times=never) == "stale"


def test_an_unstamped_cache_is_checked_by_its_times_and_stamped(fc, monkeypatch, tmp_path) -> None:
    """Same count, other audio (a re-cut clip): the old count check passed it."""
    monkeypatch.setattr(fc, "CACHE_DIR", tmp_path)
    _cache(fc, "recut", [1.0, 2.0])
    assert fc.cache_state("recut", "s1", fresh_times=lambda: np.array([1.0, 2.5])) == "stale"
    assert fc.stamp_of(fc.cache_path("recut", "clap")) is None

    _cache(fc, "same", [1.0, 2.0])
    assert fc.cache_state("same", "s1", fresh_times=lambda: np.array([1.0, 2.0])) == "ok"
    for kind in fc.KINDS:
        assert fc.stamp_of(fc.cache_path("same", kind)) == "s1"
        with np.load(fc.cache_path("same", kind)) as z:
            assert z["payload"].tolist() == [1.0, 1.0]


def test_ensure_features_lists_what_needs_extracting_without_extract(fc, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(fc, "CACHE_DIR", tmp_path)
    _cache(fc, "good", [1.0], sha="s1")
    todo = fc.ensure_features(
        ["good", "gone"], {"good": "s1", "gone": "s2"}, extract=False, log=lambda _m: None
    )
    assert todo == ["gone"]


# -- held-out figures per match -------------------------------------------------------


def test_lomo_figures_per_match_and_labels_in_the_oof_rows(build, monkeypatch) -> None:
    groups = {"f1": ("alpha", "alpha:1"), "f2": ("beta", "beta:1")}
    monkeypatch.setattr(build, "_event_groups", lambda f: groups[f])
    rows = [
        {"fixture": "f1", "candidate_idx": 0, "t_absolute": 1.0, "label": 1},
        {"fixture": "f1", "candidate_idx": 1, "t_absolute": 2.0, "label": 0},
        {"fixture": "f2", "candidate_idx": 0, "t_absolute": 1.0, "label": 1},
        {"fixture": "f2", "candidate_idx": 1, "t_absolute": 2.0, "label": 1},
    ]
    labels = np.array([r["label"] for r in rows])
    cv = np.array([0.9, 0.1, 0.9, 0.9])
    lomo = np.array([0.9, 0.8, 0.9, 0.2])
    report = build._heldout_class_report(rows, cv, labels, {"grouped": None, "lomo": lomo}, 0.5, 0.95)
    assert report["lomo_by_match"]["alpha"]["fp"] == 1
    assert report["lomo_by_match"]["beta"]["recall"] == 0.5

    out = build._heldout_rows(rows, cv, {"grouped": None, "lomo": lomo})
    assert [r["label"] for r in out] == [1, 0, 1, 1]


# -- the bundle --------------------------------------------------------------------


def test_bundle_manifest_round_trips_lists_and_verifies(tmp_path) -> None:
    from splitsmith.ensemble import model_bundle as mb

    root = tmp_path / "models"
    folder = root / "reaudit"
    (folder / "heldout").mkdir(parents=True)
    (folder / "ensemble_calibration.json").write_text("{}", encoding="utf-8")
    (folder / "heldout" / "report.json").write_text("{}", encoding="utf-8")
    manifest = mb.ModelManifest(
        name="reaudit",
        version="v1",
        built_at="2026-10-15T18:00:00+00:00",
        selection=mb.CorpusSelection(matches=["stockholm-ipsc-open-2026"]),
        corpus=[mb.CorpusFixture(stem="s", wav_sha256="d" * 64)],
    )
    mb.write_manifest(folder, manifest)

    read = mb.read_manifest(folder)
    assert read is not None
    assert [f.name for f in read.files] == ["ensemble_calibration.json", "heldout/report.json"]
    assert mb.verify(folder, read) == []
    assert [m.name for _d, m in mb.list_bundles(root)] == ["reaudit"]

    (folder / "ensemble_calibration.json").write_text('{"changed": true}', encoding="utf-8")
    assert mb.verify(folder, read) == ["ensemble_calibration.json: changed"]
    assert mb.list_bundles(tmp_path / "nowhere") == []
