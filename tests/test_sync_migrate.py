from __future__ import annotations

from pathlib import Path

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.sync.auto_state import load_auto_prefs
from splitsmith.sync.migrate import CACHE_SCHEMA, migrate_legacy_caches
from splitsmith.ui.audio import trimmed_video_path, web_trim_available
from tests.media_bytes import tiny_mp4


def _match(root: Path, *, stages: list[int]) -> tuple[Path, MatchProject]:
    match = match_model.Match.init(root, name="M")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    sh = match_model.Match.shooter_root(root, "me")
    project = MatchProject.init(sh, name="M")
    project.stages = [
        StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=10.0,
            videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0, beep_reviewed=True)],
        )
        for n in stages
    ]
    project.save(sh)
    return sh, MatchProject.load(sh)


def _legacy_files(sh: Path, stage: int, video: StageVideo) -> list[Path]:
    t = sh / "trimmed"
    t.mkdir(exist_ok=True)
    lid = video.legacy_video_id
    files = [t / f"stage{stage}_cam_{lid}_trimmed.mp4", t / f"stage{stage}_cam_{lid}_web.mp4"]
    for f in files:
        f.write_bytes(tiny_mp4())
    (t / f"stage{stage}_cam_{lid}_trimmed.params.json").write_text("{}", encoding="utf-8")
    return files


def test_renames_a_legacy_trim_and_rendition_to_the_keys_hosted_reads(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1])
    video = project.stages[0].videos[0]
    _legacy_files(sh, 1, video)
    report = migrate_legacy_caches(tmp_path / "m")
    new = trimmed_video_path(sh, 1, video, project=project)
    assert new.exists() and new.with_name(new.name.replace("_trimmed.mp4", "_trimmed.params.json")).exists()
    assert web_trim_available(project, new)  # hosted's own lookup finds it now
    assert len(report.renamed) == 1 and report.ambiguous == []
    assert load_auto_prefs(tmp_path / "m").cache_schema == CACHE_SCHEMA


def test_leaves_a_multi_stage_single_take_alone(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1, 2])  # one source on two stages
    video = project.stages[0].videos[0]
    legacy = _legacy_files(sh, 1, video)
    report = migrate_legacy_caches(tmp_path / "m")
    assert all(f.exists() for f in legacy)
    assert report.renamed == [] and len(report.ambiguous) == 1


def test_a_second_run_does_nothing(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1])
    _legacy_files(sh, 1, project.stages[0].videos[0])
    migrate_legacy_caches(tmp_path / "m")
    assert migrate_legacy_caches(tmp_path / "m").renamed == []
