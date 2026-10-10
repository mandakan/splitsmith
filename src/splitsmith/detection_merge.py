"""How a shot-detection run folds into a stage's audit document.

One rule for every detection writer (#1380): the server's shot-detect job
(``ui/server.py``) and the MCP ``detect_shots`` tool (``mcp/detect_tools``)
both call :func:`merge_detection_into`, so a ``reset`` re-detect drops the
seeder's ``auto`` stage events and ``events_seeded``, logs ``marker_deleted``
and ``events_reset``, and numbers candidates past the stage's high-water mark
whichever surface ran it. Pure: no I/O and no lock; the caller loads the
document, holds whatever guards it and saves.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from . import events as events_module
from .match_project import STUB_AUDIT_DETECTION
from .shot_id import has_usable_id


def now_iso() -> str:
    """ISO-8601 UTC timestamp for audit_events entries."""
    return datetime.now(UTC).isoformat()


def new_event_id() -> str:
    """Unique id for audit_events entries - the sync merge unions event
    lists by this id, so every event needs one at creation time. uuid4
    hex, not ULID: ordering comes from ``ts``, and the ulid package is a
    hosted-only extra while events are stamped on slim local installs too."""
    return uuid.uuid4().hex


def reset_deletion_events(shots: Any) -> list[dict[str, Any]]:
    """One ``marker_deleted`` per identified shot a reset re-detect wipes.

    A ``reset`` re-detect rewrites ``doc["shots"]`` wholesale but used to
    write no ``marker_*`` event at all, so the append-only ``audit_events``
    log went on carrying whatever verdict those shots last had. The sync
    merge reads that log for shot membership, and its
    ``verdicts.get(k) is True`` escape hatch then re-adopted a superseded
    shot from the other side's document whenever the shot's id still
    carried a ``marker_kept`` -- which every rejected marker the user
    clicked back on has written. Measured on #842: a 5-shot local document
    merged to 6 with no note, and 18 of this repo's 57 audited fixtures
    carrying events hold at least one live present-verdict id.

    Emitting the delete makes the local log carry the *newest* verdict for
    the shots this run removes, instead of falling silent and letting an
    older one stay live.

    Only shots that already carry a usable id are named. An event keys on
    the shot id and there is nothing to key on otherwise -- and an
    unstamped shot makes the merge refuse the whole shot section out loud
    anyway (``sync/merge.py``'s unstamped-shot gate), so it cannot produce
    the silent re-adoption this closes.
    """
    if not isinstance(shots, list):
        return []
    now = now_iso()
    return [
        {
            "id": new_event_id(),
            "ts": now,
            "kind": "marker_deleted",
            "payload": {"id": shot["id"], "reason": "shot_detect_reset"},
        }
        for shot in shots
        if isinstance(shot, dict) and has_usable_id(shot)
    ]


def events_reset_event(dropped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One ``events_reset`` entry naming the ``auto`` stage events a reset
    re-detect drops, or nothing when it drops none (#1329).

    The shot wipe is logged per shot (``reset_deletion_events``); the
    proposals the same wipe drops went without a trace, so the audit log
    could not show that regions had been there.
    """
    if not dropped:
        return []
    return [
        {
            "id": new_event_id(),
            "ts": now_iso(),
            "kind": "events_reset",
            "payload": {
                "count": len(dropped),
                "ids": [e["id"] for e in dropped if e.get("id")],
                "reason": "shot_detect_reset",
            },
        }
    ]


#: ``cand-<n>`` shot id, as ``shot_id.derive_shot_id`` builds it. Read back
#: here so a candidate number that survives only in the event log still
#: counts against the high-water mark (#842).
CAND_ID_RE = re.compile(r"^cand-(\d+)$")


def candidate_high_water(doc: Any) -> int:
    """Highest candidate number this stage's document has ever used.

    A detection run numbers its candidates 1..K, so a re-detect used to
    hand the same ``cand-<n>`` to a different physical shot. That is the
    aliasing half of #842: the merge keys shot membership on the id, and
    an id that names one shot on Monday and another on Tuesday cannot
    carry a verdict. Numbering the next run from here + 1 means a
    candidate number is never reused within a stage, so the alias cannot
    form in the first place.

    Three places a number can survive, and all three count:

    * ``_candidates_pending_audit.candidates`` -- the current run's block.
      This is the inductive case: each run records its own already-offset
      numbers, so the next run clears them by construction even though
      the block is overwritten and earlier runs' candidates are gone.
    * ``shots[]`` -- an audited shot outlives the block it came from.
    * ``audit_events`` payload ids -- the log is append-only and never
      pruned, so it outlives *both*. This is the clause that matters: the
      second surviving case on #842 is a re-detect recreating a
      ``cand-<n>`` that only an old ``marker_rejected`` still remembers,
      and only the log has that number by then.

    Returns 0 for a document that has never been detected, so a first run
    numbers from 1 exactly as before.
    """
    if not isinstance(doc, dict):
        return 0
    numbers: list[int] = []

    def _take(value: Any) -> None:
        # bool is an int in Python and True would count as candidate 1.
        if isinstance(value, int) and not isinstance(value, bool):
            numbers.append(value)

    block = doc.get("_candidates_pending_audit")
    if isinstance(block, dict):
        for cand in block.get("candidates") or []:
            if isinstance(cand, dict):
                _take(cand.get("candidate_number"))
    for shot in doc.get("shots") or []:
        if isinstance(shot, dict):
            _take(shot.get("candidate_number"))
    for event in doc.get("audit_events") or []:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        shot_id = payload.get("id") if isinstance(payload, dict) else None
        match = CAND_ID_RE.match(shot_id) if isinstance(shot_id, str) else None
        if match is not None:
            numbers.append(int(match.group(1)))

    return max(numbers, default=0)


#: The ``_note`` a detection run writes on its ``_candidates_pending_audit``
#: block.
CANDIDATES_NOTE = (
    "3-voter ensemble (PANN gunshot folded into voter C). "
    "vote_a/b/c=1 means the voter kept the candidate; "
    "ensemble_score = vote_total + apriori_boost. shots[] is "
    "seeded from candidates with ensemble_score >= consensus. "
    "candidate_number is unique across this stage's detection "
    "runs, not 1..K per run -- see _candidate_high_water."
)


def candidate_dict(cand: Any) -> dict[str, Any]:
    """One ``EnsembleCandidate`` as the audit document stores it."""
    return {
        "candidate_number": cand.candidate_number,
        "time": cand.time,
        "ms_after_beep": cand.ms_after_beep,
        "peak_amplitude": cand.peak_amplitude,
        "confidence": cand.confidence,
        "vote_a": cand.vote_a,
        "vote_b": cand.vote_b,
        "vote_c": cand.vote_c,
        "vote_e": cand.vote_e,
        "vote_total": cand.vote_total,
        "apriori_boost": cand.apriori_boost,
        "ensemble_score": cand.ensemble_score,
        "score_c": cand.score_c,
        "clap_diff": cand.clap_diff,
        "gunshot_prob": cand.gunshot_prob,
        "voter_e_signal": cand.voter_e_signal,
    }


def merge_detection_into(
    doc: dict[str, Any],
    result: Any,
    *,
    base: Mapping[str, Any],
    stage_rounds: dict[str, Any] | None,
    reset: bool,
    run_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold one detection run (an ``EnsembleResult``) into ``doc``, in place.

    Re-appliable against a freshly-loaded doc so a lost optimistic-lock race
    re-merges into the winner's document instead of clobbering it: project
    ``stage_rounds`` wins, the candidate block is rewritten, ``shots[]`` is
    seeded only when empty (or on ``reset``) so a concurrent manual edit
    survives, and the run is appended to the ``audit_events`` log.

    ``base`` holds the document's base fields (``stage_number``,
    ``beep_time``, ...). ``doc`` may be the beep-confirm stub (``{"shots":
    [], "detection": "none"}``), which has none of them because they are
    unknown at beep-confirm time; they are backfilled with ``setdefault``,
    which keeps this idempotent under re-merge and never overwrites a value
    the doc already carries. The stub's sentinel is dropped so the saved
    document stays clean, but ``is_stub_audit`` no longer depends on that
    for correctness -- it also requires the absence of
    ``shots``/``audit_events``.

    ``run_payload`` adds keys to the ``shot_detect_run`` event's payload
    (the MCP tool records ``source: "mcp"``).
    """
    for key, value in base.items():
        doc.setdefault(key, value)
    if doc.get("detection") == STUB_AUDIT_DETECTION:
        del doc["detection"]
    if stage_rounds is not None:
        doc["stage_rounds"] = stage_rounds
    # #842: the ensemble numbers its candidates 1..K every run, so a
    # re-detect used to hand ``cand-<n>`` to a different physical shot
    # than the one that id already named. Offset past everything this
    # document has ever used, so a number is never reused within a
    # stage. Computed from ``doc`` and applied to copies rather than to
    # ``result`` in place: this function is re-applied against a
    # freshly-loaded document when a save loses the version race, and
    # mutating the run's candidates would offset them twice.
    offset = candidate_high_water(doc)
    candidates = [candidate_dict(c) for c in result.candidates]
    numbered = [{**c, "candidate_number": c["candidate_number"] + offset} for c in candidates]
    doc["_candidates_pending_audit"] = {
        "_note": CANDIDATES_NOTE,
        "consensus": result.consensus,
        "expected_rounds": result.expected_rounds,
        "candidates": numbered,
    }
    reset_deletions: list[dict[str, Any]] = []
    if reset:
        # #842: record the wipe before performing it. Without this the
        # log falls silent and an older ``marker_kept`` on a superseded
        # shot stays the newest verdict for that id, which the sync
        # merge then reads as "keep" and re-adopts from the other side.
        reset_deletions = reset_deletion_events(doc.get("shots"))
        doc["shots"] = []
        # Stage events (spec 2026-10-08): the seeder's untouched
        # proposals sat over gaps this wipe supersedes, so they go
        # and the stage may seed again; a region the user drew or
        # edited (``manual``) describes the run and stays.
        stage_events = doc.get(events_module.EVENTS_FIELD)
        if isinstance(stage_events, list):
            dropped = [e for e in stage_events if isinstance(e, dict) and e.get("source") == "auto"]
            doc[events_module.EVENTS_FIELD] = [
                e for e in stage_events if not (isinstance(e, dict) and e.get("source") == "auto")
            ]
            reset_deletions.extend(events_reset_event(dropped))
        doc.pop(events_module.EVENTS_SEEDED_FIELD, None)
    seeded_shots = False
    kept = [c for c in result.candidates if c.kept]
    if not doc.get("shots"):
        doc["shots"] = [
            {
                "shot_number": i,
                "candidate_number": c.candidate_number + offset,
                "time": c.time,
                "ms_after_beep": c.ms_after_beep,
                "source": "detected",
                "ensemble_votes": c.vote_total,
                "apriori_boost": c.apriori_boost,
                "ensemble_score": c.ensemble_score,
            }
            for i, c in enumerate(kept, start=1)
        ]
        seeded_shots = True
    events = list(doc.get("audit_events") or [])
    # Deletions first: they describe the state this run superseded,
    # and the merge orders a shot's verdicts by ``ts``.
    events.extend(reset_deletions)
    events.append(
        {
            "id": new_event_id(),
            "ts": now_iso(),
            "kind": "shot_detect_run",
            "payload": {
                "candidate_count": len(candidates),
                "kept_count": len(kept),
                "consensus": result.consensus,
                "expected_rounds": result.expected_rounds,
                "seeded_shots": seeded_shots,
                **(run_payload or {}),
            },
        }
    )
    doc["audit_events"] = events
    return doc
