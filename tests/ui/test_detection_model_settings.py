"""The detection model preference: GET/PUT /api/settings/detection-model (spec 2026-10-26)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from splitsmith import user_config
from splitsmith.models import ModelArtifactsSpec, ModelRegistry
from splitsmith.models import cache as cache_mod
from splitsmith.models import registry as registry_mod

_PAYLOADS = {"clap_audio_encoder": b"audio", "clap_text_embeddings": b"text"}


def _registry(tmp_path: Path, *, cached: bool) -> ModelRegistry:
    spec = ModelArtifactsSpec.model_validate(
        {
            slug: {
                "filename": f"{slug}.bin",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "url": f"https://example.test/{slug}",
            }
            for slug, data in _PAYLOADS.items()
        }
    )
    root = tmp_path / "models"
    if cached:
        for slug, data in _PAYLOADS.items():
            art = spec.artifact(slug)
            assert art is not None
            path = cache_mod.artifact_path(art, root=root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return ModelRegistry(spec, root=root)


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A local app whose model download job only records that it ran."""
    from splitsmith.ui import server as server_mod
    from splitsmith.ui.server import create_app
    from tests.conftest import scaffold_match

    monkeypatch.delenv("SPLITSMITH_MODEL_SET", raising=False)
    downloads: list[str] = []
    monkeypatch.setattr(server_mod, "_run_model_download_job", lambda handle: downloads.append(handle.id))
    root, _ = scaffold_match(tmp_path, name="x")
    app = create_app(project_root=root, project_name="x")
    return TestClient(app), app.state.splitsmith_state, downloads


def test_get_reports_base_by_default(app_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "_default_registry", _registry(tmp_path, cached=False))
    client, _state, _downloads = app_client
    r = client.get("/api/settings/detection-model")
    assert r.status_code == 200
    assert r.json() == {"model_set": "base", "required": [], "present": True, "active_job": None}


def test_put_clap_saves_and_submits_one_download(app_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "_default_registry", _registry(tmp_path, cached=False))
    client, state, downloads = app_client
    r = client.put("/api/settings/detection-model", json={"model_set": "clap"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_set"] == "clap"
    assert body["required"] == ["clap_audio_encoder", "clap_text_embeddings"]
    assert body["present"] is False
    assert body["active_job"] is not None
    assert user_config.load_global_prefs().detection_model_set == "clap"
    state.jobs.wait_for_drain(timeout_s=2.0)
    assert len(downloads) == 1


def test_put_clap_does_not_submit_a_second_download(app_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "_default_registry", _registry(tmp_path, cached=False))
    client, state, _downloads = app_client
    submitted: list[str] = []
    real_submit = state.jobs.submit

    async def _counting_submit(*a, **kw):
        submitted.append(kw.get("kind", ""))
        return await real_submit(*a, **kw)

    async def _active(**_kw):
        return SimpleNamespace(id="running")

    monkeypatch.setattr(state.jobs, "submit", _counting_submit)
    monkeypatch.setattr(state.jobs, "find_active", _active)
    client.put("/api/settings/detection-model", json={"model_set": "clap"})
    assert submitted == []


def test_put_clap_with_the_files_cached_downloads_nothing(app_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "_default_registry", _registry(tmp_path, cached=True))
    client, state, downloads = app_client
    body = client.put("/api/settings/detection-model", json={"model_set": "clap"}).json()
    assert body["present"] is True and body["active_job"] is None
    state.jobs.wait_for_drain(timeout_s=1.0)
    assert downloads == []


def test_put_base_turns_it_off_and_keeps_the_files(app_client, tmp_path, monkeypatch) -> None:
    registry = _registry(tmp_path, cached=True)
    monkeypatch.setattr(registry_mod, "_default_registry", registry)
    client, _state, _downloads = app_client
    client.put("/api/settings/detection-model", json={"model_set": "clap"})
    body = client.put("/api/settings/detection-model", json={"model_set": "base"}).json()
    assert body == {"model_set": "base", "required": [], "present": True, "active_job": None}
    assert user_config.load_global_prefs().detection_model_set == "base"
    assert all(s.state == "present" for s in registry.status())


def test_an_unknown_model_set_is_refused(app_client) -> None:
    client, _state, _downloads = app_client
    assert client.put("/api/settings/detection-model", json={"model_set": "pann"}).status_code == 422
    assert client.put("/api/settings/detection-model", json={"model_set": "base", "x": 1}).status_code == 422


def test_the_route_is_local_only(app_client, monkeypatch) -> None:
    from splitsmith.ui import server

    client, _state, _downloads = app_client
    monkeypatch.setenv(server.SPLITSMITH_MODE_ENV, "hosted")
    assert client.get("/api/settings/detection-model").status_code == 404
    assert client.put("/api/settings/detection-model", json={"model_set": "clap"}).status_code == 404
