"""Tests for the hosted-mode audit-trim cache push/pull.

The audit-trim MP4 (``trimmed/stage<N>_cam_<video_id>_trimmed.mp4``) is
the short-GOP scrub clip the audit screen streams. On a worker fleet it
is cut out-of-process, so it has to round-trip through storage the same
way the audio WAV already does (see ``test_audio_storage_cache.py``):
worker cuts it once and pushes it up; the API pulls it down to serve the
``<video>`` bytes, and a second worker pulls it to skip the ffmpeg re-cut.

These tests stub the ffmpeg-backed ``splitsmith.trim`` calls so they
don't depend on a real binary, and use ``FilesystemStorage`` against
``tmp_path`` (Protocol-equivalent to ``S3Storage`` per
``test_s3_storage.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.storage import FilesystemStorage
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.audio import ensure_video_audit_trim, trimmed_video_path
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

STAGE = 1
STAGE_TIME = 10.0
BEEP_TIME = 3.5
SCOPE = "matches/m1/shooters/me"


@pytest.fixture
def fake_trim(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace the ffmpeg-backed trim with a fake that writes the output.

    ``ensure_video_audit_trim`` calls ``splitsmith.trim.select_audit_encoder``
    then ``splitsmith.trim.trim_video(output_path=<partial>, ...)``. The
    fake records each ``trim_video`` call's kwargs and writes a sentinel
    byte to the partial so the rest of the pipeline sees a real (tiny)
    MP4. Returns the list of recorded calls so tests can assert "trim ran"
    or "trim did NOT run".
    """
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "splitsmith.trim.select_audit_encoder",
        lambda *a, **k: "libx264",
    )

    def fake_trim_video(**kwargs: object) -> None:
        calls.append(dict(kwargs))
        dest = Path(kwargs["output_path"])  # type: ignore[arg-type]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"MP4DATA")

    monkeypatch.setattr("splitsmith.trim.trim_video", fake_trim_video)
    return calls


def _project_with_stage_video(root: Path, video_path: Path) -> tuple[MatchProject, StageVideo]:
    project = MatchProject.init(root, name="trim-test")
    video = StageVideo(path=video_path, role="primary")
    project.stages = [
        StageEntry(
            stage_number=STAGE,
            stage_name="One",
            time_seconds=STAGE_TIME,
            videos=[video],
        )
    ]
    project.save(root)
    return project, video


def _cut(root: Path, project: MatchProject, video: StageVideo, source: Path) -> Path:
    return ensure_video_audit_trim(
        root,
        STAGE,
        video,
        source,
        BEEP_TIME,
        STAGE_TIME,
        project=project,
    )


def _make_source(tmp_path: Path) -> Path:
    source = tmp_path / "raw" / "v.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video bytes")
    return source


def _current_params(project: MatchProject) -> dict[str, float]:
    return {
        "beep_time": round(BEEP_TIME, 4),
        "stage_time_seconds": round(STAGE_TIME, 4),
        "pre_buffer_seconds": float(project.trim_pre_buffer_seconds),
        "post_buffer_seconds": float(project.trim_post_buffer_seconds),
    }


def _trim_keys(project: MatchProject, video: StageVideo, root: Path) -> tuple[str, str]:
    output = trimmed_video_path(root, STAGE, video, project=project)
    mp4_key = f"{SCOPE}/trimmed/{output.name}"
    params_key = f"{SCOPE}/trimmed/{output.stem}.params.json"
    return mp4_key, params_key


def test_local_mode_no_storage_runs_trim_and_never_touches_storage(
    tmp_path: Path, fake_trim: list[dict[str, object]]
) -> None:
    """Desktop / CLI mode: with no storage bound the cache helpers are
    no-ops and the existing ffmpeg trim path runs unchanged."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)

    assert project._storage is None
    output = _cut(root, project, video, source)

    assert output.exists()
    assert output.read_bytes() == b"MP4DATA"
    assert len(fake_trim) == 1


def test_storage_cache_hit_skips_trim(tmp_path: Path, fake_trim: list[dict[str, object]]) -> None:
    """A trim already in the storage cache (MP4 + matching params) is
    pulled and reused -- a cold worker skips the ffmpeg re-cut."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)

    mp4_key, params_key = _trim_keys(project, video, root)
    storage.write_bytes(mp4_key, b"PRECUT")
    storage.write_bytes(params_key, (json.dumps(_current_params(project)) + "\n").encode("utf-8"))

    output = _cut(root, project, video, source)

    assert output.read_bytes() == b"PRECUT"
    assert len(fake_trim) == 0  # never re-cut


