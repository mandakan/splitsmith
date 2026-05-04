"""Lab labeling round-trip: subclasses on shots[] propagate to candidates.

The candidate's detected time and the audit shot's time can differ by
up to the matching tolerance (~75 ms). ``apply_labels`` writes a
``subclass`` onto the closest ``shots[]`` entry by audit time, but
the lab UI looks the value up by *candidate* time. The lookup must be
proximity-aware or labels appear to silently disappear.
"""

from __future__ import annotations

from splitsmith.lab.core import _load_labels_from_audit, _subclass_for_time


def test_subclass_lookup_handles_audit_candidate_time_offset() -> None:
    """A subclass written onto a ``shots[]`` entry at audit_time must
    still resolve when the candidate's detected time is offset within
    the matching tolerance."""
    audit = {
        "shots": [
            {"time": 5.123, "subclass": "paper"},
            {"time": 10.456, "subclass": "steel"},
        ],
        "_candidates_pending_audit": {"labels_by_time": {}},
    }
    _, subs = _load_labels_from_audit(audit)
    assert _subclass_for_time(5.156, subs) == "paper"  # 33 ms after audit
    assert _subclass_for_time(10.420, subs) == "steel"  # 36 ms before audit
    # Outside tolerance -> no match.
    assert _subclass_for_time(5.250, subs) is None  # 127 ms off
    # Picks the nearer one when two are within range.
    assert _subclass_for_time(5.135, subs) == "paper"


def test_subclass_lookup_returns_none_when_no_shots() -> None:
    audit: dict = {"shots": [], "_candidates_pending_audit": {}}
    _, subs = _load_labels_from_audit(audit)
    assert subs == []
    assert _subclass_for_time(1.234, subs) is None


def test_reasons_use_exact_key_lookup() -> None:
    """Reasons live in ``labels_by_time`` keyed by candidate time, so
    they don't need proximity matching -- the candidate's own time is
    the lookup key by construction."""
    audit = {
        "_candidates_pending_audit": {
            "labels_by_time": {"1.149": "handling", "5.123": "echo"},
        },
        "shots": [],
    }
    reasons, _ = _load_labels_from_audit(audit)
    # Stored keys round to the same 1 ms grid as candidate times.
    assert reasons[round(1.149, 3)] == "handling"
    assert reasons[round(5.123, 3)] == "echo"
