"""The Broadcast Look's sounds: the reference for a Look that carries sound.
Its fixed beds are in ``look.json``; what follows a template's data is in
that template's ``window.sounds()``. Every file ships with its licence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from splitsmith import audio_dress, look_tools, looks
from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

_ROOT = looks.shipped_looks_dir() / "broadcast"
_SOUNDS = _ROOT / "sounds"
_LICENCES = _SOUNDS / "LICENSES.md"


def _shipped_sounds() -> set[str]:
    return {p.name for p in _SOUNDS.iterdir() if p.is_file() and p.suffix == ".m4a"}


def _named_by_templates() -> set[str]:
    names: set[str] = set()
    for template in _ROOT.glob("*.html"):
        names |= set(re.findall(r"(?:play|bed): '([^']+)'", template.read_text(encoding="utf-8")))
    return names


def test_every_shipped_sound_is_named_licensed_cc0_and_played() -> None:
    """A file in the Look's sounds/ folder ships in the wheel to every
    user, so each must have its source on record, be CC0 (the Look names no
    credits, so nothing CC-BY may ship), and be played by some cue; every
    cue names a file that is there."""
    shipped = _shipped_sounds()
    text = _LICENCES.read_text(encoding="utf-8")
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| `([^`]+)` \|.*$", text, re.MULTILINE)}
    assert set(rows) == shipped
    sources = [line for line in text.splitlines() if line.startswith("| ") and "freesound.org" in line]
    assert len(sources) == len(shipped)
    assert all("| CC0 1.0 |" in line for line in sources)
    assert "credits" not in json.loads((_ROOT / "look.json").read_text(encoding="utf-8"))

    in_map = {cue.play or cue.bed for cue in audio_dress.load_sound_map(_ROOT).cues}
    played = in_map | _named_by_templates()
    assert played == shipped


def _cues(template: Path, context: Any) -> tuple[dict[str, Any], ...]:
    try:
        with ChromiumRasterizer() as rasterizer:
            frames = rasterizer.render_template_frames(
                template, context=context, width=320, height=180, fps=10, max_seconds=5.0
            )
            frames.close()
            return frames.sounds
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


def _with_data(context: Any, **changes: Any) -> Any:
    return context.model_copy(update={"data": {**context.data, **changes}})


@pytest.mark.integration
@pytest.mark.parametrize(
    ("slot", "file"),
    [
        ("title_page", "whoosh.m4a"),
        ("slate", "whoosh.m4a"),
        ("closing", "whoosh.m4a"),
        ("lower_third", "swoosh-soft.m4a"),
    ],
)
def test_the_card_plays_the_sound_its_slot_calls_for(tmp_path: Path, slot: str, file: str) -> None:
    """One template, four cards: the lower third plays over a stage's
    opening seconds, so it takes the quieter swoosh."""
    look = looks.load_look("broadcast")
    (_case, context), *_ = look_tools.sample_contexts(look, slot, "default", tmp_path)
    (cue,) = _cues(_ROOT / "broadcast-card.html", context)
    assert cue["play"] == file
    assert cue["at"] == cue["hit"]  # the file's loudest moment on its own offset: it starts on the cut


@pytest.mark.integration
@pytest.mark.parametrize("length", [1.0, 2.5])
def test_the_sting_times_its_sounds_to_the_transitions_length(tmp_path: Path, length: float) -> None:
    look = looks.load_look("broadcast")
    (_case, context), *_ = look_tools.sample_contexts(look, "transition", "push", tmp_path)
    context = _with_data(context, transition={**context.data["transition"], "duration_seconds": length})
    riser, out = _cues(_ROOT / "broadcast-sting.html", context)
    assert (riser["play"], riser["at"]) == ("sting.m4a", pytest.approx(0.4 * length))
    assert (out["play"], out["at"]) == ("swoosh-soft.m4a", pytest.approx(0.8 * length))


def _summary_context(**figures: Any) -> Any:
    look = looks.load_look("broadcast")
    template = _ROOT / "broadcast-summary.html"
    (_case, context, _w, _h), *_ = look_tools.summary_samples(look, "summary", "results", template)
    return _with_data(context, figures={**context.data["figures"], **figures})


@pytest.mark.integration
def test_the_summary_ticks_while_it_counts_and_lands_on_the_impact() -> None:
    tick, impact = _cues(_ROOT / "broadcast-summary.html", _summary_context(dq=False))
    assert (tick["play"], tick["every"], tick["for"]) == ("tick.m4a", 0.08, 1.6)
    assert (impact["play"], impact["at"]) == ("impact.m4a", 1.6)


@pytest.mark.integration
def test_a_dq_lands_without_the_impact() -> None:
    (tick,) = _cues(_ROOT / "broadcast-summary.html", _summary_context(dq=True))
    assert tick["play"] == "tick.m4a"