def test_cold_cut_pushes_mp4_and_params_to_storage(
    tmp_path: Path, fake_trim: list[dict[str, object]]
) -> None:
    """Neither local nor storage has the trim: ffmpeg runs and both the
    MP4 and its params sidecar are pushed up for the next worker / API."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)

    output = _cut(root, project, video, source)

    assert output.read_bytes() == b"MP4DATA"
    assert len(fake_trim) == 1
    mp4_key, params_key = _trim_keys(project, video, root)
    assert storage.exists(mp4_key)
    assert storage.read_bytes(mp4_key) == b"MP4DATA"
    assert storage.exists(params_key)
    assert json.loads(storage.read_bytes(params_key)) == _current_params(project)


def test_local_cache_hit_skips_storage_and_trim(tmp_path: Path, fake_trim: list[dict[str, object]]) -> None:
    """A fresh local trim + matching params short-circuits before storage
    or ffmpeg are consulted -- repeated runs on one worker stay cheap."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)

    first = _cut(root, project, video, source)
    assert len(fake_trim) == 1

    second = _cut(root, project, video, source)
    assert second == first
    assert len(fake_trim) == 1  # unchanged


def test_storage_params_mismatch_recuts(tmp_path: Path, fake_trim: list[dict[str, object]]) -> None:
    """A storage trim cut for different params (stale beep / stage time)
    is pulled but rejected by the params check, so it is re-cut. This is
    the trim-specific case the WAV cache can't have (WAVs are name-keyed
    and trusted)."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)

    mp4_key, params_key = _trim_keys(project, video, root)
    storage.write_bytes(mp4_key, b"STALECUT")
    stale = _current_params(project) | {"beep_time": 99.0}
    storage.write_bytes(params_key, (json.dumps(stale) + "\n").encode("utf-8"))

    output = _cut(root, project, video, source)

    assert len(fake_trim) == 1  # re-cut because params didn't match
    assert output.read_bytes() == b"MP4DATA"


def test_storage_push_failure_does_not_break_cut(
    tmp_path: Path,
    fake_trim: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A push outage must not fail the trim job -- the local MP4 is the
    source of truth for the current job; the cache push is best-effort."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("simulated R2 outage")

    monkeypatch.setattr(storage, "upload_stream", boom)

    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)

    output = _cut(root, project, video, source)
    assert output.read_bytes() == b"MP4DATA"


def test_storage_pull_torn_file_falls_through_to_trim(
    tmp_path: Path,
    fake_trim: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If open_stream raises mid-copy the helper cleans up the torn MP4
    and lets ffmpeg run, rather than leaving a half-downloaded clip the
    next call would happily serve."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)
    mp4_key, params_key = _trim_keys(project, video, root)
    storage.write_bytes(mp4_key, b"PRECUT")
    storage.write_bytes(params_key, (json.dumps(_current_params(project)) + "\n").encode("utf-8"))

    def boom(path: str) -> object:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(storage, "open_stream", boom)

    output = _cut(root, project, video, source)

    assert output.read_bytes() == b"MP4DATA"
    assert len(fake_trim) == 1


def test_bind_storage_without_scope_disables_trim_cache(
    tmp_path: Path, fake_trim: list[dict[str, object]]
) -> None:
    """Storage bound but scope None (non-match request path): the trim
    cache stays off, just like the audio cache."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=None)

    _cut(root, project, video, source)

    assert len(fake_trim) == 1
    assert list(storage.list("")) == []


