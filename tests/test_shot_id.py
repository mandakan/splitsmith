"""Stable identity for audit-document shots."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.shot_id import derive_shot_id, ensure_shot_ids
from splitsmith.sync.merge import merge_audit_doc
from splitsmith.ui.server import create_app
from tests.conftest import bound_match_id, scaffold_match
from tests.hosted_helpers import _CapturingSender, login
from tests.mirror_helpers import alias_url, legacy_audit_doc, seed_mirror_stage_with_audit


def test_detected_shot_keys_off_candidate_number() -> None:
    assert derive_shot_id({"candidate_number": 37, "time": 7.181}) == "cand-37"


def test_manual_shot_keys_off_rounded_time() -> None:
    assert derive_shot_id({"candidate_number": None, "time": 7.1814}) == "manual-t7181"


def test_derivation_is_identical_for_the_same_input() -> None:
    """Both sides must mint the same id without coordinating."""
    shot = {"candidate_number": None, "time": 12.5}
    assert derive_shot_id(shot) == derive_shot_id(dict(shot))


def test_ensure_stamps_only_missing_ids() -> None:
    shots = [
        {"candidate_number": 1, "time": 1.0},
        {"candidate_number": None, "time": 2.0, "id": "manual-already-here"},
    ]
    added = ensure_shot_ids(shots)
    assert added == 1
    assert shots[0]["id"] == "cand-1"
    assert shots[1]["id"] == "manual-already-here"


def test_existing_id_survives_a_nudge() -> None:
    """The whole point: moving a shot is a move, not a delete plus an add."""
    shots = [{"candidate_number": None, "time": 2.0}]
    ensure_shot_ids(shots)
    original = shots[0]["id"]
    shots[0]["time"] = 2.01
    ensure_shot_ids(shots)
    assert shots[0]["id"] == original


def test_colliding_derivations_get_distinct_ids() -> None:
    """Two manual shots on the same millisecond must not share an id."""
    shots = [
        {"candidate_number": None, "time": 3.0},
        {"candidate_number": None, "time": 3.0},
    ]
    ensure_shot_ids(shots)
    assert shots[0]["id"] != shots[1]["id"]


def test_shot_with_no_time_still_gets_an_id() -> None:
    shots = [{"candidate_number": None, "time": None}]
    ensure_shot_ids(shots)
    assert shots[0]["id"]


def test_a_shot_with_no_candidate_and_no_time_has_no_convergent_id() -> None:
    """Documented limitation, not an accident: a shot with neither key has
    nothing stable to derive from, so two independent stamps of equivalent
    no-key shots do not converge on the same id."""
    first = [{"candidate_number": None, "time": None}]
    second = [{"candidate_number": None, "time": None}]
    ensure_shot_ids(first)
    ensure_shot_ids(second)
    assert first[0]["id"] != second[0]["id"]


@pytest.fixture
def local_app_with_stage(tmp_path: Path) -> tuple[TestClient, str]:
    """Local-mode TestClient for a project with one shooter and one stage."""
    root, shooter_root = scaffold_match(tmp_path, name="Shot Id Match")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Shot Id Match")
    client = TestClient(app)
    return client, f"/api/matches/{bound_match_id(app)}"


def test_put_audit_stamps_ids_and_keeps_them_across_a_nudge(
    local_app_with_stage: tuple[TestClient, str],
) -> None:
    """A save mints ids; the next save preserves them even though time moved."""
    client, url_base = local_app_with_stage
    doc = {
        "stage_number": 1,
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "candidate_number": 4, "time": 6.687, "ms_after_beep": 1687},
            {"shot_number": 2, "candidate_number": None, "time": 7.181, "ms_after_beep": 2181},
        ],
        "audit_events": [],
    }
    first = client.put(f"{url_base}/shooters/me/stages/1/audit", json=doc)
    assert first.status_code == 200, first.text
    ids = [s["id"] for s in first.json()["shots"]]
    assert ids[0] == "cand-4"
    assert ids[1].startswith("manual-")

    moved = first.json()
    moved["shots"][1]["time"] = 7.201
    second = client.put(f"{url_base}/shooters/me/stages/1/audit", json=moved)
    assert second.status_code == 200, second.text
    assert [s["id"] for s in second.json()["shots"]] == ids


def test_a_truthy_non_string_id_is_replaced_not_kept() -> None:
    """A non-string id is not an id -- nothing downstream can key on it.

    Treating it as present made the shot invisible to the sync merge's
    keying and to its unstamped-shot guard at the same time, so the shot
    vanished from a merged document with no note at all.
    """
    shots = [{"id": 42, "candidate_number": 4, "time": 6.0}]
    added = ensure_shot_ids(shots)
    assert added == 1
    assert shots[0]["id"] == "cand-4"


def test_an_empty_string_id_is_replaced() -> None:
    shots = [{"id": "", "candidate_number": 4, "time": 6.0}]
    assert ensure_shot_ids(shots) == 1
    assert shots[0]["id"] == "cand-4"


def test_mint_false_leaves_an_unstamped_shot_alone() -> None:
    """The mirror's contract: only the desktop invents an id."""
    shots = [{"candidate_number": None, "time": 6.5}]
    assert ensure_shot_ids(shots, mint=False) == 0
    assert "id" not in shots[0]


