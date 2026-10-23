"""Stage a finished local match on a hosted server, for end-to-end benchmarks.

A benchmark replays a match that already has ground truth (which clip is
whose, which stage, the confirmed beeps) as if it were a new hosted match:

    manifest  read a local match folder into a media-free JSON manifest
    upload    put every clip in the account's raw pool once (idempotent)
    seed      create a fresh hosted match from the manifest and attach the
              already-uploaded clips; no bytes move

Raw uploads live in the account-level ``raw/`` pool and a match only refers
to them, so every run seeds a new match over the same objects. Proxies
(``raw_proxy/``) are per account too: they are made once, at upload time.

Authentication is a hosted session cookie, passed as ``--cookie`` or
``SPLITSMITH_BENCH_COOKIE`` (the value of ``splitsmith_session``).

    uv run python scripts/match_bench.py manifest /Volumes/X9/matches/hfo-masters-2026 -o hfo.json
    uv run python scripts/match_bench.py upload hfo.json --server https://my.staging.splitsmith.app
    uv run python scripts/match_bench.py seed hfo.json --server https://my.staging.splitsmith.app -o run.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

SESSION_COOKIE = "splitsmith_session"
PART_WORKERS = 4


class BenchStage(BaseModel):
    stage_number: int
    stage_name: str
    expected_rounds: int | None = None


class BenchShooter(BaseModel):
    key: str  # the local slug; hosted assigns its own
    name: str
    division: str | None = None
    competitor_id: int | None = None
    shooter_id: int | None = None
    stage_times: dict[int, float] = Field(default_factory=dict)


class BenchClip(BaseModel):
    filename: str  # basename in the raw pool, unique across the manifest
    source: Path  # resolved local file
    size_bytes: int
    shooter: str  # BenchShooter.key
    stages: list[int]
    role: str  # "primary" on at least one stage, else "secondary"
    camera_mount: str | None = None
    beeps: dict[int, float] = Field(default_factory=dict)  # stage -> beep_time, video-relative


class BenchManifest(BaseModel):
    name: str
    source_match_id: str
    scoreboard_match_id: str | None = None
    stages: list[BenchStage]
    shooters: list[BenchShooter]
    clips: list[BenchClip]

    @property
    def total_bytes(self) -> int:
        return sum(c.size_bytes for c in self.clips)


def build_manifest(match_dir: Path) -> BenchManifest:
    """Read a local match folder (``match.json`` + ``shooters/*/project.json``).

    Clips are ordered primaries first, so attaching them in order gives each
    stage the same primary it has locally (the first clip attached to a
    stage is promoted to primary).
    """
    match = json.loads((match_dir / "match.json").read_text())
    stages = [
        BenchStage(
            stage_number=s["stage_number"],
            stage_name=s["stage_name"],
            expected_rounds=(s.get("stage_rounds") or {}).get("expected"),
        )
        for s in match["stages"]
    ]
    shooters: list[BenchShooter] = []
    clips: dict[str, BenchClip] = {}
    for slug in match["shooters"]:
        shooter_dir = match_dir / "shooters" / slug
        project = json.loads((shooter_dir / "project.json").read_text())
        shooter = BenchShooter(
            key=slug,
            name=project.get("competitor_name") or project["name"],
            division=project.get("competitor_division"),
            competitor_id=project.get("selected_competitor_id"),
            shooter_id=project.get("selected_shooter_id"),
        )
        for stage in project["stages"]:
            number = stage["stage_number"]
            if stage.get("time_seconds") is not None:
                shooter.stage_times[number] = stage["time_seconds"]
            for video in stage.get("videos", []):
                local = shooter_dir / video["path"]
                name = Path(video["path"]).name
                clip = clips.get(name)
                if clip is None:
                    source = local.resolve()
                    clip = BenchClip(
                        filename=name,
                        source=source,
                        size_bytes=source.stat().st_size,
                        shooter=slug,
                        stages=[],
                        role="secondary",
                        camera_mount=video.get("camera_mount"),
                    )
                    clips[name] = clip
                elif clip.shooter != slug:
                    raise ValueError(
                        f"{name} belongs to both {clip.shooter} and {slug}; the raw pool needs unique names"
                    )
                clip.stages.append(number)
                if video.get("role") == "primary":
                    clip.role = "primary"
                if video.get("beep_time") is not None and video.get("beep_reviewed"):
                    clip.beeps[number] = video["beep_time"]
        shooters.append(shooter)
    ordered = sorted(
        clips.values(), key=lambda c: (c.role != "primary", c.shooter, min(c.stages), c.filename)
    )
    return BenchManifest(
        name=match["name"],
        source_match_id=match["match_id"],
        scoreboard_match_id=match.get("scoreboard_match_id"),
        stages=stages,
        shooters=shooters,
        clips=ordered,
    )


def _client(server: str, cookie: str | None) -> httpx.Client:
    cookie = cookie or os.environ.get("SPLITSMITH_BENCH_COOKIE")
    if not cookie:
        sys.exit("no session: pass --cookie or set SPLITSMITH_BENCH_COOKIE")
    return httpx.Client(base_url=server.rstrip("/"), cookies={SESSION_COOKIE: cookie}, timeout=120.0)


def _post(client: httpx.Client, path: str, body: dict) -> dict:
    resp = client.post(path, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"POST {path} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _upload_one(client: httpx.Client, clip: BenchClip) -> dict:
    started = time.monotonic()
    begun = _post(client, "/api/me/raw/upload/multipart/create", {"filename": clip.filename})
    upload_id, part_size = begun["upload_id"], int(begun["part_size"])
    count = -(-clip.size_bytes // part_size)

    def put_part(number: int) -> dict:
        url = _post(
            client,
            "/api/me/raw/upload/multipart/part-url",
            {"filename": clip.filename, "upload_id": upload_id, "part_number": number},
        )["url"]
        with clip.source.open("rb") as fh:
            fh.seek((number - 1) * part_size)
            data = fh.read(part_size)
        for attempt in range(4):
            try:
                resp = httpx.put(url, content=data, timeout=600.0)
                resp.raise_for_status()
                return {"part_number": number, "etag": resp.headers["ETag"]}
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        raise AssertionError("unreachable")

    try:
        with ThreadPoolExecutor(PART_WORKERS) as pool:
            parts = list(pool.map(put_part, range(1, count + 1)))
        _post(
            client,
            "/api/me/raw/upload/multipart/complete",
            {"filename": clip.filename, "upload_id": upload_id, "parts": parts},
        )
    except Exception:
        client.post(
            "/api/me/raw/upload/multipart/abort", json={"filename": clip.filename, "upload_id": upload_id}
        )
        raise
    seconds = time.monotonic() - started
    return {"filename": clip.filename, "bytes": clip.size_bytes, "seconds": round(seconds, 2)}


def cmd_manifest(args: argparse.Namespace) -> None:
    manifest = build_manifest(args.match_dir)
    args.output.write_text(manifest.model_dump_json(indent=2))
    print(
        f"{len(manifest.shooters)} shooters, {len(manifest.stages)} stages, {len(manifest.clips)} clips, "
        f"{manifest.total_bytes / 1e9:.1f} GB -> {args.output}"
    )


def cmd_upload(args: argparse.Namespace) -> None:
    manifest = BenchManifest.model_validate_json(args.manifest.read_text())
    log: list[dict] = []
    with _client(args.server, args.cookie) as client:
        resp = client.get("/api/me/raw/list")
        resp.raise_for_status()
        present = {u["filename"]: u["size"] for u in resp.json()["uploads"]}
        for clip in manifest.clips:
            if present.get(clip.filename) == clip.size_bytes:
                print(f"have {clip.filename}")
                continue
            entry = _upload_one(client, clip)
            log.append(entry)
            print(f"put  {clip.filename} {entry['bytes'] / 1e6:.0f} MB in {entry['seconds']:.0f} s")
    if log:
        total = sum(e["bytes"] for e in log)
        seconds = sum(e["seconds"] for e in log)
        print(f"uploaded {total / 1e9:.2f} GB in {seconds:.0f} s ({total * 8 / seconds / 1e6:.0f} Mbit/s)")
        if args.log:
            args.log.write_text(json.dumps({"server": args.server, "uploads": log}, indent=2))


def cmd_seed(args: argparse.Namespace) -> None:
    """Create a new match from the manifest; write the run record for the runner."""
    manifest = BenchManifest.model_validate_json(args.manifest.read_text())
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    first, rest = manifest.shooters[0], manifest.shooters[1:]
    with _client(args.server, args.cookie) as client:
        created = _post(
            client,
            "/api/match/create-manual",
            {
                "name": f"{manifest.name} bench {args.label or stamp}",
                "stages": [
                    {
                        "stage_number": s.stage_number,
                        "stage_name": s.stage_name,
                        "expected_rounds": s.expected_rounds,
                    }
                    for s in manifest.stages
                ],
                "primary_shooter": {"name": first.name, "division": first.division},
            },
        )
        match_id = created["match_id"]
        base = f"/api/matches/{match_id}"
        slugs = {first.key: created["default_shooter_slug"]}
        for shooter in rest:
            listed = _post(
                client,
                f"{base}/match/shooters",
                {
                    "name": shooter.name,
                    "division": shooter.division,
                    "selected_competitor_id": shooter.competitor_id,
                    "selected_shooter_id": shooter.shooter_id,
                },
            )
            slugs[shooter.key] = next(s["slug"] for s in listed["shooters"] if s["name"] == shooter.name)
        for shooter in manifest.shooters:
            for number, seconds in sorted(shooter.stage_times.items()):
                _post(
                    client,
                    f"{base}/shooters/{slugs[shooter.key]}/stages/{number}/time",
                    {"time_seconds": seconds},
                )
        for clip in manifest.clips:
            _post(
                client,
                f"{base}/shooters/{slugs[clip.shooter]}/raw-videos/attach",
                {"filename": clip.filename, "covers_stages": clip.stages, "size_bytes": clip.size_bytes},
            )
    record = {
        "server": args.server,
        "match_id": match_id,
        "label": args.label,
        "seeded_at": datetime.now(UTC).isoformat(),
        "slugs": slugs,
        "manifest": str(args.manifest),
    }
    args.output.write_text(json.dumps(record, indent=2))
    print(f"seeded {match_id} ({len(manifest.clips)} clips) -> {args.output}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("manifest", help="build a manifest from a local match folder")
    p.add_argument("match_dir", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.set_defaults(func=cmd_manifest)

    for name, func, help_text in (
        ("upload", cmd_upload, "upload every clip to the raw pool once"),
        ("seed", cmd_seed, "create a fresh hosted match from the manifest"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("manifest", type=Path)
        p.add_argument("--server", required=True)
        p.add_argument("--cookie")
        p.set_defaults(func=func)
        if name == "upload":
            p.add_argument("--log", type=Path, help="write per-file upload timings here")
        else:
            p.add_argument("-o", "--output", type=Path, required=True, help="run record for the runner")
            p.add_argument("--label", help="suffix for the match name (default: a timestamp)")

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
