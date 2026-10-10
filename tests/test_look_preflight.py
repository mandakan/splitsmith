"""The Export page's Look preflight (issue #1276): the saved Look's check is
cached by the folder's content, so choosing a Look again launches no browser,
and any edit to its files checks again."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import looks
from splitsmith import runtime as runtime_module
from splitsmith.overlay_raster import TemplateProbe
from splitsmith.ui import looks_api

from .test_ui_server import _match_create_app, _MatchClient


class _Prober:
    launches = 0

    def __enter__(self):
        type(self).launches += 1
        return self

    def __exit__(self, *exc) -> None:
        return None

    def probe_template(
        self, template: Path, *, context, width: int, height: int, at: float | None = None
    ) -> TemplateProbe:
        broken = "BOOM" in template.read_text(encoding="utf-8")
        return TemplateProbe(errors=("line 2: boom",) if broken else (), families=("Splitsmith Display",))

    def render_template_timeline(
        self, template: Path, *, context, width: int, height: int, plan
    ):  # noqa: ANN001
        """A HUD that is still everywhere: the same frame at every time."""
        from splitsmith.overlay_raster import TemplateFrames

        times = list(plan(0.0))
        return TemplateFrames(
            duration=0.0, frame_count=len(times), width=1, height=1, frames=iter([b"x"] * len(times))
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(looks_api, "prober_factory", _Prober)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _Prober.launches = 0
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="Preflight"))
    assert client.post("/api/looks/club/duplicate", json={"source": "splitsmith"}).status_code == 201
    yield client
    runtime_module._clear_runtime_cache()


def test_the_saved_looks_check_is_cached_by_its_content(client) -> None:
    first = client.post("/api/looks/club/check", json={})
    assert first.status_code == 200 and first.json()["errors"] == 0
    second = client.post("/api/looks/club/check", json={})
    assert second.json() == first.json()
    assert _Prober.launches == 1


def test_an_edited_template_is_checked_again(client) -> None:
    client.post("/api/looks/club/check", json={})
    # A duplicate draws the shipped templates; editing one writes it into
    # the Look and names it in the manifest, as the Templates tab does.
    root = looks.load_look("club").root
    shipped = looks.shipped_looks_dir() / "splitsmith" / "card.html"
    (root / "card.html").write_text(
        shipped.read_text(encoding="utf-8") + "<script>BOOM</script>", encoding="utf-8"
    )
    manifest = json.loads((root / "look.json").read_text(encoding="utf-8"))
    manifest["slots"] = {"title_page": {"default": "card.html"}}
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    again = client.post("/api/looks/club/check", json={})
    assert _Prober.launches == 2
    assert again.json()["errors"] >= 1
    assert any("line 2: boom" in i["message"] for i in again.json()["items"])


def test_a_draft_is_never_served_from_the_cache(client) -> None:
    client.post("/api/looks/club/check", json={})
    edit = {"slot": "slate", "variant": "default", "content": "<!doctype html><script>BOOM</script>"}
    client.post("/api/looks/club/check", json={"templates": [edit]})
    client.post("/api/looks/club/check", json={"templates": [edit]})
    assert _Prober.launches == 3


def test_an_edited_shared_script_is_checked_again(client, tmp_path: Path, monkeypatch) -> None:
    """The Look's templates run the shipped ``_shared/`` scripts, so a change
    there must miss the cache even though the Look's folder is unchanged
    (#1337). Edits a copy of the shipped tree, never the shipped files."""
    import shutil

    client.post("/api/looks/club/check", json={})
    root = tmp_path / "shipped"
    shutil.copytree(looks.shipped_looks_dir(), root)
    monkeypatch.setattr(looks, "shipped_looks_dir", lambda: root)
    client.post("/api/looks/club/check", json={})
    assert _Prober.launches == 1
    fit = root / "_shared" / "fit.js"
    fit.write_bytes(fit.read_bytes() + b"\n// edited\n")
    client.post("/api/looks/club/check", json={})
    assert _Prober.launches == 2