def test_mint_false_still_keeps_a_client_supplied_id() -> None:
    """Preserving an id is not minting one -- this is how a phone adds a
    genuinely new manual shot to a mirror: the SPA mints the id itself.

    Both shots here are candidate-less (non-convergent), so neither gets a
    derived id under ``mint=False``: the first already carries one and is
    left alone, the second has none and stays that way.
    """
    shots = [
        {"id": "manual-from-the-spa", "candidate_number": None, "time": 6.5},
        {"candidate_number": None, "time": 7.0},
    ]
    assert ensure_shot_ids(shots, mint=False) == 0
    assert shots[0]["id"] == "manual-from-the-spa"
    assert "id" not in shots[1]


def test_mint_false_still_derives_the_convergent_candidate_id() -> None:
    """``mint=False`` does not mean "no id is invented" -- only the two
    non-convergent branches of ``derive_shot_id`` are suppressed (#631
    Task 6 fix round 1, Critical). A detected shot's ``cand-<n>`` is
    convergent by construction: both sides derive it from the same
    ``candidate_number``, so there is no second-minter risk, and it is
    still derived with minting off. The SPA relies on exactly this -- it
    omits ``id`` for a detected shot on purpose and expects the server to
    derive ``cand-<n>`` -- so suppressing this branch on a mirror left
    every detected shot in a phone save unstamped, which made the sync
    merge's unstamped-shot gate refuse the whole shot section on the next
    desktop pull.
    """
    shots = [{"candidate_number": 4, "time": 7.0}]
    assert ensure_shot_ids(shots, mint=False) == 1
    assert shots[0]["id"] == "cand-4"


def test_mint_false_leaves_a_non_convergent_unusable_id_untouched() -> None:
    """A non-string id is not an id, and for a candidate-less shot -- so no
    convergent derivation is available under ``mint=False`` -- there is
    nothing safe to replace it with. Leaving it is the outcome: the
    merge's unstamped-shot guard uses the same ``has_usable_id``
    predicate, so the shot still reads as unstamped there and the gate
    refuses the section rather than keying on 42.
    """
    shots = [{"id": 42, "candidate_number": None, "time": 6.0}]
    assert ensure_shot_ids(shots, mint=False) == 0
    assert shots[0]["id"] == 42


def test_mint_false_replaces_an_unusable_id_when_convergent() -> None:
    """Unlike the non-convergent case above, a candidate-carrying shot's
    unusable id IS replaced under ``mint=False`` -- ``cand-<n>`` is
    derivable and convergent regardless of what junk sat in ``id``
    beforehand.
    """
    shots = [{"id": 42, "candidate_number": 4, "time": 6.0}]
    assert ensure_shot_ids(shots, mint=False) == 1
    assert shots[0]["id"] == "cand-4"


def test_mint_false_leaves_a_shot_with_no_key_at_all_unstamped() -> None:
    """The uuid4 branch is non-convergent by construction, so ``mint=False``
    suppresses it exactly as it suppresses the time-keyed branch.

    ``Audit.tsx`` documents this shape: a derived anchor shot the secondary
    couldn't snap, left with ``time: null``. Two sides stamping it
    independently would mint two random ids for one shot.
    """
    shots = [{"candidate_number": None, "time": None}]
    assert ensure_shot_ids(shots, mint=False) == 0
    assert "id" not in shots[0]


