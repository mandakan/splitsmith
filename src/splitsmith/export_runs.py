"""The durable record of one export run (#629, second half).

An export's *files* are already discoverable from persistent state --
``MatchProject.export_overview`` and ``match_export_files`` list them, and
``download_export_file`` serves them (#858). Four things are not derivable
from a directory listing: which deliverables came out of one invocation,
how long that invocation took, which formats the user selected, and how
many anomalies it reported. This module is the shape of that record.

Pure: no I/O, no storage seam, no FastAPI. Persistence is the caller's
problem -- ``AppState.load_export_runs`` / ``save_export_runs`` pick
``state_docs`` or a local file, and the export job bodies do the writing.

**Reads never raise.** ``load_log`` drops an entry it cannot validate and
keeps the rest. An export must not fail, and a history page must not 500,
because a bookkeeping document is malformed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

#: What an artefact is, for the history row's icon + wording. ``trim`` is
#: the primary lossless cut; ``secondary_trim`` a per-cam one;
#: ``match_video`` the stitched match render when the run asked for mp4.
ArtifactKind = Literal[
    "trim",
    "secondary_trim",
    "csv",
    "fcpxml",
    "report",
    "overlay",
    "sidecar",
    "match_video",
]

RunKind = Literal["stage", "match"]

#: Fixed order for ``stage_run_formats`` -- the pipeline's own order, so
#: two runs asking for the same set always compare equal as lists.
_STAGE_FORMAT_ORDER = ("trim", "csv", "fcpxml", "report", "overlay")


class ExportArtifact(BaseModel):
    """One file a run produced.

    ``filename`` is a basename under the shooter's ``exports/`` dir, never
    a path: that is the key ``download_export_file`` takes, and it is what
    makes a record written by a hosted worker meaningful to the API
    container that serves the link.
    """

    filename: str
    kind: ArtifactKind


class ExportRun(BaseModel):
    """One export invocation.

    ``formats`` is what was *requested*; ``artifacts`` is what was
    *written*. Both are kept on purpose -- "asked for an overlay, got
    none" is exactly what a user comes back to the history to find out.

    ``duration_seconds`` is wall-clock time for the run. Note that
    ``match_exports.MatchExportResult.duration_seconds`` means something
    else entirely (the timeline length of the stitched output); do not
    wire that into this field.
    """

    run_id: str
    kind: RunKind
    finished_at: datetime
    duration_seconds: float
    stage_numbers: list[int]
    formats: list[str]
    anomaly_count: int
    artifacts: list[ExportArtifact] = Field(default_factory=list)


class ExportRunLog(BaseModel):
    """Every run for one shooter in one match, newest first."""

    schema_version: int = SCHEMA_VERSION
    runs: list[ExportRun] = Field(default_factory=list)


def new_run_id() -> str:
    """Unique id for one run.

    uuid4 hex, not a ULID: ordering comes from ``finished_at``, and the
    ulid package is a hosted-only extra while runs are recorded on slim
    local installs too. Same reasoning as ``server._new_event_id``.
    """
    return uuid.uuid4().hex


def load_log(doc: dict | None) -> ExportRunLog:
    """Parse a stored log, skipping entries that no longer validate.

    Never raises. A doc that is not a dict, or whose ``runs`` is not a
    list, yields an empty log; an individual malformed run is dropped and
    its siblings survive.
    """
    if not isinstance(doc, dict):
        return ExportRunLog()
    raw = doc.get("runs")
    if not isinstance(raw, list):
        return ExportRunLog()
    runs: list[ExportRun] = []
    for entry in raw:
        try:
            runs.append(ExportRun.model_validate(entry))
        except Exception:  # noqa: BLE001 -- a bad entry costs itself, nothing else
            continue
    version = doc.get("schema_version")
    return ExportRunLog(
        schema_version=version if isinstance(version, int) else SCHEMA_VERSION,
        runs=runs,
    )


def append_run(doc: dict | None, run: ExportRun) -> dict:
    """Return ``doc`` with ``run`` prepended, as a plain JSON-ready dict.

    Newest-first is the stored order, so a reader never sorts. No cap on
    the number of runs: the retention decision on #629 keeps run records
    indefinitely, and a run is a few hundred bytes.
    """
    log = load_log(doc)
    log.runs.insert(0, run)
    log.schema_version = SCHEMA_VERSION
    return log.model_dump(mode="json")


def stage_run_formats(*, trim: bool, csv: bool, fcpxml: bool, report: bool, overlay: bool) -> list[str]:
    """The formats a per-stage export requested, in pipeline order.

    Takes bare booleans rather than the request model so this module stays
    free of any dependency on the HTTP layer.
    """
    selected = {
        "trim": trim,
        "csv": csv,
        "fcpxml": fcpxml,
        "report": report,
        "overlay": overlay,
    }
    return [name for name in _STAGE_FORMAT_ORDER if selected[name]]


def match_run_formats(*, output_format: str, youtube_sidecar: bool) -> list[str]:
    """The formats a match export requested: its output format, plus the
    YouTube sidecar when one was asked for."""
    out = [output_format]
    if youtube_sidecar:
        out.append("youtube-sidecar")
    return out
