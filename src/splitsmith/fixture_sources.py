"""Resolve a fixture's ``source_video``, loudly.

Five scripts (``build_ensemble_artifacts``, ``regression_voter_e``,
``build_sweep_signals``, ``probe_visual_voter``,
``sweep_multiframe_voter_e``) read ``source_video`` off a fixture's audit
JSON to pull frames out of the original recording. Each used to skip a
fixture whose video could not be reached, which silently shrinks the
corpus a model is built from -- a build over half a corpus looks exactly
like a build over all of it. This module is the single place that
decision lives, and its default is to fail.
"""

from __future__ import annotations

from pathlib import Path


class MissingSourceVideoError(RuntimeError):
    """A fixture's ``source_video`` is absent from the JSON or unreachable on disk."""


def resolve_source_video(
    truth: dict,
    fixture: str,
    *,
    allow_missing: bool = False,
) -> Path | None:
    """Return the reachable ``source_video`` Path for ``fixture``.

    Raises :class:`MissingSourceVideoError` when the fixture names no
    video or names one that is not on disk. ``allow_missing=True``
    downgrades both cases to ``None`` for callers that have explicitly
    opted into a partial corpus.
    """
    raw = truth.get("source_video") or ""
    if not raw:
        if allow_missing:
            return None
        raise MissingSourceVideoError(
            f"{fixture}: fixture JSON has no source_video. "
            f"Pass --allow-missing-video to proceed without it."
        )

    path = Path(raw)
    if not path.exists():
        if allow_missing:
            return None
        raise MissingSourceVideoError(
            f"{fixture}: source_video {path} is unreachable. "
            f"Mount the volume holding it (the corpus lives under /Volumes/X9), "
            f"or pass --allow-missing-video to proceed without it."
        )
    return path
