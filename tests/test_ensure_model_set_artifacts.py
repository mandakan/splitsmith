"""A worker fetches the model set's artifacts once at startup (spec 2026-10-26).

Detection never downloads (``api._load_runtime`` falls back to ``base``), and
hosted mode has no boot prefetch, so without this a worker whose cache is
empty would detect with ``base`` for good.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

from splitsmith import ensemble
from splitsmith.models import ModelArtifactsSpec, ModelRegistry, NetworkUnreachable
from splitsmith.models import cache as cache_mod
from splitsmith.models import registry as registry_mod

_PAYLOADS = {"clap_audio_encoder": b"audio", "clap_text_embeddings": b"text"}


class _CountingRegistry(ModelRegistry):
    def __init__(self, *args, fail: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.resolved: list[str] = []
        self.fail = fail

    def resolve(self, slug, *, progress=None):  # type: ignore[override]
        self.resolved.append(slug)
        if self.fail:
            raise NetworkUnreachable("no route to host")
        art = self.spec.artifact(slug)
        assert art is not None
        path = cache_mod.artifact_path(art, root=self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PAYLOADS[slug])
        return path


def _registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> _CountingRegistry:
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
    registry = _CountingRegistry(spec, root=tmp_path / "models", **kwargs)
    monkeypatch.setattr(registry_mod, "_default_registry", registry)
    return registry


def test_base_fetches_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_MODEL_SET", "base")
    registry = _registry(tmp_path, monkeypatch)
    assert ensemble.ensure_model_set_artifacts() == []
    assert registry.resolved == []


def test_clap_fetches_what_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_MODEL_SET", "clap")
    registry = _registry(tmp_path, monkeypatch)
    fetched = ensemble.ensure_model_set_artifacts()
    assert sorted(fetched) == sorted(_PAYLOADS)
    assert all(s.state == "present" for s in registry.status())


def test_clap_with_the_files_cached_fetches_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPLITSMITH_MODEL_SET", "clap")
    registry = _registry(tmp_path, monkeypatch)
    ensemble.ensure_model_set_artifacts()
    registry.resolved.clear()
    assert ensemble.ensure_model_set_artifacts() == []
    assert registry.resolved == []


def test_a_failed_fetch_warns_and_lets_the_worker_start(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("SPLITSMITH_MODEL_SET", "clap")
    registry = _registry(tmp_path, monkeypatch, fail=True)
    with caplog.at_level(logging.WARNING):
        assert ensemble.ensure_model_set_artifacts() == []
    assert registry.resolved
    assert "detecting with base" in caplog.text


def test_the_worker_command_fetches_before_draining(monkeypatch) -> None:
    from typer.testing import CliRunner

    from splitsmith import cli, queue

    order: list[str] = []
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", "postgresql+asyncpg://x/y")
    monkeypatch.setattr(ensemble, "ensure_model_set_artifacts", lambda: order.append("fetch") or [])

    async def _drain(*_a, **_k) -> None:
        order.append("drain")

    monkeypatch.setattr(queue, "run_worker", _drain)
    result = CliRunner().invoke(cli.app, ["worker", "--one-shot"])
    assert result.exit_code == 0, result.output
    assert order == ["fetch", "drain"]