def test_mint_false_leaves_a_colliding_convergent_id_unstamped() -> None:
    """Two shots sharing one ``candidate_number`` -- the shape a promoted
    stage actually produces.

    ``lab/promote.py``'s ``_find_candidate_number`` picks the nearest
    ensemble candidate per snapped shot independently, so two shots can land
    on the same one; this repo's promoted fixtures contain it (e.g.
    ``stage-shots-blacksmith-2026-stage6-...`` has candidate 18 twice). The
    second shot's derived ``cand-18`` is already taken, and the collision
    fallback is a uuid4 -- non-convergent, i.e. the exact thing ``mint=False``
    exists to prevent. It must leave the shot unstamped instead, so the sync
    merge's unstamped-shot gate refuses out loud rather than the two sides
    minting two different ids for one shot and unioning it into two.
    """
    shots = [
        {"shot_number": 7, "candidate_number": 18, "time": 8.796, "source": "promoted"},
        {"shot_number": 8, "candidate_number": 18, "time": 9.48, "source": "promoted"},
    ]
    assert ensure_shot_ids(shots, mint=False) == 1
    assert shots[0]["id"] == "cand-18"
    assert "id" not in shots[1]


def test_a_colliding_convergent_id_still_falls_back_when_minting() -> None:
    """The desktop, which may mint, keeps the fallback: it is the only side
    stamping, so a uuid4 here is not divergent -- and the two shots must not
    share an id."""
    shots = [
        {"shot_number": 7, "candidate_number": 18, "time": 8.796, "source": "promoted"},
        {"shot_number": 8, "candidate_number": 18, "time": 9.48, "source": "promoted"},
    ]
    assert ensure_shot_ids(shots) == 2
    assert shots[0]["id"] == "cand-18"
    assert shots[1]["id"].startswith("manual-")
    assert shots[0]["id"] != shots[1]["id"]


@pytest.mark.parametrize("junk", ["abc", [], {}, object()], ids=["str", "list", "dict", "object"])
def test_a_junk_candidate_number_is_treated_as_absent(junk: object) -> None:
    """``int(candidate)`` used to run unguarded on an unvalidated client
    field: ``"abc"`` raised ``ValueError`` and ``[]`` raised ``TypeError``,
    which surfaced as a 500 -- newly reachable on a mirror, where this
    derivation did not run before. A candidate that is not integer-like is
    no candidate, so the shot keys off its time instead.
    """
    assert derive_shot_id({"candidate_number": junk, "time": 6.5}) == "manual-t6500"


def test_a_bool_candidate_number_is_treated_as_absent() -> None:
    """``bool`` is an ``int`` in Python, so ``True`` would derive ``cand-1``
    and alias onto the real candidate 1. Deliberately absent instead."""
    assert derive_shot_id({"candidate_number": True, "time": 6.5}) == "manual-t6500"
    assert derive_shot_id({"candidate_number": False, "time": 6.5}) == "manual-t6500"


def test_candidate_number_zero_still_derives_cand_zero() -> None:
    """The guard must not turn a falsy-but-real candidate into no candidate."""
    assert derive_shot_id({"candidate_number": 0, "time": 6.5}) == "cand-0"
    shots = [{"candidate_number": 0, "time": 6.5}]
    assert ensure_shot_ids(shots, mint=False) == 1
    assert shots[0]["id"] == "cand-0"


def test_a_real_string_id_is_never_rewritten() -> None:
    """The invariant the above must not break: a persisted id is a shot's
    identity across a nudge, so it survives even when it looks derivable."""
    shots = [{"id": "manual-t1000", "candidate_number": 4, "time": 6.0}]
    assert ensure_shot_ids(shots) == 0
    assert shots[0]["id"] == "manual-t1000"


def _stamp_through_the_save_boundary(tmp_path: Path, subdir: str, time: float) -> dict:
    """PUT one legacy manual shot through the real audit save handler.

    Deliberately goes through ``ui/server.py``'s ``ensure_shot_ids`` call
    site rather than calling ``ensure_shot_ids`` here: the whole point of
    the test below is which id the *save boundary* mints.
    """
    # Each side gets its own parent directory: an app discovers sibling
    # matches, and two under one root would both register.
    side_root = tmp_path / subdir
    side_root.mkdir(parents=True, exist_ok=True)
    root, shooter_root = scaffold_match(side_root, name="Divergence")
    project = MatchProject.load(shooter_root)
    project.stages = [StageEntry(stage_number=1, stage_name="Stage One", time_seconds=30.0)]
    project.save(shooter_root)
    client = TestClient(create_app(project_root=root, project_name="Divergence"))
    # Not bound_match_id: the registry refreshes from the user-level recent
    # projects list, so both sides' matches register in either app.
    url_base = f"/api/matches/{match_model.Match.load(root).match_id}"
    doc = {
        "stage_number": 1,
        "beep_time": 5.0,
        # No id and no candidate_number: a legacy manual shot, the one shape
        # whose derived id moves when the time moves.
        "shots": [
            {
                "shot_number": 1,
                "candidate_number": None,
                "time": time,
                "ms_after_beep": int(round((time - 5.0) * 1000)),
            }
        ],
        "audit_events": [],
    }
    response = client.put(f"{url_base}/shooters/me/stages/1/audit", json=doc)
    assert response.status_code == 200, response.text
    return response.json()


