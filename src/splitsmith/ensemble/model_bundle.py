"""A built detection model as a self-describing, versioned bundle.

``scripts/build_ensemble_artifacts.py --out DIR`` writes a model into its own
folder rather than over the shipped one: the calibration JSON, the voter C
ONNX graphs, the held-out report, and ``model.json`` -- this module's
:class:`ModelManifest`. The manifest says what the model is (name, version,
channel), every file it is made of (sha256 and size, so a copy can be
verified wherever it lands), and what it was built from: the selection that
picked the corpus and each fixture with the sha256 of the WAV it read, which
is the same digest ``tests/fixtures/audio.lock.json`` addresses in R2.

The lab lists bundles under :data:`MODELS_DIR` beside the shipped model and
compares them. Choosing a model at runtime, or serving pre-release and
premium models, would read the same manifest; nothing does yet.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MANIFEST_NAME = "model.json"
CALIBRATION_NAME = "ensemble_calibration.json"
MODELS_DIR: Path = Path(__file__).resolve().parents[3] / "build" / "models"

Channel = Literal["shipped", "experimental", "pre-release", "premium"]


class BundleFile(BaseModel):
    name: str
    sha256: str
    bytes: int


class CorpusFixture(BaseModel):
    stem: str
    wav_sha256: str


class CorpusSelection(BaseModel):
    """How the corpus was picked, in the build's own terms."""

    matches: list[str] | None = None
    fixtures: list[str] | None = None
    reviewed_only: bool = False


class ModelManifest(BaseModel):
    schema_version: int = 1
    name: str
    version: str
    channel: Channel = "experimental"
    built_at: str
    git_sha: str | None = None
    description: str | None = None
    files: list[BundleFile] = Field(default_factory=list)
    selection: CorpusSelection = Field(default_factory=CorpusSelection)
    corpus: list[CorpusFixture] = Field(default_factory=list)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_files(folder: Path) -> list[BundleFile]:
    """Every file of a bundle but its manifest, sorted by name."""
    return [
        BundleFile(name=p.relative_to(folder).as_posix(), sha256=sha256_file(p), bytes=p.stat().st_size)
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != MANIFEST_NAME
    ]


def write_manifest(folder: Path, manifest: ModelManifest) -> Path:
    """Write ``model.json`` with the bundle's file list taken from disk."""
    manifest = manifest.model_copy(update={"files": bundle_files(folder)})
    path = folder / MANIFEST_NAME
    path.write_text(manifest.model_dump_json(indent=2) + "\n")
    return path


def read_manifest(folder: Path) -> ModelManifest | None:
    try:
        return ModelManifest.model_validate_json((folder / MANIFEST_NAME).read_text())
    except (OSError, ValueError):
        return None


def verify(folder: Path, manifest: ModelManifest) -> list[str]:
    """What does not match the manifest: missing or changed files."""
    problems = []
    for f in manifest.files:
        p = folder / f.name
        if not p.is_file():
            problems.append(f"{f.name}: missing")
        elif p.stat().st_size != f.bytes or sha256_file(p) != f.sha256:
            problems.append(f"{f.name}: changed")
    return problems


def list_bundles(root: Path | None = None) -> list[tuple[Path, ModelManifest]]:
    """Every readable bundle under ``root`` (:data:`MODELS_DIR`), newest first."""
    root = MODELS_DIR if root is None else root
    if not root.is_dir():
        return []
    found = [(d, m) for d in root.iterdir() if d.is_dir() and (m := read_manifest(d)) is not None]
    return sorted(found, key=lambda dm: dm[1].built_at, reverse=True)


def calibration_of(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / CALIBRATION_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
