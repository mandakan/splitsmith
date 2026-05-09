"""Manifest schema + loader tests for the compare module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from splitsmith.compare.manifest import CompareManifest, load_manifest


def _write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_round_trip_minimal(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "Mathias",
            "shooters": [
                {"project": "./mathias", "label": "Mathias"},
                {"project": "./anders", "label": "Anders"},
            ],
        },
    )
    m = load_manifest(src)
    assert m.audio_from == "Mathias"
    assert m.layout_2up == "horizontal"  # default
    assert m.output == (tmp_path / "out.fcpxml").resolve()
    # relative shooter paths resolve against the manifest dir
    assert m.shooters[0].project == (tmp_path / "mathias").resolve()
    assert m.shooters[1].project == (tmp_path / "anders").resolve()


def test_layout_2up_vertical(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "A",
            "layout_2up": "vertical",
            "shooters": [{"project": "/p/a", "label": "A"}],
        },
    )
    m = load_manifest(src)
    assert m.layout_2up == "vertical"


def test_layout_2up_rejects_unknown(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "A",
            "layout_2up": "diagonal",
            "shooters": [{"project": "/p/a", "label": "A"}],
        },
    )
    with pytest.raises(ValidationError):
        load_manifest(src)


def test_audio_from_must_match_a_label(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "Nobody",
            "shooters": [{"project": "/p/a", "label": "A"}],
        },
    )
    with pytest.raises(ValidationError) as exc:
        load_manifest(src)
    assert "audio_from" in str(exc.value)


def test_duplicate_labels_rejected(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "A",
            "shooters": [
                {"project": "/p/1", "label": "A"},
                {"project": "/p/2", "label": "A"},
            ],
        },
    )
    with pytest.raises(ValidationError) as exc:
        load_manifest(src)
    assert "duplicate" in str(exc.value)


def test_empty_shooters_rejected(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "A",
            "shooters": [],
        },
    )
    with pytest.raises(ValidationError):
        load_manifest(src)


def test_tilde_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "out.fcpxml",
            "audio_from": "A",
            "shooters": [{"project": "~/projects/anders", "label": "A"}],
        },
    )
    m = load_manifest(src)
    assert m.shooters[0].project == fake_home / "projects" / "anders"


def test_absolute_paths_passthrough(tmp_path: Path) -> None:
    src = _write(
        tmp_path / "m.yaml",
        {
            "output": "/tmp/out.fcpxml",
            "audio_from": "A",
            "shooters": [{"project": "/abs/path", "label": "A"}],
        },
    )
    m = load_manifest(src)
    assert m.output == Path("/tmp/out.fcpxml")
    assert m.shooters[0].project == Path("/abs/path")


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    src = tmp_path / "m.yaml"
    src.write_text("- not_a_mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_manifest(src)


def test_validate_directly_skips_path_resolution() -> None:
    """``CompareManifest.model_validate`` works without going through
    :func:`load_manifest`; relative paths simply stay relative."""
    m = CompareManifest.model_validate(
        {
            "output": "out.fcpxml",
            "audio_from": "A",
            "shooters": [{"project": "rel/path", "label": "A"}],
        }
    )
    assert m.shooters[0].project == Path("rel/path")