def _accept_a_legacy_shot_on_a_mirror(client: TestClient, match_id: str, name: str, *, time: float) -> dict:
    """Seed a mirror holding one unstamped legacy manual shot, then accept it.

    Goes through the real triage-accept handler on a ``desktop``-origin
    match -- the one hosted save boundary a phone can reach on a mirror
    (``capabilities._REVIEW_ROUTES`` grants it the review capability) and
    therefore the one that must not mint. Seeds through the desktop-sync
    doc routes and returns the stored audit doc as it stands after the
    accept.

    ``legacy_audit_doc`` is the unstamped shape under test: a manual shot
    with no id and no ``candidate_number``, whose derived id keys off the
    rounded time and so moves when the shot moves.
    """
    seed_mirror_stage_with_audit(client, match_id, name, legacy_audit_doc(time))
    audit_url = f"/api/sync/matches/{match_id}/docs/audit/alice/1"

    accepted = client.post(alias_url(match_id, "shooters/alice/stages/1/audit/accept"))
    assert accepted.status_code == 200, accepted.text
    stored = client.get(audit_url)
    assert stored.status_code == 200, stored.text
    return stored.json()["doc"]


def test_a_mirror_accept_does_not_invent_an_id(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The desktop is the sole minter for a mirror."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    doc = _accept_a_legacy_shot_on_a_mirror(client, "01JSHOTIDMIRRORMINT00001", "mirror-mint", time=6.5)
    assert "id" not in doc["shots"][0]
    # The accept itself still ran: this is a suppressed mint, not a
    # suppressed save.
    assert doc["shots"][0].get("interval_class")
    assert [e["kind"] for e in doc["audit_events"]] == ["accept"]


def test_two_sided_stamping_no_longer_duplicates_a_legacy_shot(
    tmp_path: Path,
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The failure Task 7 closes, driven through both real save boundaries.

    One legacy manual shot exists on both sides. The desktop nudges it to
    6.52 and saves; the phone accepts the mirror, still at 6.5. Before this
    task both sides minted -- ``manual-t6520`` and ``manual-t6500`` for the
    same shot -- both documents then read as fully stamped, the merge's
    unstamped-shot gate passed, and the shot unioned into two entries with
    no note at all.

    Now only the desktop mints. The mirror's copy reaches the merge
    unstamped, so the gate fires: one shot out, local's, and the refusal is
    stated. The desktop's next sync stamps its local doc in the migration
    pass and pushes it, which is what gets both sides onto one id.
    """
    # The desktop side is a *local*-mode app, so it has to be built with the
    # hosted env vars out of the way -- ``create_app`` reads them once, at
    # creation, and the hosted client above is already built.
    with pytest.MonkeyPatch.context() as env:
        env.delenv("SPLITSMITH_MODE", raising=False)
        env.delenv("SPLITSMITH_DATABASE_URL", raising=False)
        desktop = _stamp_through_the_save_boundary(tmp_path, "desktop", 6.52)
    assert desktop["shots"][0]["id"] == "manual-t6520"

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    mirror = _accept_a_legacy_shot_on_a_mirror(client, "01JSHOTIDMIRRORMINT00002", "two-sided", time=6.5)
    assert "id" not in mirror["shots"][0]

    # Base is what the desktop last pulled: the mirror as it stood.
    result = merge_audit_doc(
        copy.deepcopy(mirror),
        desktop,
        mirror,
        doc_key="audit/me/1",
        local_ts=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        remote_ts=datetime(2026, 8, 12, 13, 0, tzinfo=UTC),
    )
    assert [s["id"] for s in result.doc["shots"]] == ["manual-t6520"]  # one shot, not two
    assert any("without a persisted id" in note for note in result.notes)  # and not silent
    assert result.conflicts == []
