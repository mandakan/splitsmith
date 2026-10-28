"""Reviewing a fixture's speech masks (#1778): load and save.

The lab's fixture page shows what :mod:`speech_mask` proposes beside what
the fixture already stores, and a person decides. Saving writes the
accepted ``masked_regions`` and the ``speech_review`` stamp
(:mod:`fixture_schema`) into the fixture JSON and nothing else.

The server refuses a region that touches a protected window (a labelled
shot or the beep), whatever the page sent: masked audio over a shot would
teach the model that silence means no shot.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import fs_atomic
from ..config import SpeechMaskConfig
from ..fixture_schema import SPEECH_EXCLUDED, SPEECH_MASKED, MaskedRegion, SpeechReview, speech_review
from . import speech_mask

_SLUG = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class SpeechReviewError(ValueError):
    """A save the reviewer must fix (mapped to a 422)."""


class SpeechReviewState(BaseModel):
    slug: str
    duration: float
    proposal: speech_mask.MaskProposal | None
    #: What the fixture stores now (accepted earlier), else empty.
    masked_regions: list[MaskedRegion]
    review: SpeechReview | None
    protected: list[speech_mask.MaskRegion]
    masker_version: int = speech_mask.MASKER_VERSION
    method: str = speech_mask.METHOD


class RegionIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class SpeechReviewIn(BaseModel):
    decision: Literal["masked", "no_speech", "excluded"]
    regions: list[RegionIn] = Field(default_factory=list)


def config_from_env() -> SpeechMaskConfig:
    """From ``SPLITSMITH_CONFIG`` when set, like the coach thresholds."""
    from ..config import Config
    from ..runtime import ENV_CONFIG_FILE

    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    return Config.load(Path(raw).expanduser()).speech_mask if raw else SpeechMaskConfig()


def fixture_path(root: Path, slug: str) -> Path:
    """The fixture JSON for ``slug``, confined to ``root``."""
    if not _SLUG.match(slug):
        raise FileNotFoundError(slug)
    path = root / f"{slug}.json"
    if not path.is_file():
        raise FileNotFoundError(slug)
    return path


def load_state(
    path: Path, *, session: Any | None, config: SpeechMaskConfig | None = None
) -> SpeechReviewState:
    """The page's data. ``session`` is the VAD; ``None`` (or no WAV) leaves
    ``proposal`` empty and the stored regions still show."""
    from ..beep.detect import load_audio

    cfg = config or SpeechMaskConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    wav = path.with_suffix(".wav")
    proposal = None
    duration = 0.0
    if wav.exists():
        audio, sr = load_audio(wav)
        duration = round(len(audio) / sr, 3)
        if session is not None:
            proposal = speech_mask.propose_for_fixture(data, audio, sr, session, cfg)
    stored = []
    for entry in data.get("masked_regions") or []:
        try:
            stored.append(MaskedRegion.model_validate(entry))
        except ValueError:
            continue
    return SpeechReviewState(
        slug=path.stem,
        duration=duration,
        proposal=proposal,
        masked_regions=stored,
        review=speech_review(data),
        protected=speech_mask.protected_windows(speech_mask.labelled_times(data), cfg),
    )


def _checked_regions(
    regions: list[RegionIn], protected: list[speech_mask.MaskRegion], duration: float | None
) -> list[tuple[float, float]]:
    spans = sorted((round(r.start, 3), round(r.end, 3)) for r in regions)
    for start, end in spans:
        if end <= start:
            raise SpeechReviewError(f"region {start:.3f}-{end:.3f} s has no length")
        if duration is not None and end > duration + 1e-6:
            raise SpeechReviewError(f"region {start:.3f}-{end:.3f} s runs past the clip ({duration:.3f} s)")
        for guard in protected:
            if start < guard.end and end > guard.start:
                raise SpeechReviewError(
                    f"region {start:.3f}-{end:.3f} s covers a shot or the beep "
                    f"({guard.start:.3f}-{guard.end:.3f} s); trim it or exclude the stage"
                )
    for (_, prev_end), (start, _) in zip(spans, spans[1:], strict=False):
        if start < prev_end:
            raise SpeechReviewError(f"regions overlap at {start:.3f} s")
    return spans


def save_review(
    path: Path,
    body: SpeechReviewIn,
    *,
    config: SpeechMaskConfig | None = None,
    duration: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write the decision into the fixture and return the fixture's data.

    ``masked`` needs at least one region; ``no_speech`` stores none;
    ``excluded`` keeps any regions given (the stage still trains, its masks
    still apply), and is never published.
    """
    cfg = config or SpeechMaskConfig()
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    protected = speech_mask.protected_windows(speech_mask.labelled_times(data), cfg)
    spans = _checked_regions(body.regions, protected, duration)
    if body.decision == SPEECH_MASKED and not spans:
        raise SpeechReviewError("accepting masks needs at least one region; choose no speech instead")
    if body.decision not in (SPEECH_MASKED, SPEECH_EXCLUDED):
        spans = []
    data["masked_regions"] = [
        MaskedRegion(
            start=s, end=e, method=speech_mask.METHOD, masker_version=speech_mask.MASKER_VERSION
        ).model_dump()
        for s, e in spans
    ]
    data["speech_review"] = SpeechReview(
        decision=body.decision,
        reviewed_at=(now or datetime.now(UTC)).isoformat(),
        masker_version=speech_mask.MASKER_VERSION,
    ).model_dump()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=raw.isascii()) + "\n", encoding="utf-8")
    fs_atomic.replace(tmp, path)
    return data
