"""How long an export has left (spec 2026-10-25).

Pure functions over an export's jobs and their parts (``Job.parts``). A
part's planned length comes from the user's finished history, as seconds
of work per second of footage scaled by the part's footage, or, with no
history, from a built-in table seeded from the staging benchmarks (run G4,
1080p basic grid stages 173 to 400 s; 4K stages up to 11 min). Running
parts go by their own rate once they have one.

Time left follows the export's shape: a grid's stage jobs run side by
side, so its wait part counts the longest of them; the parts of one job
run in turn; a job queued after the lead (the chained YouTube upload)
runs after it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from pydantic import BaseModel

from .ui.jobs import EXPORT_LEAD_KINDS, Job, JobPart, JobStatus

#: Seconds a part takes with no history, by (part kind, profile). Fixed
#: seconds, not per footage-second: the benchmarks never recorded footage
#: per stage (plan ruling).
BUILTIN_PART_SECONDS: dict[tuple[str, str | None], float] = {
    # A queued stage job, before it knows its canvas.
    ("grid_stage", None): 300.0,
    ("grid_stage", "1080-basic"): 300.0,
    ("grid_stage", "1080-dressed"): 480.0,
    ("grid_stage", "2160-basic"): 420.0,
    ("grid_stage", "2160-dressed"): 700.0,
    ("stitch", None): 100.0,
    ("stitch", "1080-basic"): 100.0,
    ("stitch", "1080-dressed"): 100.0,
    ("stitch", "2160-basic"): 270.0,
    ("stitch", "2160-dressed"): 270.0,
    ("load", None): 20.0,
    ("stage_export", None): 60.0,
    ("compose", None): 120.0,
    ("upload", None): 30.0,
    ("upload", "1080-basic"): 30.0,
    ("upload", "1080-dressed"): 30.0,
    ("upload", "2160-basic"): 90.0,
    ("upload", "2160-dressed"): 90.0,
    ("reel", None): 180.0,
    ("youtube", None): 60.0,
}
DEFAULT_PART_SECONDS = 60.0
#: A hosted job still queued waits about this long for a worker to boot.
WORKER_BOOT_S = 60.0
#: A running part's own rate is trusted once it is this far along. A grid
#: stage's clock starts at its tile download, before ffmpeg's first fraction,
#: so at 5% the rate overshot by double (staging run G6).
MIN_RATE_PROGRESS = 0.15

_DONE = ("done", "skipped", "failed")
_ENDED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class PartEstimate(BaseModel):
    job_id: str
    key: str
    remaining_s: float
    overrun: bool


class ExportEstimate(BaseModel):
    time_left_s: float
    finish_at: datetime
    approximate: bool
    parts: list[PartEstimate]


def history_rates(finished: list[Job]) -> dict[tuple[str, str | None], float]:
    """Seconds of work per second of footage, the median over every done
    part with footage and both times, keyed by (part kind, profile)."""
    samples: dict[tuple[str, str | None], list[float]] = {}
    for job in finished:
        for part in job.parts or ():
            if part.status != "done" or not part.footage_s or part.started_at is None:
                continue
            if part.finished_at is None:
                continue
            seconds = (part.finished_at - part.started_at).total_seconds()
            if seconds > 0:
                samples.setdefault((part.kind, part.profile), []).append(seconds / part.footage_s)
    return {key: median(values) for key, values in samples.items()}


def parts_of(job: Job) -> list[JobPart]:
    """A job's parts, or what a job that published none yet stands for: a
    queued grid job is its plan, its wait for the stage jobs and its join; a
    queued stage job is a grid stage; any other job is one part of its kind."""
    if job.parts:
        return list(job.parts)
    if job.kind == "compare-grid" and job.status == JobStatus.PENDING:
        return [
            JobPart(key="plan", label="Planning", kind="load"),
            JobPart(key="stages", label="Stage renders", kind="wait"),
            JobPart(key="stitch", label="Joining", kind="stitch"),
        ]
    status = {
        JobStatus.PENDING: "waiting",
        JobStatus.RUNNING: "running",
        JobStatus.SUCCEEDED: "done",
    }.get(job.status, "failed")
    kind = "grid_stage" if job.kind == "compare-grid-stage" else job.kind
    return [JobPart(key="job", label=job.kind, kind=kind, status=status, started_at=job.started_at)]  # type: ignore[arg-type]


def _planned(part: JobPart, history: dict[tuple[str, str | None], float]) -> tuple[float, bool]:
    """A part's planned seconds, and whether it came from the built-in table."""
    rate = history.get((part.kind, part.profile))
    if rate is not None and part.footage_s:
        return rate * part.footage_s, False
    builtin = BUILTIN_PART_SECONDS.get((part.kind, part.profile))
    if builtin is None:
        builtin = BUILTIN_PART_SECONDS.get((part.kind, None), DEFAULT_PART_SECONDS)
    return builtin, True


