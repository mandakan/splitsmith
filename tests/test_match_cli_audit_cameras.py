"""``splitsmith match audit-cameras`` lists audits off their primary camera (#1496)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from splitsmith.cli import app
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from tests.conftest import scaffold_match, strip_ansi

runner = CliRunner()


def _match(tmp_path: Path, audits: dict[int, dict]) -> Path:
    """Stages with a head cam primary (beep unconfirmed) and a phone (beep
    confirmed). An audit's ``clip_video_id`` of ``"head"`` / ``"phone"``
    becomes that stage's real video id."""
    root, shooter_root = scaffold_match(tmp_path)
    project = MatchProject.load(shooter_root)

    def stage(n: int) -> StageEntry:
        return StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=8.0,
            videos=[
                StageVideo(path=Path(f"raw/HEAD{n}.mp4"), role="primary", beep_time=3.0),
                StageVideo(
                    path=Path(f"raw/PHONE{n}.MOV"),
                    role="secondary",
                    beep_time=19.2,
                    beep_reviewed=True,
                ),
            ],
        )

    project.stages = [stage(n) for n in sorted(audits)]
    project.save(shooter_root)
    project = MatchProject.load(shooter_root)
    audit_dir = project.audit_path(shooter_root)
    audit_dir.mkdir(parents=True, exist_ok=True)
    for n, doc in audits.items():
        ids = {v.path.name[:4].lower(): v.video_id for v in project.stage(n).videos}
        if "clip_video_id" in doc:
            doc = {**doc, "clip_video_id": ids[doc["clip_video_id"][:4]]}
        (audit_dir / f"stage{n}.json").write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_lists_stamped_and_suspect_audits_and_writes_nothing(tmp_path: Path) -> None:
    shots = [{"time": 6.0, "ms_after_beep": 1000}]
    root = _match(
        tmp_path,
        {
            1: {"beep_time": 5.0, "shots": shots},  # unstamped, primary beep unconfirmed: suspect
            2: {"beep_time": 5.0, "shots": shots, "clip_video_id": "phone"},  # on the phone
            3: {"beep_time": 5.0, "shots": shots, "clip_video_id": "head"},  # fine
            4: {"beep_time": 5.0, "shots": []},  # nothing to strand
        },
    )
    before = {p.name: p.read_text(encoding="utf-8") for p in root.rglob("stage*.json")}

    result = runner.invoke(app, ["match", "audit-cameras", str(root)])

    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "me stage 1: suspect, audit on PHONE1.MOV, primary HEAD1.mp4" in out
    assert "me stage 2: on another camera, audit on PHONE2.MOV, primary HEAD2.mp4" in out
    assert "stage 3" not in out and "stage 4" not in out
    assert "2 audit(s) to check" in out
    assert {p.name: p.read_text(encoding="utf-8") for p in root.rglob("stage*.json")} == before