def test_trim_available_reports_storage_without_download(
    tmp_path: Path, fake_trim: list[dict[str, object]]
) -> None:
    """``trim_available`` sees a worker-pushed trim via a cheap exists()
    probe (no local copy, no download) so the beep-position metadata
    agrees with what the byte-serving path will produce."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)

    output = trimmed_video_path(root, STAGE, video, project=project)
    assert not output.exists()
    assert audio_helpers.trim_available(project, output) is False

    mp4_key, _ = _trim_keys(project, video, root)
    storage.write_bytes(mp4_key, b"PRECUT")

    # Still no local copy, but storage has it -> available, no download.
    assert audio_helpers.trim_available(project, output) is True
    assert not output.exists()


def _project_with_assigned_video(
    root: Path, video_path: Path, *, stage_numbers: tuple[int, ...] = (STAGE,)
) -> tuple[MatchProject, StageVideo]:
    """Project whose StageVideo(s) carry a stamped ``stage_number`` so
    ``video_id`` uses the post-take-spec "<path>#<stage>" hash. One
    StageVideo per entry in ``stage_numbers``, all sharing ``video_path``
    (two entries model a multi-stage single take). Returns the first."""
    project = MatchProject.init(root, name="legacy-id-test")
    project.stages = [
        StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=STAGE_TIME,
            videos=[StageVideo(path=video_path, role="primary", stage_number=n)],
        )
        for n in stage_numbers
    ]
    return project, project.stages[0].videos[0]


def _touch(path: Path, data: bytes = b"X") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _legacy_trim_path(root: Path, project: MatchProject, video: StageVideo) -> Path:
    new = trimmed_video_path(root, STAGE, video, project=project)
    return new.with_name(f"stage{STAGE}_cam_{audio_helpers.legacy_video_id(video)}_trimmed.mp4")


def test_pull_trimmed_video_prefers_new_keyed_file(tmp_path: Path) -> None:
    """A trim under the current video_id wins even when a legacy-keyed
    sibling also exists - the fallback only fires on a miss."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source)
    new = _touch(trimmed_video_path(root, STAGE, video, project=project), b"NEW")
    _touch(_legacy_trim_path(root, project, video), b"LEGACY")

    got = audio_helpers.pull_trimmed_video(root, STAGE, video, project=project)

    assert got == new
    assert got.read_bytes() == b"NEW"


def test_pull_trimmed_video_legacy_fallback_single_registration(tmp_path: Path) -> None:
    """Pre-take-spec trim (path-only id) is served read-only when the
    source path is registered on exactly one stage. Regression: the
    take-spec video_id change orphaned these caches and streaming 424d
    despite a valid trim on disk."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source)
    # Guard: the fixture must actually exercise the id divergence.
    assert video.video_id != audio_helpers.legacy_video_id(video)
    legacy = _touch(_legacy_trim_path(root, project, video), b"LEGACY")

    got = audio_helpers.pull_trimmed_video(root, STAGE, video, project=project)

    assert got == legacy
    assert got.read_bytes() == b"LEGACY"
    # The new-keyed name stays untouched - the fallback never writes.
    assert not trimmed_video_path(root, STAGE, video, project=project).exists()


def test_pull_trimmed_video_no_fallback_when_path_on_two_stages(tmp_path: Path) -> None:
    """Multi-stage single take: one source path on two stages means the
    legacy trim's window belongs to an unknown stage - no fallback, the
    (missing) new-keyed path is returned."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source, stage_numbers=(STAGE, STAGE + 1))
    _touch(_legacy_trim_path(root, project, video), b"LEGACY")

    got = audio_helpers.pull_trimmed_video(root, STAGE, video, project=project)

    assert got == trimmed_video_path(root, STAGE, video, project=project)
    assert not got.exists()


def test_pull_trimmed_video_neither_file_returns_missing_path(tmp_path: Path) -> None:
    """No new-keyed trim, no legacy trim: the resolver returns the
    (nonexistent) new-keyed path so callers fall through as before."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source)

    got = audio_helpers.pull_trimmed_video(root, STAGE, video, project=project)

    assert got == trimmed_video_path(root, STAGE, video, project=project)
    assert not got.exists()


def test_resolve_trim_for_read_shared_resolver_and_sidecar_pre_buffer(tmp_path: Path) -> None:
    """The resolver behind both stream_video and the beep anchor: None
    when nothing exists, the unambiguous legacy trim next, the new-keyed
    trim first once present. The pre-buffer comes from whichever file's
    sidecar the resolver returned so anchor and bytes agree."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source)

    assert audio_helpers.resolve_trim_for_read(root, STAGE, video, project=project) is None

    legacy = _touch(_legacy_trim_path(root, project, video), b"LEGACY")
    _touch(
        legacy.with_name(f"{legacy.stem}.params.json"),
        json.dumps({"pre_buffer_seconds": 3.0}).encode("utf-8"),
    )
    got = audio_helpers.resolve_trim_for_read(root, STAGE, video, project=project)
    assert got == legacy
    # Legacy trim was cut with pre_buffer 3.0, not the current 5.0.
    assert audio_helpers.trim_pre_buffer_seconds_for(got, default=5.0) == pytest.approx(3.0)

    new = _touch(trimmed_video_path(root, STAGE, video, project=project), b"NEW")
    got = audio_helpers.resolve_trim_for_read(root, STAGE, video, project=project)
    assert got == new
    # No sidecar beside the new-keyed trim -> default.
    assert audio_helpers.trim_pre_buffer_seconds_for(got, default=5.0) == pytest.approx(5.0)


