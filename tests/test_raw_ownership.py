"""A hosted raw object has exactly one owning shooter (#562).

On desktop a raw video is a file under one shooter's project dir, so the
filesystem enforced single ownership for free. The hosted port kept
"attach = append a manifest entry to a project doc" while the bytes live
in a shared per-user ``raw/`` pool, and lost that invariant -- in prod the
same 10 uploads ended up attached to two shooters in one match.

#562 settled the model as **option C**: keep the shared pool, treat
attachment as an explicit single-owner claim, and make transfer the only
way ownership changes. These tests pin that model at its three seams --
the claim (attach), the view (the available-uploads list), and the
transfer (move-shooter) -- and specifically that all three agree, since
each used to answer "who owns this?" for itself.

``test_take_endpoints.py`` already covers the claim and the view in
isolation. What is pinned here is that they stay consistent *through a
transfer*, which is the case where independent derivations drift.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from splitsmith.match_project import MatchProject

from .test_ui_server import _match_create_app, _MatchClient


def _two_shooter_match(tmp_path: Path) -> tuple[Any, str, str, str]:
    """A match with two shooters and one object in the hosted raw pool."""
    app = _match_create_app(project_root=tmp_path / "match", project_name="Ownership Test")
    client = _MatchClient(app)
    match_id = app.state.splitsmith_state.matches.known_ids()[0]

    resp = client.post("/api/match/shooters", json={"name": "Other Shooter"})
    assert resp.status_code == 200, resp.text
    other = next(s["slug"] for s in resp.json()["shooters"] if s["slug"] != "me")

    storage = MagicMock()
    storage.stat.return_value = SimpleNamespace(size=1234)
    storage.list.return_value = [
        SimpleNamespace(path="raw/VID.mp4", size=1234, last_modified=None, etag=None)
    ]
    app.state.splitsmith_state.storage = storage
    return client, match_id, other, "raw/VID.mp4"


def _owner_in_list(client: Any, match_id: str, path: str) -> str | None:
    resp = client.get(f"/api/matches/{match_id}/me/raw/list")
    assert resp.status_code == 200, resp.text
    uploads = {u["path"]: u for u in resp.json()["uploads"]}
    return uploads[path]["attached_to"]


def _attach(client: Any, slug: str, filename: str = "VID.mp4") -> Any:
    return client.post(f"/api/shooters/{slug}/raw-videos/attach", json={"filename": filename})


def test_ownership_follows_a_move_to_another_shooter(tmp_path: Path) -> None:
    """Transfer is the only thing that reassigns ownership, and every
    surface must agree afterwards -- the list's label and the attach
    guard's verdict both have to name the new owner."""
    client, match_id, other, path = _two_shooter_match(tmp_path)
    assert _attach(client, "me").status_code == 200

    resp = client.post(
        "/api/match/videos/move-shooter",
        json={"source_slug": "me", "target_slug": other, "video_paths": [path]},
    )
    assert resp.status_code == 200, resp.text

    assert _owner_in_list(client, match_id, path) == other
    # And the claim is now enforced in the other direction: the shooter
    # that used to own it is refused, naming the new owner.
    refused = _attach(client, "me")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["shooter"] == other


def test_reattaching_to_the_current_owner_is_not_a_conflict(tmp_path: Path) -> None:
    """Single-owner means one *other* owner. Re-attaching an object to the
    shooter that already holds it merges rather than 409s, which is what
    keeps the ingest flow idempotent under a double-click or a retry."""
    client, _match_id, _other, _path = _two_shooter_match(tmp_path)
    assert _attach(client, "me").status_code == 200

    again = _attach(client, "me")

    assert again.status_code == 200, again.text


def test_a_doubly_owned_object_is_still_refused_to_a_third_party(tmp_path: Path) -> None:
    """The corrupt state this issue was filed about must stay refused.

    Matches created before the claim guard shipped can already hold the
    same object on two shooters -- that is the prod failure in #562's
    body. Whatever derives ownership must keep reporting a conflict for
    such an object rather than treating "the caller is *an* owner" as
    permission, which would silently normalise the corruption.
    """
    client, _match_id, other, path = _two_shooter_match(tmp_path)
    assert _attach(client, "me").status_code == 200

    # Manufacture the pre-guard state by writing the second claim
    # straight onto the other shooter's doc on disk, bypassing the
    # endpoint that would refuse it. ``state.shooter_project`` needs a
    # request-scoped match root, so go through MatchProject directly.
    owner_root = tmp_path / "match" / "shooters" / "me"
    victim_root = tmp_path / "match" / "shooters" / other
    owner_rv = MatchProject.load(owner_root).find_raw_video(path)
    assert owner_rv is not None
    victim = MatchProject.load(victim_root)
    victim.raw_videos.append(owner_rv.model_copy(deep=True))
    victim.save(victim_root)

    refused = _attach(client, "me")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["shooter"] == other


def test_an_unclaimed_object_has_no_owner(tmp_path: Path) -> None:
    """The list distinguishes "nobody owns this" from "someone does" --
    the SPA hides on that field, so a wrong null hides a clip from every
    shooter or offers a claimed one to all of them."""
    client, match_id, _other, path = _two_shooter_match(tmp_path)

    assert _owner_in_list(client, match_id, path) is None
