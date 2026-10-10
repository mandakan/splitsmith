"""Snap every secondary angle onto its stage's reviewed fixture (#1363).

Walks the match projects, finds each stage video that has no fixture on a
stage that has a reviewed one, and promotes it with the existing
promote-from-anchor engine (known beeps, guided snap). Every fixture it
writes is ``needs_review``: snapped, not audited.

Dry run by default: prints the plan and every skip with its reason. With
``--write`` it runs the ensemble and the snap per video and writes the
fixture, the clip WAV and the promotion report, except where too many
anchor shots found no onset on the secondary (``--max-missed``); those are
listed, not written. A summary lands in ``build/promote_secondary/``.

Usage:
    uv run python scripts/promote_secondary_angles.py [--matches DIR ...] [--write]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from splitsmith import beep_detect
from splitsmith.ensemble import fixtures as fx_module
from splitsmith.ensemble.api import load_ensemble_runtime
from splitsmith.fixture_schema import probe_camera_metadata, review_status
from splitsmith.lab.promote import PromoteFromAnchorRequest, promote_from_anchor, write_promoted_fixture
from splitsmith.lab.secondary import (
    AnchorFixture,
    AngleVideo,
    PlannedPromotion,
    StageAngles,
    camera_for,
    plan_secondary_promotions,
)
from splitsmith.match_project import MatchProject
from splitsmith.runtime import runtime as process_runtime
from splitsmith.ui import audio as audio_helpers

DEFAULT_MATCHES = Path("/Volumes/X9/matches")
OUT_DIR = Path("build/promote_secondary")


def _corpus(fixtures_dir: Path) -> list[AnchorFixture]:
    out = []
    for f in fx_module.all_fixtures(fixtures_dir):
        data = json.loads((fixtures_dir / f"{f.stem}.json").read_text())
        source = data.get("source_video") or data.get("source")
        if not source:
            continue
        out.append(AnchorFixture(f.stem, Path(source).name, review_status(data), f.n_audited_shots))
    return out


def _shooter_roots(match_dirs: list[Path]) -> list[tuple[str, Path]]:
    roots = []
    for m in match_dirs:
        shooters = m / "shooters"
        if not shooters.is_dir():
            continue
        for sh in sorted(p for p in shooters.iterdir() if (p / "project.json").exists()):
            roots.append((m.name, sh))
    return roots


def _trim(ffmpeg: str):
    def trim(src: Path, dst: Path, start: float, end: float) -> None:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src)]
        cmd += [
            "-t",
            f"{max(0.0, end - start):.3f}",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    return trim


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--matches", type=Path, nargs="*", help="match project dirs (default: all on X9)")
    ap.add_argument("--fixtures", type=Path, default=fx_module.FIXTURES_DIR)
    ap.add_argument("--write", action="store_true", help="promote; without it, only print the plan")
    ap.add_argument("--max-missed", type=float, default=0.25, help="skip when more anchor shots miss")
    args = ap.parse_args()

    match_dirs = args.matches or sorted(p for p in DEFAULT_MATCHES.iterdir() if p.is_dir())
    corpus = _corpus(args.fixtures)
    known = {a.source_name for a in corpus}

    stages: list[StageAngles] = []
    cameras = {}
    context = {}
    for match, root in _shooter_roots(match_dirs):
        project = MatchProject.load(root)
        for stage in project.stages:
            if len(stage.videos) < 2:
                continue
            angles = []
            for v in stage.videos:
                name = Path(v.path).name
                angles.append(
                    AngleVideo(v.video_id, name, v.beep_time, bool(v.beep_reviewed), v.camera_mount)
                )
                if name in known:
                    continue
                source = project.resolve_video_path(root, v.path)
                probe = probe_camera_metadata(source) if source.exists() else None
                cameras[v.video_id] = camera_for(name, v.camera_mount, probe) if probe else None
                context[v.video_id] = (project, root, stage.stage_number, v, source)
            stages.append(StageAngles(match, root.name, stage.stage_number, tuple(angles)))

    plans = plan_secondary_promotions(stages, corpus, cameras)
    todo = [p for p in plans if p.skip is None]
    print(f"{len(plans)} videos without a fixture on a stage with one; {len(todo)} to promote")
    for p in plans:
        what = p.skip or f"-> {p.slug}"
        where = f"{p.stage.match} {p.stage.shooter_slug} stage {p.stage.stage_number}"
        print(f"  {where} {p.video.file_name}: {what}")

    results = [_row(p, "planned" if p.skip is None else "skipped", p.skip) for p in plans]
    if args.write and todo:
        runtime = load_ensemble_runtime()
        ffmpeg = process_runtime().ffmpeg_binary
        results = [r for r in results if r["status"] == "skipped"]
        for p in todo:
            results.append(_promote(p, context[p.video.video_id], args, runtime, ffmpeg))
            print(f"  {p.slug}: {results[-1]['status']} {results[-1].get('reason') or ''}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / ("result.json" if args.write else "plan.json")
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


def _row(p: PlannedPromotion, status: str, reason: str | None, **extra) -> dict:
    return {
        "match": p.stage.match,
        "shooter": p.stage.shooter_slug,
        "stage": p.stage.stage_number,
        "video": p.video.file_name,
        "anchor": p.anchor_stem,
        "slug": p.slug,
        "camera": p.camera.id if p.camera else None,
        "status": status,
        "reason": reason,
        **extra,
    }


def _promote(p: PlannedPromotion, ctx, args, runtime, ffmpeg: str) -> dict:
    project, root, stage_number, video, source = ctx
    anchor_path = args.fixtures / f"{p.anchor_stem}.json"
    try:
        wav = audio_helpers.ensure_video_audio(
            root, stage_number, video, source, project=project, ffmpeg_binary=ffmpeg
        )
        anchor_audio, anchor_sr = beep_detect.load_audio(anchor_path.with_suffix(".wav"))
        audio, sr = beep_detect.load_audio(wav)
        result = promote_from_anchor(
            PromoteFromAnchorRequest(
                anchor_data=json.loads(anchor_path.read_text()),
                primary_audio=anchor_audio,
                primary_sr=anchor_sr,
                secondary_audio=audio,
                secondary_sr=sr,
                secondary_source_desc=f"raw/{source.name}",
                camera=p.camera,
                slug=p.slug,
                secondary_beep_time=float(video.beep_time),
            ),
            runtime=runtime,
        )
    except Exception as exc:  # noqa: BLE001 - one bad video must not stop the batch
        return _row(p, "failed", f"{type(exc).__name__}: {exc}")
    counts = result.promotion_report["counts"]
    missed = counts["missed"] / max(1, counts["anchor_shots"])
    if missed > args.max_missed:
        return _row(
            p,
            "skipped",
            f"{counts['missed']} of {counts['anchor_shots']} shots found no onset",
            counts=counts,
        )
    write_promoted_fixture(
        fixture_data=result.fixture_data,
        promotion_report=result.promotion_report,
        secondary_wav=wav,
        fixtures_root=args.fixtures,
        slug=p.slug,
        trim_wav=_trim(ffmpeg),
        source_video=str(source.resolve()),
    )
    return _row(p, "written", None, counts=counts)


if __name__ == "__main__":
    sys.exit(main())
