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
therefore mint *that* non-convergent id -- the desktop -- see the ``mint``
argument to ``ensure_shot_ids``; the ``candidate_number`` case is convergent
regardless of ``mint``, since both sides derive the same id from the same
number, unless two shots in one document share a ``candidate_number`` and the
second one collides -- the collision fallback is a uuid4, so it is suppressed
under ``mint=False`` too. A shot with neither key (``Audit.tsx`` documents this
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


def _candidate_number(shot: dict[str, Any]) -> int | None:
    """A shot's ``candidate_number`` as an ``int``, or ``None`` if it has none.

    A value that is not integer-like counts as *absent* rather than as an
    error: these documents arrive from an unvalidated client field, and
    ``int("abc")`` / ``int([])`` would otherwise raise out of a derivation
    that now runs on the hosted save boundary for a mirror too -- a 500 on a
    phone save. Falling through to the time branch is the conservative
    outcome, and it matches what the shot actually is: no usable candidate.

    ``0`` is a real candidate number and yields ``cand-0``; only ``None``
    and junk are absent.

    A ``bool`` is deliberately absent even though ``bool`` is an ``int`` in
    Python: ``True`` would derive ``cand-1`` and alias onto the real
    candidate 1, which is worse than having no candidate at all.
    """
    candidate = shot.get("candidate_number")
    if candidate is None or isinstance(candidate, bool):
        return None
    try:
        return int(candidate)
    except (TypeError, ValueError, OverflowError):
        return None


def derive_shot_id(shot: dict[str, Any]) -> str:
    """Deterministic id for one shot dict.

    Detected and promoted shots key off ``candidate_number``; a manual shot
    with no candidate -- or one whose ``candidate_number`` is not
    integer-like, see ``_candidate_number`` -- keys off its rounded time. A
    shot with neither gets a minted id, which is not deterministic --
    callers that need convergence must persist it.
    """
    candidate = _candidate_number(shot)
    if candidate is not None:
        return f"cand-{candidate}"
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

    ``mint=False`` does *not* mean "no id is invented" -- it means "no
    *non-convergent* id is invented". A shot carrying a ``candidate_number``
    still gets ``cand-<n>`` derived under ``mint=False``: both sides compute
    it from the same detector-assigned number, so there is no second-minter
    risk, and the SPA relies on exactly this -- it omits ``id`` for a
    detected shot on purpose and expects the server to derive ``cand-<n>``
    (see ``audit-doc.test.ts``). Suppressing that derivation on a mirror
    left every detected shot in a phone save unstamped, which made the
    sync merge's unstamped-shot gate refuse the whole shot section on the
    next desktop pull -- reverting the phone's edits, not just declining to
    mint an id.

    What ``mint=False`` does suppress is the two non-convergent branches of
    ``derive_shot_id``: a candidate-less manual shot keys off its *rounded
    time*, so two sides that stamp one pre-existing shot independently -- a
    desktop that nudged it to 6.52 s, a phone that accepted the mirror at
    6.5 s -- mint ``manual-t6520`` and ``manual-t6500`` for the same shot.
    Both documents then look fully stamped, the sync merge's unstamped-shot
    gate passes, and the shot unions into two. A shot with neither key would
    otherwise get a random uuid4 id, which is non-convergent by
    construction. Those two branches are left with no usable id exactly as
    they arrived, and this function returns 0 for them. Two sides cannot
    derive different ids for one shot if only one side ever derives the
    non-convergent branches, so the hosted save boundary passes
    ``mint=False`` for a ``desktop``-origin mirror and leaves *that* minting
    to the desktop (which does it on its own save boundary, in the sync
    migration pass, and in the merge itself).

    A collision is the one place where even the convergent branch stops
    being convergent, so ``mint=False`` suppresses it too: two shots can
    carry the *same* ``candidate_number`` (``lab/promote.py``'s
    ``_find_candidate_number`` picks the nearest ensemble candidate per
    snapped shot independently, so a promoted stage can snap two shots onto
    one candidate -- this repo's own promoted fixtures contain that shape),
    and the second of them derives a ``cand-<n>`` that is already taken.
    The fallback for that is a uuid4, which is non-convergent by
    construction: a mirror and a desktop stamping the same colliding shot
    would mint two different ids, both documents would then read as fully
    stamped, the sync merge's unstamped-shot gate would pass, and one shot
    would silently become two. Under ``mint=False`` the colliding shot is
    therefore left unstamped instead, which makes that gate refuse the
    section out loud -- a stated refusal, not a silent duplicate.

    Preserving an id is not minting one: a shot that arrives carrying a
    client-supplied id keeps it either way, which is what lets a phone add
    a genuinely new shot to a mirror -- the SPA mints that id itself.
    """
    taken = {shot["id"] for shot in shots if isinstance(shot, dict) and _has_usable_id(shot)}
    added = 0
    for shot in shots:
        if not isinstance(shot, dict) or _has_usable_id(shot):
            continue
        convergent = _candidate_number(shot) is not None
        if not mint and not convergent:
            continue
        candidate_id = derive_shot_id(shot)
        if candidate_id in taken:
            if not mint:
                # Leave it unstamped; the merge's gate handles it loudly.
                continue
            candidate_id = f"manual-{uuid.uuid4().hex}"
        shot["id"] = candidate_id
        taken.add(candidate_id)
        added += 1
    return added
