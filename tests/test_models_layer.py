"""Tests for the slim runtime model layer (issue #377 -- doc 03)."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import httpx
import pytest

from splitsmith.models import (
    ArtifactSpec,
    HashMismatch,
    HttpError,
    ModelArtifactsSpec,
    ModelError,
    ModelRegistry,
    NetworkUnreachable,
)
from splitsmith.models import cache as cache_mod
from splitsmith.models import download as download_mod
from splitsmith.models import registry as registry_mod

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_spec(
    *,
    payload: bytes = b"hello onnx",
    slug: str = "clap_audio_encoder",
    filename: str = "clap_audio_encoder.onnx",
    url: str | None = None,
    base_url: str | None = None,
) -> tuple[ModelArtifactsSpec, ArtifactSpec, bytes]:
    spec = ModelArtifactsSpec.model_validate(
        {
            "base_url": base_url,
            slug: {
                "filename": filename,
                "sha256": _sha256(payload),
                "size_bytes": len(payload),
                "url": url or f"https://example.test/artifacts/{_sha256(payload)}/{filename}",
            },
        }
    )
    artifact = spec.artifact(slug)
    assert artifact is not None
    return spec, artifact, payload


def _serve_payload(payload: bytes, *, url: str, status_code: int = 200):
    """Return a respx-style mock transport that serves ``payload`` from ``url``."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url, f"unexpected url {request.url}"
        return httpx.Response(status_code, content=payload)

    return httpx.MockTransport(handler)


# ----------------------------------------------------------------------
# Manifest parsing
# ----------------------------------------------------------------------


def test_manifest_parses_minimal_entry() -> None:
    spec, artifact, _ = _make_spec()
    assert artifact.filename == "clap_audio_encoder.onnx"
    assert artifact.size_bytes == len(b"hello onnx")
    assert len(artifact.sha256) == 64
    assert spec.slugs() == ["clap_audio_encoder"]


def test_manifest_artifact_returns_none_for_unknown_slug() -> None:
    spec, _, _ = _make_spec()
    assert spec.artifact("does-not-exist") is None


def test_manifest_composed_url_honours_base_url() -> None:
    payload = b"abc"
    spec = ModelArtifactsSpec.model_validate(
        {
            "base_url": "https://mirror.example/splitsmith",
            "clap_audio_encoder": {
                "filename": "clap_audio_encoder.onnx",
                "sha256": _sha256(payload),
                "size_bytes": len(payload),
                "url": "https://canonical.example/x",
            },
        }
    )
    artifact = spec.artifact("clap_audio_encoder")
    assert artifact is not None
    url = spec.composed_url(artifact)
    assert url == (
        "https://mirror.example/splitsmith/" f"artifacts/{_sha256(payload)}/clap_audio_encoder.onnx"
    )


def test_manifest_rejects_invalid_sha256() -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic raises ValidationError
        ArtifactSpec.model_validate(
            {"filename": "x.onnx", "sha256": "nothex", "size_bytes": 1, "url": "https://x.test/x"}
        )


# ----------------------------------------------------------------------
# Cache primitives
# ----------------------------------------------------------------------


def test_sha256_file_streams_chunks(tmp_path: Path) -> None:
    data = b"split" * 1024
    target = tmp_path / "blob"
    target.write_bytes(data)
    assert cache_mod.sha256_file(target) == _sha256(data)


