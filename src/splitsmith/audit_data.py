"""Reading a stage's audit JSON and turning it into engine records.

The audit document at ``<project>/audit/stage<N>.json`` is the user's
source of truth for a stage: which shots are real and where they sit
relative to the beep. Two consumers need it and they sit on opposite
sides of the codebase -- the per-stage export pipeline
(``ui/exports.py``, ``ui/match_exports.py``) and the multi-shooter
compare grid's overlay (``compare/overlay_data.py``).

**This module exists because of where those two consumers are.** Both
functions used to live in ``ui/exports.py``, so ``compare`` reached into
the web-UI layer to read a file, and that edge closed a real cycle:

    overlay_html -> compare.overlay_sprites -> compare.overlay_data
                 -> ui.exports -> overlay_render -> overlay_html

Dormant until #684 made ``overlay_render`` import ``overlay_html``, at
which point nothing in the package could be imported at all --
``ui/exports.py`` does ``from ..overlay_render import OverlayCodec`` and
``OverlayCodec`` is defined after ``overlay_render``'s import block, so
re-entry was guaranteed to hit a partially initialised module. #684
held it open with a ``TYPE_CHECKING`` guard in ``overlay_html``; issue
#760 is this module, which removes the edge instead of guarding it.

Reading an audit is a core concern, not a web-UI one. Nothing here
knows about HTTP, and it imports only the core ``config`` and ``coach``
modules (the latter for the coach field names it is the source of truth
for).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .coach import (
    COACH_INTERVAL_CLASS_SOURCES,
    COACH_INTERVAL_CLASSES,
    FIELD_INTERVAL_CLASS,
    FIELD_INTERVAL_CLASS_SOURCE,
)
from .config import Shot


class StageExportError(RuntimeError):
    """Raised when the audit JSON is malformed or lacks the data needed to
    produce an export. Endpoints surface this as a 400.

    The name is historical -- it predates this module and names the
    layer that *surfaces* the failure rather than the one that raises
    it. Renaming it would touch every catch site for no behaviour
    change, so it moved here as-is.
    """


def read_audit_data(audit_path: Path) -> dict[str, Any]:
    """Return the stage's audit document, or an empty one when absent.

    A missing file means shot detection never ran for this stage. That is
    a legitimate state -- the lossless trim and the FCPXML spine need only
    a beep and a stage time -- so it collapses to zero shots and the
    shot-dependent artefacts skip themselves downstream. A file that
    exists but won't parse is a real fault and still raises.

    Public because it is *the* audit precondition (#619): the MCP export
    tools and ``ui.match_exports`` used to hard-gate on the file existing,
    which told a user asking for a trim-only export to run exactly the shot
    detection the audit-free path exists to make optional.
    """
    if not audit_path.exists():
        return {"shots": []}
    try:
        return json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageExportError(f"failed to read audit JSON {audit_path}: {exc}") from exc


def audit_shots_to_engine_shots(
    audit_data: dict[str, Any],
    *,
    beep_time_in_source: float,
) -> list[Shot]:
    """Convert the audit JSON's ``shots[]`` to engine :class:`Shot` records.

    ``beep_time_in_source`` is the beep position in the source video's
    timeline (seconds from start). Audit ``shots[].time`` is clip-local;
    we never use it directly here -- the engine wants ``time_absolute`` in
    the source, which is ``beep_time_in_source + time_from_beep``.

    ``peak_amplitude`` and ``confidence`` are looked up from the candidate
    pool (``_candidates_pending_audit.candidates``) by ``candidate_number``
    when present; otherwise default to 0.0 (manually-added shots that
    weren't tied to a detector candidate).

    Splits: shot 1's split is the draw (= ``time_from_beep``); shot N>1 is
    the difference between successive ``time_from_beep`` values. This
    mirrors :func:`csv_gen.write_splits_csv`'s expectations from the CLI.
    """
    raw_shots = audit_data.get("shots") or []
    if not isinstance(raw_shots, list) or not raw_shots:
        return []

    candidates_block = audit_data.get("_candidates_pending_audit") or {}
    candidates = candidates_block.get("candidates") if isinstance(candidates_block, dict) else None
    by_cand: dict[int, dict[str, Any]] = {}
    if isinstance(candidates, list):
        for c in candidates:
            num = c.get("candidate_number") if isinstance(c, dict) else None
            if isinstance(num, int):
                by_cand[num] = c

    # Sort by shot_number so the output is deterministic regardless of the
    # JSON's row order. Audits saved by the SPA preserve order, but external
    # tools (audit-apply) write append-style, so don't trust order.
    ordered = sorted(raw_shots, key=lambda s: s.get("shot_number", 0))

    out: list[Shot] = []
    prev_time_from_beep: float | None = None
    for raw in ordered:
        if not isinstance(raw, dict):
            continue
        ms = raw.get("ms_after_beep")
        if ms is None:
            continue
        time_from_beep = float(ms) / 1000.0
        time_absolute = beep_time_in_source + time_from_beep
        cand_num = raw.get("candidate_number")
        cand = by_cand.get(cand_num) if isinstance(cand_num, int) else None
        peak = float(cand.get("peak_amplitude", 0.0)) if isinstance(cand, dict) else 0.0
        conf = (
            float(cand.get("confidence", 0.0))
            if isinstance(cand, dict) and cand.get("confidence") is not None
            else 0.0
        )
        # Clamp confidence to the model's [0, 1] domain in case the
        # candidate carries a raw classifier score that escaped the band.
        conf = max(0.0, min(1.0, conf))
        notes_raw = raw.get("notes")
        notes = str(notes_raw) if isinstance(notes_raw, str) else ""
        # Coach annotations ride along (#772) so downstream consumers can
        # tell a split from a reload. A junk or half-set pair degrades to
        # unclassified rather than failing the read - same posture as the
        # candidate lookups above.
        interval_class = raw.get(FIELD_INTERVAL_CLASS)
        interval_class_source = raw.get(FIELD_INTERVAL_CLASS_SOURCE)
        if (
            interval_class not in COACH_INTERVAL_CLASSES
            or interval_class_source not in COACH_INTERVAL_CLASS_SOURCES
        ):
            interval_class = None
            interval_class_source = None
        shot_number = int(raw.get("shot_number", len(out) + 1))
        if prev_time_from_beep is None:
            split = time_from_beep  # draw
        else:
            split = time_from_beep - prev_time_from_beep
        prev_time_from_beep = time_from_beep
        out.append(
            Shot(
                shot_number=shot_number,
                time_absolute=time_absolute,
                time_from_beep=time_from_beep,
                split=split,
                peak_amplitude=peak,
                confidence=conf,
                notes=notes,
                interval_class=interval_class,
                interval_class_source=interval_class_source,
            )
        )
    return out
