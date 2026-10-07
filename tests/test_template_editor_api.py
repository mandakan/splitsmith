"""The template editor's server side (issue #1265), local only: a Look's
templates read and written per slot and variant, the data each one receives,
a check of the unsaved draft, unsaved template text in the draft preview,
and the Look's folder revealed. Hosted answers 404 (or 403 for the preview's
template text) until the sandbox ships (#1266).
"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import looks
from splitsmith import runtime as runtime_module
from splitsmith.overlay_raster import TemplateProbe
from splitsmith.ui import export_preview_api, looks_api
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_ui_server import _seed_match_export_project


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
            {"template": Path(template).name, "html": Path(template).read_text(encoding="utf-8")}
        )
        return self.png("", width=width, height=height)

    def engine_version(self) -> str:
        return "fake"


class _Prober:
    seen: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def probe_template(self, template: Path, *, context, width: int, height: int) -> TemplateProbe:
        html = template.read_text(encoding="utf-8")
        self.seen.append((template.name, html))
        errors = ("line 3: boom",) if "BOOM" in html else ()
        return TemplateProbe(errors=errors, families=("Splitsmith Display",))


@contextmanager
def _factory():
    yield _Recorder()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setattr(looks_api, "prober_factory", _Prober)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _Recorder.calls = []
    _Prober.seen = []
    client, _root = _seed_match_export_project(tmp_path, stage_count=2)
    assert client.post("/api/looks/club/duplicate", json={"source": "clean"}).status_code == 201
    yield client
    runtime_module._clear_runtime_cache()


def _templates(client) -> dict[tuple[str, str], dict]:
    r = client.get("/api/looks/club/templates")
    assert r.status_code == 200, r.text
    return {(t["slot"], t["variant"]): t for t in r.json()["templates"]}


def test_lists_every_slot_and_variant_with_whose_template_it_is(client) -> None:
    templates = _templates(client)
    title = templates[("title_page", "default")]
    assert title["own"] is False and "<" in title["content"]
    assert ("title_page", "rise") in templates and ("transition", "wipe") in templates
    starters = client.get("/api/looks/club/templates").json()["starters"]
    assert [s["name"] for s in starters] == ["animated", "lower-third", "still", "sting"]
    assert all(s["content"].startswith("<!") for s in starters)


def test_saving_a_borrowed_slot_makes_the_look_its_own(client) -> None:
    html = "<!doctype html><p>mine</p>"
    r = client.put("/api/looks/club/templates", json={"slot": "slate", "variant": "default", "content": html})
    assert r.status_code == 200, r.text
    look = looks.load_look("club")
    own = look.own_template("slate", "default")
    assert own is not None and own.name == "slate-default.html" and own.read_text(encoding="utf-8") == html
    assert _templates(client)[("slate", "default")]["own"] is True
    again = "<!doctype html><p>again</p>"
    client.put("/api/looks/club/templates", json={"slot": "slate", "variant": "default", "content": again})
    assert looks.load_look("club").own_template("slate", "default").read_text(encoding="utf-8") == again


def test_a_shipped_look_and_a_bad_slot_are_refused(client) -> None:
    body = {"slot": "slate", "variant": "default", "content": "<p>x</p>"}
    assert client.put("/api/looks/splitsmith/templates", json=body).status_code == 404
    assert client.put("/api/looks/club/templates", json={**body, "slot": "summary"}).status_code == 422
    assert client.put("/api/looks/club/templates", json={**body, "variant": "Bad Name"}).status_code == 422
    assert client.put("/api/looks/club/templates", json={**body, "content": "x" * 300_000}).status_code == 422


def test_samples_are_what_the_template_receives(client) -> None:
    r = client.get("/api/looks/club/samples", params={"slot": "title_page", "variant": "default"})
    assert r.status_code == 200, r.text
    cases = r.json()["cases"]
    assert [c["case"] for c in cases] == [
        "one shooter with a logo",
        "two shooters, no logo",
        "a long stage name",
    ]
    first = cases[0]["context"]
    assert first["data"]["card"]["slot"] == "title_page" and "css" not in first["engine"]
    sting = client.get("/api/looks/club/samples", params={"slot": "transition", "variant": "wipe"}).json()
    assert sting["cases"][0]["context"]["data"]["transition"]["kind"] == "sting:wipe"


def test_check_runs_on_the_unsaved_draft(client) -> None:
    body = {
        "templates": [
            {"slot": "slate", "variant": "default", "content": "<!doctype html><script>BOOM</script>"}
        ]
    }
    r = client.post("/api/looks/club/check", json=body)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    slate = [i for i in items if i["subject"].startswith("slate default")]
    assert slate and slate[0]["level"] == "error" and "line 3: boom" in slate[0]["message"]
    assert looks.load_look("club").own_template("slate", "default") is None


def test_the_preview_draws_unsaved_template_text(client) -> None:
    html = "<!doctype html><p>draft text</p>"
    r = client.post(
        "/api/shooters/me/export-preview",
        json={
            "card": "slate",
            "stage_number": 1,
            "width": 480,
            "look": "club",
            "templates": [{"slot": "slate", "variant": "default", "content": html}],
        },
    )
    assert r.status_code == 200, r.text
    assert _Recorder.calls[-1]["html"] == html
    assert looks.load_look("club").own_template("slate", "default") is None


def test_reveal_opens_the_looks_folder(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import server

    opened: list[Path] = []
    monkeypatch.setattr(server, "_reveal_in_file_manager", opened.append)
    assert client.post("/api/looks/club/reveal").status_code == 200
    assert opened == [looks.load_look("club").root]
    assert client.post("/api/looks/splitsmith/reveal").status_code == 404


def test_hosted_has_no_template_editing(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    assert client.get("/api/looks/club/templates").status_code == 404
    body = {"slot": "slate", "variant": "default", "content": "<p>x</p>"}
    assert client.put("/api/looks/club/templates", json=body).status_code == 404
    r = client.post(
        "/api/shooters/me/export-preview",
        json={"card": "slate", "stage_number": 1, "templates": [body]},
    )
    assert r.status_code in (403, 404)


def test_page_error_messages_carry_the_template_line() -> None:
    from splitsmith.overlay_raster import describe_page_error

    class _Err:
        message = "Cannot read properties of undefined (reading 'forEach')"
        stack = "TypeError: ...\n    at file:///tmp/x/card-rise.html:9:22\n    at file:///shared/fit.js:3:1"

    assert describe_page_error(_Err(), "card-rise.html") == f"line 9: {_Err.message}"
    assert describe_page_error(_Err(), "other.html") == _Err.message
    assert json.dumps(describe_page_error(Exception("plain"), "x.html")) == '"plain"'
