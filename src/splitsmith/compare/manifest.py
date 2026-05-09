"""Pydantic schema + YAML loader for compare-export manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class CompareShooter(BaseModel):
    """One shooter contributing tiles to the comparison."""

    project: Path
    label: str = Field(min_length=1)

    @field_validator("project", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        if isinstance(value, Path):
            return value.expanduser()
        return value


class CompareManifest(BaseModel):
    """A compare-export manifest loaded from YAML.

    Path resolution: ``output`` and shooter ``project`` paths in the
    YAML are taken verbatim. ``~`` is expanded; relative paths are NOT
    rewritten here -- :func:`load_manifest` does the manifest-dir
    resolution because it's the only caller that knows where the
    manifest lives on disk.
    """

    output: Path
    audio_from: str = Field(min_length=1)
    layout_2up: Literal["horizontal", "vertical"] = "horizontal"
    shooters: list[CompareShooter] = Field(min_length=1)

    @field_validator("output", mode="before")
    @classmethod
    def _expand_output(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        if isinstance(value, Path):
            return value.expanduser()
        return value

    @model_validator(mode="after")
    def _labels_unique(self) -> CompareManifest:
        labels = [s.label for s in self.shooters]
        if len(set(labels)) != len(labels):
            dupes = sorted({lab for lab in labels if labels.count(lab) > 1})
            raise ValueError(f"duplicate shooter labels: {dupes}")
        return self

    @model_validator(mode="after")
    def _audio_from_matches(self) -> CompareManifest:
        labels = {s.label for s in self.shooters}
        if self.audio_from not in labels:
            raise ValueError(
                f"audio_from={self.audio_from!r} does not match any shooter label "
                f"({sorted(labels)})"
            )
        return self


def load_manifest(path: Path) -> CompareManifest:
    """Read ``path`` and return a validated :class:`CompareManifest`.

    Resolves the manifest's ``output`` path against the manifest's
    parent directory when it's relative, and rewrites each shooter's
    ``project`` path against the same base when relative -- so a
    manifest is portable as long as the YAML and the project roots
    move together.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"manifest {path} must be a YAML mapping at the top level")
    manifest = CompareManifest.model_validate(raw)
    base = path.parent
    if not manifest.output.is_absolute():
        manifest = manifest.model_copy(update={"output": (base / manifest.output).resolve()})

    def _resolve(p: Path) -> Path:
        return p if p.is_absolute() else (base / p).resolve()

    resolved_shooters = [
        s.model_copy(update={"project": _resolve(s.project)}) for s in manifest.shooters
    ]
    return manifest.model_copy(update={"shooters": resolved_shooters})
