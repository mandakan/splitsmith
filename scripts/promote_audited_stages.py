"""Promote audited multi-angle stages that have no fixture yet (#1363).

The lab's Promote button (``/api/lab/promote``) without a server: for every
stage with more than one angle, no fixture on any of them, a reviewed
primary beep and an audit saved in the app, copy the audit JSON and its
audit WAV into ``tests/fixtures/`` as a reviewed fixture. Its other angles
can then be snapped onto it with ``scripts/promote_secondary_angles.py``.

Dry run by default; ``--write`` promotes.

Usage:
    uv run python scripts/promote_audited_stages.py [--matches DIR ...] [--write]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from splitsmith import lab as lab_module
from splitsmith.ensemble import fixtures as fx_module
from splitsmith.fixture_schema import REVIEW_NEEDED, probe_camera_metadata
from splitsmith.lab.secondary import camera_for
from splitsmith.match_project import MatchProject
from splitsmith.runtime import runtime as process_runtime
from splitsmith.ui import audio as audio_helpers

DEFAULT_MATCHES = Path("/Volumes/X9/matches")


def _known_sources(fixtures_dir: Path) -> set[str]:
    names = set()
    for f in fx_module.all_fixtures(fixtures_dir):
        data = json.loads((fixtures_dir / f"{f.stem}.json").read_text())
        for key in ("source_video", "source"):
            if data.get(key):
                names.add(Path(data[key]).name)
    return names


def _match_slug(match_dir: Path, project: MatchProject) -> str:
    name = match_dir.name
    if re.search(r"-\d{4}$", name):
        return name
    when = project.match_date or next(
        (s.scorecard_updated_at for s in project.stages if s.scorecard_updated_at), None
    )
    year = str(when)[:4] if when else None
    return f"{name}-{year}" if year else name


def _audited(audit: Path) -> bool:
    if not audit.exists():
        return False
    doc = json.loads(audit.read_text())
    saved = any(e.get("kind") == "save" for e in doc.get("audit_events") or [])
    return bool(doc.get("shots")) and saved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--matches", type=Path, nargs="*")
    ap.add_argument("--fixtures", type=Path, default=fx_module.FIXTURES_DIR)
    ap.add_argument("--write", action="store_true")
    ap.add_argument(
        "--needs-review",
        action="append",
        default=[],
        metavar="MATCH/SHOOTER",
        help=(
            "a shooter whose audit was heard on another angle than the primary "
            "(e.g. hostfinalen-xi/s_8905a77e): their fixtures land as needs_review"
        ),
    )
    ap.add_argument("--note", default="audited by ear on another angle, placed on this one")
    args = ap.parse_args()

    match_dirs = args.matches or sorted(p for p in DEFAULT_MATCHES.iterdir() if p.is_dir())
    known = _known_sources(args.fixtures)
    ffmpeg = process_runtime().ffmpeg_binary
    n_ok = 0
    for m in match_dirs:
        shooters = m / "shooters"
        if not shooters.is_dir():
            continue
        for root in sorted(p for p in shooters.iterdir() if (p / "project.json").exists()):
            project = MatchProject.load(root)
            for stage in project.stages:
                if len(stage.videos) < 2 or any(Path(v.path).name in known for v in stage.videos):
                    continue
                where = f"{m.name} {root.name} stage {stage.stage_number}"
                primary = stage.primary()
                audit = project.audit_path(root) / f"stage{stage.stage_number}.json"
                skip = None
                if primary is None or primary.beep_time is None or not primary.beep_reviewed:
                    skip = "primary beep not reviewed"
                elif not _audited(audit):
                    skip = "no saved audit"
                elif project.selected_shooter_id is None:
                    skip = "no SSI shooter pinned"
                elif stage.time_seconds is None:
                    skip = "no stage time"
                source = project.resolve_video_path(root, primary.path) if primary else None
                camera = None
                if skip is None:
                    probe = probe_camera_metadata(source)
                    camera = camera_for(
                        Path(primary.path).name,
                        primary.camera_mount,
                        probe,
                        make=primary.camera_make,
                        model=primary.camera_model,
                    )
                    if camera is None:
                        skip = f"camera not recognised for {Path(primary.path).name}"
                token = (
                    lab_module.shooter_token(project.selected_shooter_id)
                    if project.selected_shooter_id
                    else "?"
                )
                slug = f"stage-shots-{_match_slug(m, project)}-stage{stage.stage_number}-{token}"
                if skip:
                    print(f"  {where}: skip ({skip})")
                    continue
                flagged = f"{m.name}/{root.name}" in args.needs_review
                status = "needs review" if flagged else "reviewed"
                print(f"  {where}: {Path(primary.path).name} -> {slug} ({camera.id}, {status})")
                n_ok += 1
                if not args.write:
                    continue
                audit_audio = audio_helpers.ensure_audit_audio(
                    root,
                    stage.stage_number,
                    lambda source=source: source,
                    primary.beep_time,
                    project=project,
                    ffmpeg_binary=ffmpeg,
                )
                start = max(0.0, float(primary.beep_time) - float(project.trim_pre_buffer_seconds))
                end = (
                    float(primary.beep_time)
                    + float(stage.time_seconds)
                    + float(project.trim_post_buffer_seconds)
                )
                lab_module.promote_stage_to_fixture(
                    lab_module.PromoteRequest(
                        audit_json_path=audit,
                        audit_wav_path=audit_audio.audio_path,
                        fixture_slug=slug,
                        fixtures_root=args.fixtures,
                        extra_metadata={"stage_number": stage.stage_number, "stage_name": stage.stage_name},
                        shooter={"id": token},
                        source_video=source,
                        fixture_window_in_source=(start, end),
                        camera=camera.model_dump(mode="json"),
                    )
                )
                if flagged:
                    path = args.fixtures / f"{slug}.json"
                    data = json.loads(path.read_text())
                    data["review"] = {
                        "status": REVIEW_NEEDED,
                        "derived_from": None,
                        "reviewed_at": None,
                        "note": args.note,
                    }
                    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
    print(f"{n_ok} stage(s) {'promoted' if args.write else 'to promote'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
