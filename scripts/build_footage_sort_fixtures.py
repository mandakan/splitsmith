"""Build the footage-sort engine fixtures from real, hand-sorted matches.

Each fixture is the probed metadata of every clip (no media), every squad
shooter's scorecard times, and a label per clip taken from where the user
assigned it by hand in their match projects. The engine tests replay the
fixtures and compare proposals with the labels.

Two sources:

- ``--match <dir>``: a sorted match folder (``shooters/*/project.json``).
  Every assigned clip is labeled ``(shooter, stage)``. The clips' original
  folders are lost (the user curated them per subject), so ``folder`` is
  empty and cameras are keyed by device alone.
- ``--unsorted <dir> --project <dir> --scorecards <json>``: shared folders as
  received (one subfolder per club mate). Clips the user later assigned in
  ``--project`` are labeled; a phone clip in a folder named after its owner
  gets the constraint that it shows someone else. Scorecards come from a
  ``get_stage_times``-shaped JSON, since the project has only one shooter.

Needs ffprobe and the source volume mounted. Usage:

  uv run python scripts/build_footage_sort_fixtures.py \\
      --match /Volumes/X9/matches/blacksmith-handgun-open-2026 \\
      --out tests/fixtures/footage_sort/blacksmith-handgun-open-2026.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import unicodedata
from pathlib import Path

from splitsmith.match_project import VIDEO_EXTENSIONS, _heuristic_mount_from_make
from splitsmith.video_match import recording_start_from_tags


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    fmt = json.loads(out)["format"]
    tags = fmt.get("tags") or {}
    start = recording_start_from_tags(tags)
    lowered = {k.lower(): v for k, v in tags.items()}
    return {
        "start": start.isoformat() if start else None,
        "duration": round(float(fmt["duration"]), 3) if fmt.get("duration") else None,
        "make": lowered.get("com.apple.quicktime.make") or lowered.get("make"),
        "model": lowered.get("com.apple.quicktime.model") or lowered.get("model"),
    }


def _key(name: str) -> str:
    """Shooter key: lowercase first name, ASCII-folded (``Engström`` -> ``engstrom``)."""
    first = name.split()[0].lower()
    return unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode()


def _sorted_match(match_dir: Path) -> dict:
    shooters: list[dict] = []
    clips: dict[str, dict] = {}
    for pj in sorted(match_dir.glob("shooters/*/project.json")):
        project = json.loads(pj.read_text())
        name = project.get("competitor_name") or pj.parent.name
        key = _key(name)
        shooters.append(
            {
                "key": key,
                "name": name,
                "scorecards": {
                    str(s["stage_number"]): s["scorecard_updated_at"]
                    for s in project["stages"]
                    if s.get("scorecard_updated_at")
                },
            }
        )
        for stage in project["stages"]:
            for video in stage.get("videos", []):
                source = (pj.parent / video["path"]).resolve()
                if not source.exists():
                    continue
                entry = clips.setdefault(
                    str(source),
                    {
                        "id": source.name,
                        "folder": "",
                        "filename": source.name,
                        **_probe(source),
                        "labels": [],
                    },
                )
                entry["labels"].append({"shooter": key, "stage": stage["stage_number"]})
    return {"shooters": shooters, "clips": sorted(clips.values(), key=lambda c: c["filename"])}


def _unsorted(source_dir: Path, project_dir: Path, scorecards_json: Path) -> dict:
    sb = json.loads(scorecards_json.read_text())
    shooters = [
        {
            "key": _key(c["name"]),
            "name": c["name"],
            "scorecards": {str(s["stage_number"]): s["scorecard_updated_at"] for s in c["stages"]},
        }
        for c in sb["competitors"]
    ]
    keys = {s["key"] for s in shooters}
    project = json.loads(next(project_dir.glob("shooters/*/project.json")).read_text())
    owner = _key(project.get("competitor_name") or "")
    known = {
        Path(v["path"]).name: stage["stage_number"]
        for stage in project["stages"]
        for v in stage.get("videos", [])
    }
    clips: list[dict] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        folder = str(path.parent.relative_to(source_dir))
        entry = {"id": f"{folder}/{path.name}", "folder": folder, "filename": path.name, **_probe(path)}
        if path.name in known:
            entry["labels"] = [{"shooter": owner, "stage": known[path.name]}]
        # The owner's phone films the others (the user's account of these
        # folders); a head cam in the owner's folder is the owner's own.
        folder_owner = next((k for k in keys if folder.lower().endswith(k)), None)
        is_phone = _heuristic_mount_from_make(entry["make"]) == "hand"
        if folder_owner is not None and is_phone and "labels" not in entry:
            entry["not_shooter"] = folder_owner
        clips.append(entry)
    return {"shooters": shooters, "clips": clips}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--match", type=Path)
    parser.add_argument("--unsorted", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--scorecards", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.match:
        fixture = _sorted_match(args.match)
        fixture["source"] = args.match.name
    else:
        fixture = _unsorted(args.unsorted, args.project, args.scorecards)
        fixture["source"] = args.unsorted.name
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(fixture['clips'])} clips, {len(fixture['shooters'])} shooters")


if __name__ == "__main__":
    main()
