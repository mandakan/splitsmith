"""Stable identity for audit-document shots.

``shot_number`` is positional -- ``ui/server.py`` writes ``"shot_number": i``
-- so it renumbers on every insert or delete and cannot key a merge. Shots
therefore carry an ``id``.

The derivation is what the SPA already computes client-side (``Audit.tsx``
builds ``cand-<n>`` for detected markers) and simply did not persist, so it is
deterministic for every shot carrying a ``candidate_number`` or a ``time`` --
which in practice is every shot -- and desktop and hosted independently mint
the same id for the same pre-existing shot, so no migration is needed for
those. A shot with neither key (``Audit.tsx`` documents this shape: a derived
anchor shot the secondary couldn't snap, left with ``time: null``) has
nothing stable to derive from and gets a minted, non-convergent id instead --
see ``derive_shot_id``.

uuid4 hex, not ULID, for the minted case -- matching ``_new_event_id`` in
``ui/server.py``, whose reasoning applies verbatim: the ulid package is a
hosted-only extra while these documents are also written on slim local
installs.
"""

from __future__ import annotations

import uuid
from typing import Any


def derive_shot_id(shot: dict[str, Any]) -> str:
    """Deterministic id for one shot dict.

    Detected and promoted shots key off ``candidate_number``; a manual shot
    with no candidate keys off its rounded time. A shot with neither gets a
    minted id, which is not deterministic -- callers that need convergence
    must persist it.
    """
    candidate = shot.get("candidate_number")
    if candidate is not None:
        return f"cand-{int(candidate)}"
    time = shot.get("time")
    if time is not None:
        return f"manual-t{int(round(float(time) * 1000))}"
    return f"manual-{uuid.uuid4().hex}"


def _has_usable_id(shot: dict[str, Any]) -> bool:
    """Whether a shot already carries an id worth keeping.

    Only a non-empty string counts. A truthy *non-string* ``id`` -- an int
    from a hand-edited document, say -- is not an id: nothing downstream can
    key on it, and treating it as present made the shot invisible to the
    keying and to the merge's unstamped guard at the same time, so it
    vanished from a merge with no note at all.
    """
    shot_id = shot.get("id")
    return isinstance(shot_id, str) and bool(shot_id)


def ensure_shot_ids(shots: list[dict[str, Any]]) -> int:
    """Stamp ``id`` on every shot that lacks a usable one; return how many.

    An existing *string* id is never rewritten -- that is what makes a nudge
    a move rather than a delete plus an add. A derived id that collides with
    one already used in this document falls back to a minted id, so two
    manual shots on the same millisecond stay distinct.
    """
    taken = {shot["id"] for shot in shots if isinstance(shot, dict) and _has_usable_id(shot)}
    added = 0
    for shot in shots:
        if not isinstance(shot, dict) or _has_usable_id(shot):
            continue
        candidate_id = derive_shot_id(shot)
        if candidate_id in taken:
            candidate_id = f"manual-{uuid.uuid4().hex}"
        shot["id"] = candidate_id
        taken.add(candidate_id)
        added += 1
    return added
