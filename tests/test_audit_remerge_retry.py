"""Worker audit save: bounded re-load + re-merge on optimistic-lock loss.

State-refactor phase 4. When ``_run_shot_detect`` saves its merged audit
doc to hosted ``state_docs`` and a concurrent writer (a manual SPA edit,
another job) bumps the version in between, the save raises
``StateConflictError``. The worker must re-load the winner's doc, re-apply
its merge to *that* (so the concurrent edit survives -- this job merges,
never blindly overwrites), and save again, bounded.

Tests the extracted ``_save_audit_with_remerge`` helper against a fake
state so the concurrency logic is exercised without standing up the whole
detection job.
"""

from __future__ import annotations

import pytest

from splitsmith.db import StateConflictError
from splitsmith.ui.server import _AUDIT_SAVE_MAX_ATTEMPTS, _save_audit_with_remerge


class _FakeState:
    """Minimal state exposing load_audit / save_audit with a scripted
    conflict on the first N saves."""

    def __init__(self, *, conflicts: int, reloaded_doc: dict) -> None:
        self._conflicts = conflicts
        self._reloaded_doc = reloaded_doc
        self.saved: list[tuple[dict, int]] = []
        self.load_calls = 0
        self._version = 7  # the winner's current version after a conflict

    def save_audit(self, slug: str, stage_number: int, doc: dict, *, version: int) -> int:
        if self._conflicts > 0:
            self._conflicts -= 1
            raise StateConflictError("scripted conflict")
        self.saved.append((doc, version))
        return version + 1

    def load_audit(self, slug: str, stage_number: int) -> tuple[dict | None, int]:
        self.load_calls += 1
        # Hand back a copy so the merge can't mutate the test's fixture.
        return dict(self._reloaded_doc), self._version


def _merge(doc: dict) -> dict:
    # Seed shots only when empty (mirrors the real merge) + append an event.
    if not doc.get("shots"):
        doc["shots"] = [{"shot_number": 1, "source": "detected"}]
    doc.setdefault("audit_events", []).append({"kind": "shot_detect_run"})
    return doc


def _default() -> dict:
    return {"shots": []}


def test_no_conflict_saves_once() -> None:
    state = _FakeState(conflicts=0, reloaded_doc={})
    doc = {"shots": []}
    new_version = _save_audit_with_remerge(
        state, "alice", 1, doc=doc, version=3, merge=_merge, default=_default
    )
    assert new_version == 4
    assert state.load_calls == 0  # never re-loaded
    assert len(state.saved) == 1
    saved_doc, saved_version = state.saved[0]
    assert saved_version == 3
    assert saved_doc["shots"] == [{"shot_number": 1, "source": "detected"}]


def test_one_conflict_reloads_remerges_preserving_concurrent_edit() -> None:
    # The winner's doc already carries a manual edit (a kept shot + an
    # event). Our re-merge must preserve it: shots[] is non-empty so we
    # don't reseed, and our event appends after theirs.
    winner = {
        "shots": [{"shot_number": 1, "source": "manual"}],
        "audit_events": [{"kind": "manual_edit"}],
    }
    state = _FakeState(conflicts=1, reloaded_doc=winner)
    doc = {"shots": []}  # what we loaded before the race
    new_version = _save_audit_with_remerge(
        state, "alice", 1, doc=doc, version=3, merge=_merge, default=_default
    )
    assert state.load_calls == 1  # re-loaded once after the conflict
    assert new_version == 8  # winner version 7 -> 8
    saved_doc, saved_version = state.saved[0]
    assert saved_version == 7  # saved against the re-loaded version
    # Concurrent manual shot preserved (not reseeded), our event appended.
    assert saved_doc["shots"] == [{"shot_number": 1, "source": "manual"}]
    assert [e["kind"] for e in saved_doc["audit_events"]] == ["manual_edit", "shot_detect_run"]


def test_exhausting_retries_reraises() -> None:
    state = _FakeState(conflicts=_AUDIT_SAVE_MAX_ATTEMPTS, reloaded_doc={"shots": []})
    with pytest.raises(StateConflictError):
        _save_audit_with_remerge(
            state, "alice", 1, doc={"shots": []}, version=3, merge=_merge, default=_default
        )
    # One save attempt per allowed try; load between all but the last.
    assert state.load_calls == _AUDIT_SAVE_MAX_ATTEMPTS - 1
