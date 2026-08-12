"""Stable identity for audit-document shots.

``shot_number`` is positional -- ``ui/server.py`` writes ``"shot_number": i``
-- so it renumbers on every insert or delete and cannot key a merge. Shots
therefore carry an ``id``.

The derivation is what the SPA already computes client-side (``Audit.tsx``
builds ``cand-<n>`` for detected markers) and simply did not persist, so it is
deterministic for every shot carrying a ``candidate_number`` or a ``time`` --
which in practice is every shot. It is *convergent* across two sides only for
the ``candidate_number`` case, though: a candidate-less manual shot keys off
its rounded time, which moves when the shot is nudged, so two sides stamping
it independently mint two different ids for one shot. Only one side may
therefore mint -- the desktop -- see the ``mint`` argument to
``ensure_shot_ids``. A shot with neither key (``Audit.tsx`` documents this
shape: a derived anchor shot the secondary couldn't snap, left with
``time: null``) has nothing stable to derive from and gets a minted,
non-convergent id instead -- see ``derive_shot_id``.

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


def ensure_shot_ids(shots: list[dict[str, Any]], *, mint: bool = True) -> int:
    """Stamp ``id`` on every shot that lacks a usable one; return how many.

    An existing *string* id is never rewritten -- that is what makes a nudge
    a move rather than a delete plus an add. A derived id that collides with
    one already used in this document falls back to a minted id, so two
    manual shots on the same millisecond stay distinct.

    ``mint=False`` leaves a shot with no usable id exactly as it arrived and
    returns 0 for it. It exists because ``derive_shot_id`` is only
    convergent for a shot carrying a ``candidate_number``: a candidate-less
    manual shot keys off its *rounded time*, so two sides that stamp one
    pre-existing shot independently -- a desktop that nudged it to 6.52 s,
    a phone that accepted the mirror at 6.5 s -- mint ``manual-t6520`` and
    ``manual-t6500`` for the same shot. Both documents then look fully
    stamped, the sync merge's unstamped-shot gate passes, and the shot
    unions into two. Two sides cannot derive different ids for one shot if
    only one side ever derives, so the hosted save boundary passes
    ``mint=False`` for a ``desktop``-origin mirror and leaves the minting
    to the desktop (which does it on its own save boundary, in the sync
    migration pass, and in the merge itself).

    Preserving an id is not minting one: a shot that arrives carrying a
    client-supplied id keeps it either way, which is what lets a phone add
    a genuinely new shot to a mirror -- the SPA mints that id itself.
    """
    taken = {shot["id"] for shot in shots if isinstance(shot, dict) and _has_usable_id(shot)}
    added = 0
    for shot in shots:
        if not isinstance(shot, dict) or _has_usable_id(shot):
            continue
        if not mint:
            continue
        candidate_id = derive_shot_id(shot)
        if candidate_id in taken:
            candidate_id = f"manual-{uuid.uuid4().hex}"
        shot["id"] = candidate_id
        taken.add(candidate_id)
        added += 1
    return added
