"""Fixture audio stored in R2 by content hash (#1363)."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fixture_audio.py"


@pytest.fixture()
def fa(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("fixture_audio", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fixture_audio"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "FIXTURES", tmp_path)
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "audio.lock.json")
    monkeypatch.setattr(mod, "GITIGNORE", tmp_path / ".gitignore")
    return mod


@pytest.fixture()
def bucket(fa, monkeypatch):
    stored: dict[str, bytes] = {}
    monkeypatch.setattr(fa, "_object_exists", lambda _url, digest: digest in stored)
    monkeypatch.setattr(fa, "_upload", lambda path, digest: stored.__setitem__(digest, path.read_bytes()))
    return stored


def test_push_uploads_by_hash_records_and_ignores(fa, bucket, tmp_path):
    (tmp_path / "stage-shots-a.wav").write_bytes(b"RIFF-a")
    (tmp_path / ".gitignore").write_text("*.peaks\n")
    fa.push(["stage-shots-a"])
    files = fa.load_manifest()
    digest = fa.sha256(tmp_path / "stage-shots-a.wav")
    assert files == {"stage-shots-a.wav": {"sha256": digest, "bytes": 6}}
    assert bucket == {digest: b"RIFF-a"}
    ignore = (tmp_path / ".gitignore").read_text()
    assert "*.peaks" in ignore and "/stage-shots-a.wav" in ignore


def test_push_skips_an_object_already_stored(fa, bucket, tmp_path, monkeypatch):
    (tmp_path / "a.wav").write_bytes(b"same")
    bucket[fa.sha256(tmp_path / "a.wav")] = b"same"
    monkeypatch.setattr(fa, "_upload", lambda *_a: pytest.fail("uploaded twice"))
    fa.push(["a.wav"])
    assert "a.wav" in fa.load_manifest()


def test_push_twice_keeps_one_managed_block(fa, bucket, tmp_path):
    for n in ("a", "b"):
        (tmp_path / f"stage-shots-{n}.wav").write_bytes(n.encode())
        fa.push([f"stage-shots-{n}.wav"])
    ignore = (tmp_path / ".gitignore").read_text()
    assert ignore.count(fa._BEGIN) == 1
    assert "/stage-shots-a.wav" in ignore and "/stage-shots-b.wav" in ignore


def test_status_and_fetch_restore_a_missing_or_changed_file(fa, tmp_path, monkeypatch):
    (tmp_path / "x.wav").write_bytes(b"good")
    fa.save_manifest({"x.wav": {"sha256": fa.sha256(tmp_path / "x.wav"), "bytes": 4}})
    assert fa.status()["ok"] == ["x.wav"]
    (tmp_path / "x.wav").write_bytes(b"evil")
    assert fa.status()["changed"] == ["x.wav"]

    served = {}

    def fake_download(base_url, name, entry):
        served[name] = base_url
        (tmp_path / name).write_bytes(b"good")

    monkeypatch.setattr(fa, "_download", fake_download)
    assert fa.fetch("https://example.invalid") == []
    assert served == {"x.wav": "https://example.invalid"}
    assert fa.status()["ok"] == ["x.wav"]


def test_a_download_with_the_wrong_hash_is_refused(fa, tmp_path, monkeypatch):
    import io

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fa.urllib.request, "urlopen", lambda *_a, **_k: Resp(b"tampered"))
    err = fa._download("https://x", "x.wav", {"sha256": "0" * 64, "bytes": 8})
    assert "hash mismatch" in err
    assert not (tmp_path / "x.wav").exists()
    assert not list(tmp_path.glob("*.part"))
