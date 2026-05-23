"""Pydantic models for the slim ``model_artifacts`` block (doc 03).

The bundled ``src/splitsmith/data/ensemble_calibration.json`` grows an
optional ``model_artifacts`` block written by
``scripts/build_ensemble_artifacts.py --onnx``. Each entry pins the
exact bytes the slim runtime should fetch + verify before handing the
file to ``onnxruntime.InferenceSession``.

The optional ``base_url`` field on the parent block lets self-hosting
users (or air-gapped CI) point the runtime at a local mirror without
touching the per-artifact ``url`` entries. The runtime composes
``{base_url}artifacts/{sha256}/{filename}`` when ``base_url`` is set
and falls back to the per-artifact URL only if the composed one 404s.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ArtifactSpec(BaseModel):
    """One artifact entry in the calibration's ``model_artifacts``."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    sha256: _Sha256
    size_bytes: int = Field(ge=0)
    url: str = Field(min_length=1)


class ModelArtifactsSpec(BaseModel):
    """The full ``model_artifacts`` block; entries keyed by slug.

    The slugs are the model identities the slim runtime asks for
    (``clap_audio_encoder``, ``clap_text_embeddings``, ``pann_cnn14``,
    ``clip_visual_encoder``). Extra keys are preserved so future
    artifacts (e.g. a fallback GBDT-only path) round-trip cleanly.
    """

    model_config = ConfigDict(extra="allow")

    base_url: str | None = None
    # Pydantic v2 doesn't surface the extras map as a typed dict, so we
    # rely on ``model_dump`` for slug iteration. Helpers below normalise
    # the access pattern.

    def artifact(self, slug: str) -> ArtifactSpec | None:
        """Return the artifact for ``slug`` or ``None`` if absent."""
        raw = self.model_extra.get(slug) if self.model_extra else None
        if raw is None:
            return None
        return ArtifactSpec.model_validate(raw)

    def slugs(self) -> list[str]:
        """All artifact slugs in declaration order."""
        return list(self.model_extra.keys()) if self.model_extra else []

    def composed_url(self, spec: ArtifactSpec) -> str:
        """URL the runtime should hit first; honours ``base_url``."""
        if not self.base_url:
            return spec.url
        base = self.base_url.rstrip("/") + "/"
        return f"{base}artifacts/{spec.sha256}/{spec.filename}"