def test_verify_artifact_true_when_present_and_matching(tmp_path: Path) -> None:
    _, artifact, payload = _make_spec()
    dest = cache_mod.artifact_path(artifact, root=tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    assert cache_mod.verify_artifact(artifact, root=tmp_path) is True


def test_verify_artifact_false_when_mismatched(tmp_path: Path) -> None:
    _, artifact, _ = _make_spec()
    dest = cache_mod.artifact_path(artifact, root=tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"wrong bytes")
    assert cache_mod.verify_artifact(artifact, root=tmp_path) is False


def test_install_verified_moves_and_deletes_on_mismatch(tmp_path: Path) -> None:
    _, artifact, payload = _make_spec()
    src = tmp_path / "tempfile.bin"
    src.write_bytes(payload)
    final = cache_mod.install_verified(artifact, src, root=tmp_path)
    assert final.exists()
    assert final.read_bytes() == payload
    assert not src.exists()

    bad_src = tmp_path / "bad.bin"
    bad_src.write_bytes(b"wrong")
    with pytest.raises(HashMismatch) as exc:
        cache_mod.install_verified(artifact, bad_src, root=tmp_path)
    assert exc.value.expected == artifact.sha256
    assert exc.value.actual == _sha256(b"wrong")
    assert not bad_src.exists()


def test_cache_lock_is_mutually_exclusive(tmp_path: Path) -> None:
    """Second concurrent ``cache_lock`` waits until the first exits."""
    other_acquired = threading.Event()
    release_first = threading.Event()
    first_acquired = threading.Event()
    errors: list[BaseException] = []

    def first():
        try:
            with cache_mod.cache_lock(root=tmp_path, timeout_s=5.0):
                first_acquired.set()
                release_first.wait(timeout=5.0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def second():
        first_acquired.wait(timeout=5.0)
        try:
            with cache_mod.cache_lock(root=tmp_path, timeout_s=5.0):
                other_acquired.set()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()

    # Briefly: second thread should not have acquired yet.
    assert not other_acquired.wait(timeout=0.2)
    release_first.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not errors, errors
    assert other_acquired.is_set()


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------


def test_download_to_streams_bytes(tmp_path: Path) -> None:
    payload = b"streamed-payload"
    url = "https://models.test/artifacts/abc/x.onnx"
    transport = _serve_payload(payload, url=url)
    client = httpx.Client(transport=transport)
    try:
        dest = tmp_path / "blob"
        download_mod.download_to(url, dest, client=client)
    finally:
        client.close()
    assert dest.read_bytes() == payload


def test_download_raises_http_error_on_5xx(tmp_path: Path) -> None:
    url = "https://models.test/down"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(HttpError) as exc:
            download_mod.download_to(url, tmp_path / "blob", client=client)
    finally:
        client.close()
    assert exc.value.status_code == 503


def test_download_raises_network_unreachable_on_connect_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consecutive ConnectError raises -> NetworkUnreachable."""
    monkeypatch.setattr(download_mod, "_RETRY_BACKOFF_S", 0.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(NetworkUnreachable):
            download_mod.download_to("https://models.test/down", tmp_path / "blob", client=client)
    finally:
        client.close()


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


def test_registry_resolve_uses_cached_file(tmp_path: Path) -> None:
    spec, artifact, payload = _make_spec()
    dest = cache_mod.artifact_path(artifact, root=tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)

    registry = ModelRegistry(spec, root=tmp_path)
    resolved = registry.resolve("clap_audio_encoder")
    assert resolved == dest
    assert resolved.read_bytes() == payload


def test_registry_downloads_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = b"hello onnx"
    url = f"https://example.test/artifacts/{_sha256(payload)}/clap_audio_encoder.onnx"
    spec, _, _ = _make_spec(payload=payload, url=url)
    transport = _serve_payload(payload, url=url)

    def fake_download(target_url, dest, **kwargs):
        client = httpx.Client(transport=transport)
        try:
            return download_mod.download_to(target_url, dest, client=client, **kwargs)
        finally:
            client.close()

    monkeypatch.setattr(registry_mod, "download_to", fake_download)
    registry = ModelRegistry(spec, root=tmp_path)
    resolved = registry.resolve("clap_audio_encoder")
    assert resolved.exists()
    assert resolved.read_bytes() == payload


def test_registry_raises_hash_mismatch_on_bad_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = b"hello onnx"
    bad = b"tampered"
    url = f"https://example.test/artifacts/{_sha256(payload)}/clap_audio_encoder.onnx"
    spec, _, _ = _make_spec(payload=payload, url=url)
    transport = _serve_payload(bad, url=url)

    def fake_download(target_url, dest, **kwargs):
        client = httpx.Client(transport=transport)
        try:
            return download_mod.download_to(target_url, dest, client=client, **kwargs)
        finally:
            client.close()

    monkeypatch.setattr(registry_mod, "download_to", fake_download)
    registry = ModelRegistry(spec, root=tmp_path)
    with pytest.raises(HashMismatch):
        registry.resolve("clap_audio_encoder")
    # File must not have been installed.
    assert cache_mod.artifact_path(spec.artifact("clap_audio_encoder"), root=tmp_path).exists() is False


def test_registry_status_reports_missing_and_present(tmp_path: Path) -> None:
    spec, artifact, payload = _make_spec()
    registry = ModelRegistry(spec, root=tmp_path)
    statuses = {s.slug: s for s in registry.status()}
    assert statuses["clap_audio_encoder"].state == "missing"

    dest = cache_mod.artifact_path(artifact, root=tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    statuses = {s.slug: s for s in registry.status()}
    assert statuses["clap_audio_encoder"].state == "present"


def test_registry_status_reports_mismatched(tmp_path: Path) -> None:
    spec, artifact, _ = _make_spec()
    dest = cache_mod.artifact_path(artifact, root=tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"junk")
    registry = ModelRegistry(spec, root=tmp_path)
    statuses = {s.slug: s for s in registry.status()}
    assert statuses["clap_audio_encoder"].state == "mismatched"


def test_registry_unknown_slug_raises_model_error(tmp_path: Path) -> None:
    spec, _, _ = _make_spec()
    registry = ModelRegistry(spec, root=tmp_path)
    with pytest.raises(ModelError):
        registry.resolve("unknown-slug")


def test_registry_remove_deletes_cached_file(tmp_path: Path) -> None:
    spec, artifact, payload = _make_spec()
    dest = cache_mod.artifact_path(artifact, root=tmp_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    registry = ModelRegistry(spec, root=tmp_path)
    registry.remove("clap_audio_encoder")
    assert not dest.exists()


# ----------------------------------------------------------------------
# Calibration loader
# ----------------------------------------------------------------------


def test_load_spec_from_calibration_returns_none_when_block_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "calibration.json"
    fake.write_text(json.dumps({"version": 1}))

    class _FakeRuntime:
        def artifact(self, name):  # noqa: ARG002
            return fake

    monkeypatch.setattr(registry_mod, "process_runtime", lambda: _FakeRuntime())
    assert registry_mod.load_spec_from_calibration() is None


def test_load_spec_from_calibration_returns_spec_when_block_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"hello"
    sha = _sha256(payload)
    fake = tmp_path / "calibration.json"
    fake.write_text(
        json.dumps(
            {
                "model_artifacts": {
                    "clap_audio_encoder": {
                        "filename": "x.onnx",
                        "sha256": sha,
                        "size_bytes": len(payload),
                        "url": f"https://x.test/{sha}/x.onnx",
                    }
                }
            }
        )
    )

    class _FakeRuntime:
        def artifact(self, name):  # noqa: ARG002
            return fake

    monkeypatch.setattr(registry_mod, "process_runtime", lambda: _FakeRuntime())
    spec = registry_mod.load_spec_from_calibration()
    assert spec is not None
    assert spec.slugs() == ["clap_audio_encoder"]


# ----------------------------------------------------------------------
# /api/models/status endpoint
# ----------------------------------------------------------------------


def test_models_status_endpoint_reports_unavailable_when_no_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    monkeypatch.setattr(registry_mod, "_default_registry", None)
    monkeypatch.setattr(registry_mod, "load_spec_from_calibration", lambda: None)

    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.get("/api/models/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"available": False, "artifacts": [], "missing": [], "mismatched": []}


def test_models_status_endpoint_lists_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    spec, artifact, payload = _make_spec()
    dest = cache_mod.artifact_path(artifact, root=tmp_path / "cache")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    registry = ModelRegistry(spec, root=tmp_path / "cache")
    monkeypatch.setattr(registry_mod, "_default_registry", registry)

    app = create_app(project_root=tmp_path / "match", project_name="x")
    client = TestClient(app)
    resp = client.get("/api/models/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["missing"] == []
    assert body["mismatched"] == []
    assert body["artifacts"][0]["slug"] == "clap_audio_encoder"
    assert body["artifacts"][0]["state"] == "present"