def test_resolve_trim_for_read_ambiguous_registration_returns_none(tmp_path: Path) -> None:
    """Same path on two stages: the resolver refuses the legacy trim so
    the beep anchor stays source-based, matching the source clip
    stream_video will serve."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source, stage_numbers=(STAGE, STAGE + 1))
    _touch(_legacy_trim_path(root, project, video), b"LEGACY")

    assert audio_helpers.resolve_trim_for_read(root, STAGE, video, project=project) is None


def test_invalidate_video_audit_trim_sweeps_legacy_files(tmp_path: Path) -> None:
    """A beep/stage-time change must also drop the legacy-keyed trim +
    sidecar, or the read fallback would keep serving a stale cut."""
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_assigned_video(root, source)
    new = _touch(trimmed_video_path(root, STAGE, video, project=project), b"NEW")
    new_params = _touch(new.with_name(f"{new.stem}.params.json"))
    legacy = _touch(_legacy_trim_path(root, project, video), b"LEGACY")
    legacy_params = _touch(legacy.with_name(f"{legacy.stem}.params.json"))

    audio_helpers.invalidate_video_audit_trim(root, STAGE, video, project=project)

    for p in (new, new_params, legacy, legacy_params):
        assert not p.exists(), p


def test_ensure_video_audio_legacy_fallback_when_source_unplugged(tmp_path: Path) -> None:
    """WAV seam: only a legacy-keyed WAV exists and the source is gone
    (unplugged external storage) - the legacy WAV is served instead of
    raising FileNotFoundError, when the registration is unambiguous."""
    root = tmp_path / "p"
    source = tmp_path / "raw" / "gone.mov"  # never created
    project, video = _project_with_assigned_video(root, source)
    legacy = _touch(
        project.audio_path(root) / f"stage{STAGE}_cam_{audio_helpers.legacy_video_id(video)}.wav",
        b"WAVDATA",
    )

    got = audio_helpers.ensure_video_audio(root, STAGE, video, source, project=project)

    assert got == legacy
    assert got.read_bytes() == b"WAVDATA"


def test_ensure_video_audio_no_legacy_fallback_when_ambiguous(tmp_path: Path) -> None:
    """WAV seam ambiguity gate: same path on two stages means no
    fallback; with the source unreachable the pre-existing
    FileNotFoundError contract holds."""
    root = tmp_path / "p"
    source = tmp_path / "raw" / "gone.mov"  # never created
    project, video = _project_with_assigned_video(root, source, stage_numbers=(STAGE, STAGE + 1))
    _touch(
        project.audio_path(root) / f"stage{STAGE}_cam_{audio_helpers.legacy_video_id(video)}.wav",
        b"WAVDATA",
    )

    with pytest.raises(FileNotFoundError):
        audio_helpers.ensure_video_audio(root, STAGE, video, source, project=project)


def test_pull_trimmed_video_mirrors_from_storage(tmp_path: Path, fake_trim: list[dict[str, object]]) -> None:
    """The API serving path pulls a worker-pushed trim into the local
    cache and never invokes ffmpeg."""
    backing = tmp_path / "tenant"
    backing.mkdir()
    storage = FilesystemStorage(backing)
    root = tmp_path / "p"
    source = _make_source(tmp_path)
    project, video = _project_with_stage_video(root, source)
    project.bind_storage(storage, scope=SCOPE)
    mp4_key, _ = _trim_keys(project, video, root)
    storage.write_bytes(mp4_key, b"PRECUT")

    pulled = audio_helpers.pull_trimmed_video(root, STAGE, video, project=project)

    assert pulled.exists()
    assert pulled.read_bytes() == b"PRECUT"
    assert len(fake_trim) == 0