def _remaining(
    part: JobPart, job: Job, history: dict[tuple[str, str | None], float], now: datetime
) -> tuple[float, bool, bool]:
    """(seconds left, overrun, used a built-in) for one part."""
    if part.status in _DONE or job.status in _ENDED:
        return 0.0, False, False
    planned, builtin = _planned(part, history)
    if part.status == "waiting":
        return planned, False, builtin
    started = part.started_at or job.started_at
    elapsed = (now - started).total_seconds() if started is not None else 0.0
    progress = part.progress or 0.0
    if progress >= MIN_RATE_PROGRESS and elapsed > 0:
        return elapsed * (1.0 - progress) / progress, False, False
    return max(0.0, planned - elapsed), elapsed > planned, builtin


def estimate_export(
    jobs: list[Job],
    history: dict[tuple[str, str | None], float],
    *,
    now: datetime,
    hosted: bool,
) -> ExportEstimate:
    """Time left for one export's jobs (spec 2026-10-25, plan Task 5)."""
    estimates: list[PartEstimate] = []
    approximate = False
    job_left: dict[str, float] = {}
    wait_keys: dict[str, list[str]] = {}
    for job in jobs:
        total = WORKER_BOOT_S if hosted and job.status == JobStatus.PENDING else 0.0
        for part in parts_of(job):
            seconds, overrun, builtin = _remaining(part, job, history, now)
            if part.kind == "wait":
                wait_keys.setdefault(job.id, []).append(part.key)
                seconds = 0.0  # stands for the siblings, counted below
            approximate = approximate or (builtin and seconds > 0)
            estimates.append(PartEstimate(job_id=job.id, key=part.key, remaining_s=seconds, overrun=overrun))
            total += seconds
        job_left[job.id] = total

    ordered = sorted(jobs, key=lambda j: j.created_at)
    leads = [j for j in ordered if j.kind in EXPORT_LEAD_KINDS]
    lead = leads[-1] if leads else None  # a re-run grid leads its export
    if lead is None:
        time_left = max(job_left.values(), default=0.0)
    else:
        siblings = [j for j in ordered if j is not lead and j.created_at <= lead.created_at]
        followers = [j for j in ordered if j is not lead and j.created_at > lead.created_at]
        longest_sibling = max((job_left[j.id] for j in siblings), default=0.0)
        lead_parts = {p.key: p for p in parts_of(lead)}
        waiting_on_siblings = any(lead_parts[k].status not in _DONE for k in wait_keys.get(lead.id, ()))
        if waiting_on_siblings:
            # Stages the lead renders itself run beside the stage jobs.
            beside = sum(
                e.remaining_s
                for e, p in zip([e for e in estimates if e.job_id == lead.id], parts_of(lead), strict=True)
                if p.kind == "grid_stage"
            )
            through_lead = job_left[lead.id] - beside + max(beside, longest_sibling)
        else:
            through_lead = max(job_left[lead.id], longest_sibling)
        time_left = through_lead + sum(job_left[j.id] for j in followers)

    return ExportEstimate(
        time_left_s=time_left,
        finish_at=now + timedelta(seconds=time_left),
        approximate=approximate,
        parts=estimates,
    )
