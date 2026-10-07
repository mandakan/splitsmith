"""The Look editor's server side (issue #1264): a draft previewed through the
export-preview route (``draft``, ``at``, the ``sting`` card) and
``POST /api/looks/{name}/duplicate``, local and hosted.

Rasterization is stubbed as in ``test_export_preview_api``; the stub records
each template call's palette, data and seek time.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import look_store, looks
from splitsmith import runtime as runtime_module
from splitsmith.ui import export_preview_api, server
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_ui_server import _seed_match_export_project

ROUTE = "/api/shooters/me/export-preview"


class _Recorder:
    calls: list[dict] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def render_template(
        self, template, *, context, width: int, height: int, at: float | None = None
    ) -> bytes:
        self.calls.append(
            {"template": Path(template).name, "theme": context.theme, "data": context.data, "at": at}
        )
        return self.png("", width=width, height=height)

    def engine_version(self) -> str:
        return "fake"


@contextmanager
def _factory():
    yield _Recorder()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _Recorder.calls = []
    client, _root = _seed_match_export_project(tmp_path, stage_count=2)
    yield client
    runtime_module._clear_runtime_cache()


def _draft(**over) -> dict:
    colors = {k: list(v) for k, v in looks.load_look("splitsmith").manifest.colors.items()}
    return {"label": "Club", "base": "splitsmith", "colors": colors, **over}


def _put_club(client, **over) -> None:
    r = client.put("/api/looks/club", json=_draft(**over))
    assert r.status_code in (200, 201), r.text


# --- the draft preview -------------------------------------------------------------------


def test_a_draft_draws_in_its_own_colours_and_styles_without_saving(client) -> None:
    _put_club(client)
    accent = [10, 200, 30]
    draft = _draft(colors={**_draft()["colors"], "accent": accent}, styles={"slate": "rise"})
    r = client.post(
        ROUTE, json={"card": "slate", "stage_number": 1, "width": 480, "look": "club", "draft": draft}
    )
    assert r.status_code == 200, r.text
    call = _Recorder.calls[-1]
    assert call["theme"]["accent"] == "#0ac81e"
    assert call["template"] == "card-rise.html"
    saved = client.get("/api/looks/club").json()["body"]
    assert saved["colors"]["accent"] != accent and saved["styles"] == {}


def test_the_draft_and_the_time_move_the_cache_key(client) -> None:
    _put_club(client)
    body = {"card": "title", "stage_number": 1, "width": 480, "look": "club", "draft": _draft()}
    client.post(ROUTE, json=body)
    client.post(ROUTE, json=body)
    assert len(_Recorder.calls) == 1
    client.post(ROUTE, json={**body, "draft": _draft(label="Other")})
    client.post(ROUTE, json={**body, "at": 0.4})
    assert len(_Recorder.calls) == 3
    assert _Recorder.calls[-1]["at"] == 0.4 and _Recorder.calls[0]["at"] is None


def test_saving_a_look_moves_its_plain_previews_key(client) -> None:
    """The rail previews a saved Look with no draft; after Save (a new colour,
    a new font) it must draw again, not serve the card from before."""
    _put_club(client)
    body = {"card": "title", "stage_number": 1, "width": 480, "look": "club"}
    client.post(ROUTE, json=body)
    client.post(ROUTE, json=body)
    assert len(_Recorder.calls) == 1
    _put_club(client, colors={**_draft()["colors"], "accent": [10, 200, 30]})
    client.post(ROUTE, json=body)
    assert len(_Recorder.calls) == 2
    assert _Recorder.calls[-1]["theme"]["accent"] == "#0ac81e"


def test_the_sting_card_draws_the_looks_sting_with_the_transition(client) -> None:
    r = client.post(
        ROUTE, json={"card": "sting", "stage_number": 1, "width": 480, "variant": "wipe", "at": 0.5}
    )
    assert r.status_code == 200, r.text
    call = _Recorder.calls[-1]
    assert call["template"] == "sting-wipe.html" and call["at"] == 0.5
    assert call["data"]["transition"]["kind"] == "sting:wipe"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (480, 270)


def test_a_sting_the_look_lacks_is_a_404(client) -> None:
    r = client.post(ROUTE, json={"card": "sting", "stage_number": 1, "width": 480, "variant": "nope"})
    assert r.status_code == 404


def test_a_bad_draft_is_a_422_naming_the_field(client) -> None:
    _put_club(client)
    r = client.post(
        ROUTE,
        json={"card": "title", "stage_number": 1, "look": "club", "draft": _draft(accent_series=["red"])},
    )
    assert r.status_code == 422 and "accent_series" in r.text


# --- duplicate ----------------------------------------------------------------------------


def test_duplicate_a_shipped_look_locally_keeps_its_templates(client) -> None:
    r = client.post("/api/looks/club/duplicate", json={"source": "splitsmith"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "club"
    look = looks.load_look("club")
    assert look.source == "user" and look.own_template("title_page", "rise") is not None
    assert client.post("/api/looks/club/duplicate", json={"source": "splitsmith"}).status_code == 409


def test_duplicate_refuses_an_unknown_source(client) -> None:
    assert client.post("/api/looks/club/duplicate", json={"source": "nope"}).status_code == 404


def test_hosted_duplicate_makes_a_manifest_on_the_shipped_base(
    hosted_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "user_looks_cache_root", lambda: tmp_path / "user-looks")
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.post("/api/looks/club/duplicate", json={"source": "clean"})
    assert r.status_code == 201, r.text
    body = client.get("/api/looks/club").json()["body"]
    assert body["base"] == "clean"
    assert body["colors"] == {k: list(v) for k, v in looks.load_look("clean").manifest.colors.items()}
    r = client.post("/api/looks/team/duplicate", json={"source": "club"})
    assert r.status_code == 201, r.text
    assert client.get("/api/looks/team").json()["body"]["base"] == "clean"


# --- draft_look ----------------------------------------------------------------------------


def test_draft_look_keeps_the_saved_looks_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    from splitsmith import look_tools

    look_tools.new_look("mine", starter="still")
    saved = looks.load_look("mine")
    body = look_store.body_from_manifest(saved.manifest).model_copy(update={"label": "Draft"})
    draft = look_store.draft_look(saved, body, tmp_path / "work")
    assert draft.label == "Draft" and draft.name == "mine"
    assert (
        looks.template_for(draft, "title_page").read_bytes() == saved.own_template("title_page").read_bytes()
    )
