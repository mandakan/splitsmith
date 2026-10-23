"""Stage a finished local match on a hosted server, for end-to-end benchmarks.

A benchmark replays a match that already has ground truth (which clip is
whose, which stage, the confirmed beeps) as if it were a new hosted match:

    manifest  read a local match folder into a media-free JSON manifest
    upload    put every clip in the account's raw pool once (idempotent)
    seed      create a fresh hosted match from the manifest and attach the
              already-uploaded clips; no bytes move
    run       confirm the manifest's beeps (which chains trim and shot
              detection), wait for quiet, render the compare grid, and write
              every job of the match with its timings
    report    per-phase, per-kind and per-worker figures and a cost estimate

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


def subset(manifest: BenchManifest, stages: list[int]) -> BenchManifest:
    """The manifest cut to ``stages``; a clip covering none of them is dropped."""
    keep = set(stages)
    clips = []
    for clip in manifest.clips:
        covered = [n for n in clip.stages if n in keep]
        if covered:
            clips.append(
                clip.model_copy(
                    update={"stages": covered, "beeps": {n: b for n, b in clip.beeps.items() if n in keep}}
                )
            )
    shooters = [
        s.model_copy(update={"stage_times": {n: t for n, t in s.stage_times.items() if n in keep}})
        for s in manifest.shooters
    ]
    return manifest.model_copy(
        update={
            "stages": [s for s in manifest.stages if s.stage_number in keep],
            "shooters": shooters,
            "clips": clips,
        }
    )


def cmd_manifest(args: argparse.Namespace) -> None:
    manifest = build_manifest(args.match_dir)
    if args.stages:
        manifest = subset(manifest, args.stages)
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


TERMINAL = {"succeeded", "failed", "cancelled"}
RUN_PHASES = ("settle", "detect", "beeps", "grid")


class _JobWatch:
    """Polls the account's jobs and keeps the last snapshot of each of this match's."""

    def __init__(self, client: httpx.Client, match_id: str, interval: float) -> None:
        self._client = client
        self._match_id = match_id
        self._interval = interval
        self.jobs: dict[str, dict] = {}

    def poll(self) -> list[dict]:
        resp = self._client.get("/api/me/jobs")
        resp.raise_for_status()
        mine = [j for j in resp.json() if j.get("match_id") == self._match_id]
        for job in mine:
            self.jobs[job["id"]] = job
        return [j for j in mine if j["status"] not in TERMINAL]

    def wait_quiet(self, label: str, *, settle: float, timeout: float) -> float:
        """Block until no job of this match is active for ``settle`` seconds.

        A beep confirm chains trim then detection, so a quiet moment between
        two links is not the end; ``settle`` covers the gap.
        """
        started = time.monotonic()
        quiet_since: float | None = None
        while True:
            active = self.poll()
            now = time.monotonic()
            if active:
                quiet_since = None
                kinds = sorted({j["kind"] for j in active})
                print(
                    f"  [{label}] {now - started:6.0f} s  {len(active)} active: {', '.join(kinds)}",
                    flush=True,
                )
            elif quiet_since is None:
                quiet_since = now
            elif now - quiet_since >= settle:
                return quiet_since - started
            if now - started > timeout:
                raise TimeoutError(f"{label}: still busy after {timeout:.0f} s")
            time.sleep(self._interval)


def cmd_run(args: argparse.Namespace) -> None:
    """Drive a seeded match through the selected phases, in pipeline order.

    Phases: ``settle`` (wait out what attaching started), ``detect`` (beep
    detection on every clip; on a primary, trim and shot detection chain
    from a trusted result), ``beeps`` (confirm or correct each beep against
    the manifest; a correction re-cuts and re-detects) and ``grid`` (render
    the compare grid). Running only some of them reruns that
    part on a match an earlier run already took further, e.g. ``--phases
    grid`` to compare renders across workers; only jobs created during this
    run are reported.
    """
    record = json.loads(args.run.read_text())
    manifest = BenchManifest.model_validate_json(Path(record["manifest"]).read_text())
    base = f"/api/matches/{record['match_id']}"
    slugs: dict[str, str] = record["slugs"]
    phases: list[dict] = []
    ran_at = datetime.now(UTC).isoformat()
    with _client(record["server"], args.cookie) as client:
        watch = _JobWatch(client, record["match_id"], args.interval)

        def phase(name: str, action) -> None:
            t0 = time.monotonic()
            began = datetime.now(UTC).isoformat()
            action()
            busy = watch.wait_quiet(name, settle=args.settle, timeout=args.timeout)
            phases.append(
                {"name": name, "began": began, "wall_s": round(time.monotonic() - t0, 1), "busy_s": busy}
            )
            print(f"{name}: {busy:.0f} s until quiet", flush=True)

        if "settle" in args.phases:
            phase("settle", lambda: None)

        def videos() -> list[tuple[str, BenchClip, int, dict]]:
            """(slug, clip, stage, the hosted video view) for every manifest clip-stage."""
            found = []
            for shooter in manifest.shooters:
                slug = slugs[shooter.key]
                view = client.get(f"{base}/shooters/{slug}/project")
                view.raise_for_status()
                by_key = {
                    (stage["stage_number"], Path(video["path"]).name): video
                    for stage in view.json()["stages"]
                    for video in stage.get("videos", [])
                }
                for clip in (c for c in manifest.clips if c.shooter == shooter.key):
                    for number in clip.stages:
                        found.append((slug, clip, number, by_key[(number, clip.filename)]))
            return found

        def video_path(slug: str, number: int, video: dict) -> str:
            return f"{base}/shooters/{slug}/stages/{number}/videos/{video['video_id']}"

        def detect_beeps() -> None:
            for slug, _clip, number, video in videos():
                _post(client, f"{video_path(slug, number, video)}/detect-beep", {})

        corrections: list[dict] = []

        def confirm_beeps() -> None:
            # As a user would: confirm a detected beep that matches the ground
            # truth, correct one that does not. Re-setting a matching beep would
            # re-cut the trim and re-run detection the detect phase already ran.
            for slug, clip, number, video in videos():
                truth = clip.beeps.get(number)
                if truth is None:
                    continue
                path = video_path(slug, number, video)
                detected = video.get("beep_time")
                wrong = detected is None or abs(detected - truth) > args.beep_tolerance
                if wrong:
                    corrections.append(
                        {"clip": clip.filename, "stage": number, "detected": detected, "truth": truth}
                    )
                    _post(client, f"{path}/beep", {"beep_time": truth})
                if wrong or not video.get("beep_reviewed"):
                    _post(client, f"{path}/beep/review", {"reviewed": True})

        if "detect" in args.phases:
            phase("detect", detect_beeps)
        if "beeps" in args.phases:
            phase("beeps", confirm_beeps)

        if "grid" in args.phases:
            body = {
                "stage_numbers": [s.stage_number for s in manifest.stages],
                "audio_from": slugs[manifest.shooters[0].key],
                **(json.loads(args.grid_options.read_text()) if args.grid_options else {}),
            }
            phase("grid", lambda: _post(client, f"{base}/match/compare-export", body))
        watch.poll()
    since = datetime.fromisoformat(ran_at)
    jobs = [j for j in watch.jobs.values() if datetime.fromisoformat(j["created_at"]) >= since]
    failed = [j for j in jobs if j["status"] == "failed"]
    result = {**record, "ran_at": ran_at, "phases": phases, "beep_corrections": corrections, "jobs": jobs}
    args.output.write_text(json.dumps(result, indent=2))
    print(f"{len(jobs)} jobs, {len(failed)} failed -> {args.output}")
    for job in failed:
        where = f"{job.get('shooter_slug')} stage {job.get('stage_number')}"
        print(f"  FAILED {job['kind']} {where}: {job['error']}")


def summarize(result: dict, *, vcpu_s: float, gib_s: float, egress_gb: float) -> dict:
    """Per-kind and per-worker figures from a run result, plus a cost estimate.

    A worker's billed span runs from its process start (first job's start
    minus its ``uptime_at_start_ms``) to its last job's end: that is what a
    one-shot Cloud Run execution pays for, idle tail excluded. Egress counts
    every byte a worker wrote to storage; on the self-hosted agent the rates
    should be zero.
    """
    jobs = [j for j in result["jobs"] if j.get("timings")]
    kinds: dict[str, dict] = {}
    for job in jobs:
        k = kinds.setdefault(job["kind"], {"jobs": 0, "busy_s": 0.0, "max_s": 0.0, "queue_s": 0.0})
        seconds = job["timings"]["total_ms"] / 1000.0
        k["jobs"] += 1
        k["busy_s"] += seconds
        k["max_s"] = max(k["max_s"], seconds)
        k["queue_s"] += (job["timings"].get("queue_wait_ms") or 0.0) / 1000.0
    workers: dict[str, dict] = {}
    for job in sorted(jobs, key=lambda j: j["started_at"] or ""):
        facts = job["timings"].get("worker")
        if not facts or not job.get("started_at") or not job.get("finished_at"):
            continue
        started = datetime.fromisoformat(job["started_at"]).timestamp()
        finished = datetime.fromisoformat(job["finished_at"]).timestamp()
        w = workers.get(facts["name"])
        if w is None:
            w = workers[facts["name"]] = {
                "vcpu": facts.get("vcpu"),
                "memory_mb": facts.get("memory_mb"),
                "startup_s": facts["uptime_at_start_ms"] / 1000.0,
                "process_start": started - facts["uptime_at_start_ms"] / 1000.0,
                "last_end": finished,
                "jobs": 0,
                "busy_s": 0.0,
                "read_bytes": 0,
                "written_bytes": 0,
                "peak_rss_mb": 0.0,
                "peak_child_rss_mb": 0.0,
                "overlapped_jobs": 0,
            }
        w["last_end"] = max(w["last_end"], finished)
        w["jobs"] += 1
        w["busy_s"] += job["timings"]["total_ms"] / 1000.0
        w["read_bytes"] += facts.get("storage_read_bytes") or 0
        w["written_bytes"] += facts.get("storage_written_bytes") or 0
        w["peak_rss_mb"] = max(w["peak_rss_mb"], facts.get("peak_rss_mb") or 0.0)
        w["peak_child_rss_mb"] = max(w["peak_child_rss_mb"], facts.get("peak_child_rss_mb") or 0.0)
        w["overlapped_jobs"] += 0 if facts.get("ran_alone") else 1
    total_cost = 0.0
    for w in workers.values():
        w["span_s"] = w.pop("last_end") - w.pop("process_start")
        compute = w["span_s"] * ((w["vcpu"] or 0) * vcpu_s + (w["memory_mb"] or 0) / 1024 * gib_s)
        egress = w["written_bytes"] / 1e9 * egress_gb
        w["cost_compute"] = round(compute, 4)
        w["cost_egress"] = round(egress, 4)
        total_cost += compute + egress
    return {
        "label": result.get("label"),
        "match_id": result["match_id"],
        "phases": result["phases"],
        "kinds": kinds,
        "workers": workers,
        "cost_total": round(total_cost, 4),
        "failed": sum(1 for j in result["jobs"] if j["status"] == "failed"),
    }


def cmd_report(args: argparse.Namespace) -> None:
    summaries = [
        summarize(
            json.loads(path.read_text()), vcpu_s=args.vcpu_s, gib_s=args.gib_s, egress_gb=args.egress_gb
        )
        for path in args.results
    ]
    for s in summaries:
        title = s["label"] or s["match_id"]
        print(f"== {title}  (failed jobs: {s['failed']}, est. cost ${s['cost_total']:.2f})")
        for phase in s["phases"]:
            print(f"   phase {phase['name']:<8} {phase['busy_s']:8.0f} s until quiet")
        print(f"   {'kind':<16} {'jobs':>5} {'busy s':>9} {'max s':>8} {'queued s':>9}")
        for kind, k in sorted(s["kinds"].items(), key=lambda kv: -kv[1]["busy_s"]):
            print(f"   {kind:<16} {k['jobs']:>5} {k['busy_s']:>9.0f} {k['max_s']:>8.1f} {k['queue_s']:>9.0f}")
        for name, w in s["workers"].items():
            print(
                f"   worker {name}: {w['vcpu']} vCPU / {w['memory_mb']} MB, {w['jobs']} jobs, "
                f"span {w['span_s']:.0f} s (startup {w['startup_s']:.0f} s, busy {w['busy_s']:.0f} s), "
                f"read {w['read_bytes'] / 1e9:.2f} GB, wrote {w['written_bytes'] / 1e9:.2f} GB, "
                f"peak {w['peak_rss_mb']:.0f} MB (+child {w['peak_child_rss_mb']:.0f}), "
                f"overlapped {w['overlapped_jobs']}, "
                f"${w['cost_compute']:.3f} + ${w['cost_egress']:.3f} egress"
            )
    if args.json:
        args.json.write_text(json.dumps(summaries, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("manifest", help="build a manifest from a local match folder")
    p.add_argument("match_dir", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--stages", type=int, nargs="+", help="keep only these stages (a dry run)")
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("report", help="per-phase, per-kind and per-worker figures with a cost estimate")
    p.add_argument("results", type=Path, nargs="+", help="result files run wrote")
    # Defaults are approximate Cloud Run (europe-west1) and internet-egress list prices; pass
    # current ones, and zeros for a self-hosted worker.
    p.add_argument("--vcpu-s", type=float, default=0.000018, help="price per vCPU-second")
    p.add_argument("--gib-s", type=float, default=0.000002, help="price per GiB-second")
    p.add_argument("--egress-gb", type=float, default=0.12, help="price per GB written to storage")
    p.add_argument("--json", type=Path, help="also write the summaries here")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("run", help="confirm beeps, wait for trim and detection, render the grid")
    p.add_argument("run", type=Path, help="the run record seed wrote")
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--cookie")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between job polls")
    p.add_argument("--settle", type=float, default=30.0, help="quiet seconds that end a phase")
    p.add_argument("--timeout", type=float, default=4 * 3600.0, help="seconds before a phase gives up")
    p.add_argument(
        "--phases", nargs="+", choices=RUN_PHASES, default=list(RUN_PHASES), help="which phases to run"
    )
    p.add_argument("--grid-options", type=Path, help="JSON merged into the compare-export body")
    p.add_argument(
        "--beep-tolerance",
        type=float,
        default=0.02,
        help="seconds a detected beep may be off and still confirm",
    )
    p.set_defaults(func=cmd_run)

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
