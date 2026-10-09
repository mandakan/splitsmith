"""The template HUD path of render_overlay: frames, cache, fallback."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import overlay_render
from splitsmith.config import VideoMetadata
from splitsmith.overlay_hud import HudOptions
from splitsmith.overlay_raster import TemplateFrames, TemplateScriptError
from splitsmith.segment_cache import SegmentCache
from tests.conftest import fake_ffmpeg_probe

W, H = 64, 36


def _meta(duration: float = 2.0) -> VideoMetadata:
    return VideoMetadata(width=W, height=H, duration_seconds=duration, frame_rate_num=10, frame_rate_den=1)


def _audit(tmp_path: Path, events: Any = None) -> Path:
    audit = tmp_path / "stage1.json"
    doc: dict[str, Any] = {
        "shots": [
            {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
            for i, ms in enumerate([300, 550, 800])
        ]
    }
    if events is not None:
        doc["events"] = events
    audit.write_text(json.dumps(doc), encoding="utf-8")
    return audit


class _FakeRasterizer:
    """Classic's ``png`` plus the HUD's ``render_template_timeline``: real
    RGBA buffers, every call recorded. ``fail_at`` raises a
    TemplateScriptError on that frame index, mid-run."""

    def __init__(self, *, settle: float = 0.2, fail_at: int | None = None) -> None:
        self.settle = settle
        self.fail_at = fail_at
        self.timelines: list[dict[str, Any]] = []
        self.documents: list[str] = []
        self.rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.documents.append(html)
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()

    def engine_version(self) -> str:
        return "fake-1"

    def render_template_timeline(self, template, *, context, width, height, plan):  # noqa: ANN001
        times = list(plan(self.settle))
        self.timelines.append(
            {"template": template, "context": context, "times": times, "size": (width, height)}
        )

        def frames():
            for index, _ in enumerate(times):
                if self.fail_at is not None and index == self.fail_at:
                    raise TemplateScriptError("hud-plate.html: boom")
                self.rendered += 1
                yield bytes((index % 256, 0, 0, 255)) * (width * height)

        return TemplateFrames(
            duration=self.settle, frame_count=len(times), width=width, height=height, frames=frames()
        )


class _Encoder:
    """A stub ffmpeg: records argv and every byte piped, writes the output."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.piped: list[int] = []
        self.kills = 0
        encoder = self

        class Stdin:
            def write(self, data: bytes) -> int:
                encoder.piped[-1] += len(data)
                return len(data)

            def close(self) -> None:
                return None

        class Stderr:
            def read(self) -> bytes:
                return b""

        class Proc:
            def __init__(self, cmd: list[str]) -> None:
                self.cmd = cmd
                self.stdin = Stdin()
                self.stderr = Stderr()

            def wait(self) -> int:
                Path(self.cmd[-1]).write_bytes(f"mov:{len(encoder.calls)}".encode())
                return 0

            def kill(self) -> None:
                encoder.kills += 1

        def popen(cmd: list[str], **_: Any) -> Proc:
            encoder.calls.append(cmd)
            encoder.piped.append(0)
            return Proc(cmd)

        monkeypatch.setattr(overlay_render.subprocess, "Popen", popen)
        # A real file: the segment cache keys the encoder by stat-ing what
        # ``which`` answers, and ``shutil`` is one module for both callers.
        monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: sys.executable)


def _render(tmp_path: Path, rasterizer: _FakeRasterizer, *, events: Any = None, **kwargs: Any) -> Path:
    kwargs.setdefault("probe", _meta())
    kwargs.setdefault("beep_offset_seconds", 1.0)
    return overlay_render.render_overlay(
        audit_path=_audit(tmp_path, events),
        trimmed_video_path=tmp_path / "trim.mp4",
        output_path=tmp_path / "overlay.mov",
        codec="prores-4444",
        rasterizer=rasterizer,
        probe_runner=fake_ffmpeg_probe(),
        **kwargs,
    )


def test_a_template_variant_renders_the_planned_frames_and_pipes_every_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer(settle=0.2)
    _render(tmp_path, fake, variant="plate")
    (timeline,) = fake.timelines
    assert timeline["template"].name == "hud-plate.html"
    # 10 fps, beep at 1.0, last shot at 1.8, settle 0.2: one held frame at 0,
    # then frames 1.0 .. 2.0 clamped to the clip's last frame (1.9).
    assert timeline["times"] == [0.0, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9]
    assert fake.documents == [], "the Classic path drew nothing"
    assert encoder.piped == [20 * W * H * 4]
    assert "drawtext" not in " ".join(encoder.calls[0]), "the template draws the clock"
    data = timeline["context"].data
    assert data["stage"]["rounds"] == 3 and data["stage"]["beep"] == 1.0
    assert data["options"]["position"] == "bottom-left"


def test_the_options_and_position_reach_the_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    _render(
        tmp_path,
        fake,
        variant="plate",
        hud_options=HudOptions(speed_colors=False, class_labels=False, landing=False, position="top-right"),
    )
    assert fake.timelines[0]["context"].data["options"] == {
        "speed_colors": False,
        "class_labels": False,
        "landing": False,
        "reload_chip": False,
        "stage_bar": False,
        "position": "top-right",
    }


_MOVEMENT = {"id": "evt-1", "kind": "movement", "start": 0.2, "end": 0.6, "source": "manual"}
_PROPOSAL = {"id": "evt-2", "kind": "reload", "start": 0.6, "end": 0.75, "source": "auto"}


def test_confirmed_regions_from_the_audit_reach_the_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    _render(tmp_path, fake, events=[_MOVEMENT, _PROPOSAL], variant="plate")
    stage = fake.timelines[0]["context"].data["stage"]
    assert stage["events"] == [{"kind": "movement", "start": 1.2, "end": 1.6}]
    assert stage["reloads"] == [], "an auto proposal never renders"
    assert [s["moving"] for s in stage["shots"]] == [True, True, False]


def test_a_corrupt_events_list_renders_without_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    inverted = {"id": "evt-1", "kind": "reload", "start": 0.6, "end": 0.2, "source": "manual"}
    _render(tmp_path, fake, events=[inverted], variant="plate")
    (timeline,) = fake.timelines
    stage = timeline["context"].data["stage"]
    assert stage["events"] == [] and stage["reloads"] == [] and stage["rounds"] == 3


def test_confirming_a_region_misses_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The HUD key hashes the whole context, so a confirmed region reaches
    it with no key change of its own; a proposal does not move it."""
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), events=[_PROPOSAL], variant="plate", segment_cache=cache)
    proposal_only = _FakeRasterizer()
    _render(tmp_path, proposal_only, variant="plate", segment_cache=cache)
    assert proposal_only.rendered == 0, "a proposal is invisible, so the key is unchanged"
    confirmed = _FakeRasterizer()
    _render(tmp_path, confirmed, events=[_MOVEMENT], variant="plate", segment_cache=cache)
    assert confirmed.rendered > 0 and len(encoder.calls) == 2


def test_a_tall_output_renders_a_1080_page_and_scales_in_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    tall = VideoMetadata(width=3840, height=2160, duration_seconds=0.5, frame_rate_num=10, frame_rate_den=1)
    _render(tmp_path, fake, variant="plate", probe=tall, beep_offset_seconds=0.0)
    assert fake.timelines[0]["size"] == (1920, 1080)
    cmd = encoder.calls[0]
    assert cmd[cmd.index("-s") + 1] == "1920x1080"
    assert cmd[cmd.index("-vf") + 1] == "scale=3840:2160:flags=lanczos"


def test_no_variant_is_the_classic_path_and_loads_no_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)

    def no_template(*_a: Any, **_k: Any) -> None:
        raise AssertionError("Classic must not resolve a template")

    monkeypatch.setattr(overlay_render, "overlay_template_for", no_template)
    fake = _FakeRasterizer()
    for variant in (None, "default"):
        _render(tmp_path, fake, variant=variant)
    assert fake.timelines == [] and fake.documents


def test_a_template_that_throws_mid_run_falls_back_to_classic_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer(fail_at=4)
    degraded: list[str] = []
    out = _render(tmp_path, fake, variant="plate", degraded=degraded)
    assert len(encoder.calls) == 2, "the HUD encode was killed, then Classic encoded"
    assert fake.documents, "Classic drew its runs"
    assert out.read_bytes() == b"mov:2"
    assert degraded == ["overlay style 'plate' fell back to Classic: hud-plate.html: boom"]


def test_an_unknown_variant_draws_classic_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    degraded: list[str] = []
    _render(tmp_path, fake, variant="nope", degraded=degraded)
    assert fake.timelines == [] and fake.documents
    assert degraded == ["overlay style 'nope' is not in Look 'splitsmith'; drew Classic"]


def test_a_cache_hit_renders_no_frame_and_leaves_the_mov_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    first = _FakeRasterizer()
    out = _render(tmp_path, first, variant="plate", segment_cache=cache)
    before = (out.read_bytes(), out.stat().st_mtime_ns)
    os.utime(out, ns=(before[1] - 10**9, before[1] - 10**9))
    pinned = out.stat().st_mtime_ns

    second = _FakeRasterizer()
    _render(tmp_path, second, variant="plate", segment_cache=cache)
    assert second.rendered == 0, "a hit renders no browser frame"
    assert len(encoder.calls) == 1, "and encodes nothing"
    assert out.read_bytes() == before[0] and out.stat().st_mtime_ns == pinned


@pytest.mark.parametrize(
    "change",
    [
        {"hud_options": HudOptions(speed_colors=False)},
        {"hud_options": HudOptions(position="top-left")},
        {"beep_offset_seconds": 0.9},
        {"theme": "clean"},
    ],
)
def test_any_input_change_misses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict
) -> None:
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), variant="plate", segment_cache=cache)
    again = _FakeRasterizer()
    _render(tmp_path, again, variant="plate", segment_cache=cache, **change)
    assert again.rendered > 0 and len(encoder.calls) == 2


def test_changed_template_bytes_miss_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), variant="plate", segment_cache=cache)
    edited = tmp_path / "hud-plate.html"
    shipped = overlay_render.overlay_template_for(overlay_render.load_look("splitsmith"), "plate")
    edited.write_text(shipped.read_text(encoding="utf-8") + "<!-- edit -->", encoding="utf-8")
    monkeypatch.setattr(overlay_render, "overlay_template_for", lambda _look, _v: edited)
    again = _FakeRasterizer()
    _render(tmp_path, again, variant="plate", segment_cache=cache)
    assert again.rendered > 0


# --- the shipped Plate, through real Chromium and ffmpeg ------------------------

import subprocess as _subprocess  # noqa: E402

from splitsmith.overlay_raster import ChromiumRasterizer  # noqa: E402
from tests.synthetic_media import ffmpeg_available  # noqa: E402


def _frame_at(mov: Path, index: int, out: Path) -> Image.Image:
    _subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(mov),
            "-vf",
            f"select=eq(n\\,{index})",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgba",
            str(out),
        ],
        check=True,
    )
    return Image.open(out).convert("RGBA")


def _alpha_in(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    return max(image.crop(box).getchannel("A").getdata())


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
def test_plate_renders_a_stage_into_a_mov_the_trim_s_length(tmp_path: Path) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=3.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant="plate",
        )
    frames = _subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(frames) == 90
    # Bottom-left plate: drawn before the beep (the counter reads 00/03) and after a shot.
    bottom_left = (0, 220, 320, 360)
    top_right = (400, 0, 640, 140)
    pre_beep = _frame_at(out, 10, tmp_path / "pre.png")
    after_shot = _frame_at(out, 50, tmp_path / "shot.png")
    assert _alpha_in(pre_beep, bottom_left) > 200
    assert _alpha_in(after_shot, bottom_left) > 200
    assert _alpha_in(after_shot, top_right) == 0, "nothing drawn outside the HUD's corner"


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
def test_plate_honours_the_position(tmp_path: Path) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=2.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant="plate",
            hud_options=HudOptions(position="top-right"),
        )
    frame = _frame_at(out, 50, tmp_path / "f.png")
    assert _alpha_in(frame, (400, 0, 640, 140)) > 200
    assert _alpha_in(frame, (0, 220, 320, 360)) == 0


def test_a_failed_cached_render_kills_the_encoder_and_leaves_no_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake encoder writes its output when reaped, as a real one flushes
    on exit: without the kill and the discard, a truncated MOV would sit in
    the cache root."""
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    degraded: list[str] = []
    _render(tmp_path, _FakeRasterizer(fail_at=4), variant="plate", segment_cache=cache, degraded=degraded)
    assert encoder.kills == 1
    assert degraded and "fell back to Classic" in degraded[0]
    assert sorted(p.name for p in cache.root.iterdir()) == []


# --- every shipped style, through real Chromium --------------------------------

SHIPPED_STYLES = ("plate", "pips", "ticker", "timeline", "minimal")


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
@pytest.mark.parametrize("variant", SHIPPED_STYLES)
def test_every_shipped_style_renders_without_falling_back(tmp_path: Path, variant: str) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=3.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    degraded: list[str] = []
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant=variant,
            degraded=degraded,
        )
    assert degraded == []
    after_shot = _frame_at(out, 50, tmp_path / "shot.png")
    assert after_shot.getchannel("A").getbbox() is not None, "the HUD drew something after a shot"


@pytest.mark.integration
@pytest.mark.parametrize("landing", [True, False])
@pytest.mark.parametrize("variant", SHIPPED_STYLES)
def test_every_shipped_style_is_still_outside_its_live_span(variant: str, landing: bool) -> None:
    """The frame plan holds one frame before the beep and one after
    ``last shot + settle()``; a template that moves there would freeze
    mid-motion. Two instants either side must draw the same pixels."""
    from splitsmith.looks import load_look, overlay_template_for
    from splitsmith.overlay_hud import hud_options_data, hud_stage_data
    from splitsmith.overlay_hud_render import hud_context
    from splitsmith.overlay_theme import theme_for
    from splitsmith.stage_summary_data import TileShot

    look = load_look("splitsmith")
    template = overlay_template_for(look, variant)
    assert template is not None
    shots = [
        TileShot(time_from_beep=1.1, split=1.1, interval_class="first_shot"),
        TileShot(time_from_beep=1.35, split=0.25, interval_class="split"),
        TileShot(time_from_beep=1.62, split=0.27, interval_class="split"),
        TileShot(time_from_beep=1.86, split=0.24, interval_class="split"),
    ]
    stage = hud_stage_data(shots, beep_in_clip=1.0)
    context = hud_context(
        stage=stage,
        options=hud_options_data(HudOptions(landing=landing), None),
        theme=theme_for(look),
        width=320,
        height=180,
        fps=30.0,
    )
    settled: list[float] = []

    def plan(settle: float) -> list[float]:
        settled.append(settle)
        end = stage["shots"][-1]["t"] + settle
        return [0.0, 0.99, end, end + 0.7]

    with ChromiumRasterizer() as rasterizer:
        rendered = rasterizer.render_template_timeline(
            template, context=context, width=320, height=180, plan=plan
        )
        try:
            before, at_beep, settled_frame, later = list(rendered.frames)
        finally:
            rendered.close()
    assert settled and settled[0] >= 0
    assert before == at_beep, f"{variant} moves before the beep"
    assert settled_frame == later, f"{variant} still moves after settle() (landing={landing})"


def test_the_hud_digest_moves_when_a_confirmed_region_is_added() -> None:
    from splitsmith.config import StageEvent
    from splitsmith.look_template import template_digest
    from splitsmith.overlay_hud import hud_options_data, hud_stage_data
    from splitsmith.overlay_hud_render import hud_context
    from splitsmith.overlay_theme import load_theme
    from splitsmith.stage_summary_data import TileShot

    shots = [TileShot(time_from_beep=t, split=t, interval_class=None) for t in (0.3, 0.55, 0.8)]
    template = overlay_render.overlay_template_for(overlay_render.load_look("splitsmith"), "plate")

    def digest(events: list[StageEvent]) -> str:
        ctx = hud_context(
            stage=hud_stage_data(shots, beep_in_clip=1.0, events=events),
            options=hud_options_data(HudOptions(), "bottom-left"),
            theme=load_theme("splitsmith"),
            width=W,
            height=H,
            fps=10.0,
        )
        return template_digest(template, ctx, fps=10.0, engine_version="x")

    region = StageEvent.model_validate(_MOVEMENT)
    assert digest([]) != digest([region])


# --- Timeline: the reload chip and the stage bar, through real Chromium ---------

#: Twelve shots (seconds from the beep): three standing, three on the move,
#: a reload, six standing. The beep sits at clip second 1.0.
_TL_SHOTS = (1.1, 1.32, 1.54, 1.9, 2.15, 2.4, 3.85, 4.07, 4.29, 4.52, 4.74, 4.96)
_TL_MOVEMENT = {"id": "evt-1", "kind": "movement", "start": 1.65, "end": 2.7, "source": "manual"}
_TL_RELOAD = {"id": "evt-2", "kind": "reload", "start": 2.55, "end": 3.7, "source": "manual"}
_TL_ACTIVATION = {"id": "evt-3", "kind": "activation", "start": 4.3, "end": 4.6, "source": "manual"}

_TIMELINE_DOM_JS = """() => {
  const box = (el) => { const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
  const chip = document.getElementById('chip');
  const tag = document.getElementById('tag');
  return {
    chip: chip ? {opacity: Number(getComputedStyle(chip).opacity), label: chip.firstChild.textContent,
                  num: chip.lastChild.textContent, border: getComputedStyle(chip).borderTopColor,
                  box: box(chip)} : null,
    clock: box(document.getElementById('clock')),
    tag: tag && Number(getComputedStyle(tag).opacity) > 0 ? box(tag) : null,
    track: box(document.getElementById('track')),
    bands: [...document.querySelectorAll('#track .band')].map((b) => ({
      shown: getComputedStyle(b).visibility === 'visible' && b.getBoundingClientRect().width > 0,
      colour: getComputedStyle(b).backgroundColor, box: box(b)})),
  };
}"""


def _timeline_view(rasterizer: Any, *, events: list[dict[str, Any]], **options: Any) -> Any:
    from splitsmith.config import StageEvent
    from splitsmith.looks import load_look, overlay_template_for
    from splitsmith.overlay_hud import hud_options_data, hud_stage_data
    from splitsmith.overlay_hud_render import hud_context
    from splitsmith.overlay_theme import theme_for
    from splitsmith.stage_summary_data import TileShot

    look = load_look("splitsmith")
    template = overlay_template_for(look, "timeline")
    assert template is not None
    shots, previous = [], 0.0
    for index, t in enumerate(_TL_SHOTS):
        shots.append(
            TileShot(time_from_beep=t, split=t - previous, interval_class="split" if index else "first_shot")
        )
        previous = t
    stage = hud_stage_data(shots, beep_in_clip=1.0, events=[StageEvent.model_validate(e) for e in events])
    context = hud_context(
        stage=stage,
        options=hud_options_data(HudOptions(**options), None),
        theme=theme_for(look),
        width=640,
        height=360,
        fps=30.0,
    )
    return rasterizer._open_template(template, context=context, width=640, height=360)


def _timeline_at(view: Any, t: float) -> dict[str, Any]:
    view.call("seek", t)
    state = view.page.evaluate(_TIMELINE_DOM_JS)
    assert view.errors == []
    return state


def _overlaps(a: dict[str, float], b: dict[str, float]) -> bool:
    return (
        a["left"] < b["right"]
        and b["left"] < a["right"]
        and a["top"] < b["bottom"]
        and b["top"] < a["bottom"]
    )


@pytest.mark.integration
def test_timeline_reload_chip_counts_the_reload_and_holds_its_duration_through_the_fade() -> None:
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD], reload_chip=True)
        try:
            before = _timeline_at(view, 1.0 + 2.5)
            mid = _timeline_at(view, 1.0 + 2.55 + 1.0)
            fading = _timeline_at(view, 1.0 + 3.7 + 0.2)
            gone = _timeline_at(view, 1.0 + 3.7 + 0.45)
        finally:
            view.close()
    assert before["chip"]["opacity"] == 0
    chip = mid["chip"]
    assert chip["opacity"] == 1 and chip["label"].lower() == "reload" and chip["num"] == "1.00"
    assert chip["border"] == "rgb(251, 191, 36)", "the reload colour, never the brand red"
    assert not _overlaps(chip["box"], mid["clock"])
    assert mid["tag"] is not None and not _overlaps(chip["box"], mid["tag"])
    assert 0.4 < fading["chip"]["opacity"] < 0.6 and fading["chip"]["num"] == "1.15"
    assert gone["chip"]["opacity"] == 0


@pytest.mark.integration
def test_timeline_reload_chip_clears_the_tag_on_the_first_shot() -> None:
    """The split tag sits furthest left on the first tick: a reload right
    after the draw puts the chip and that tag up together."""
    early = {"id": "evt-2", "kind": "reload", "start": 1.15, "end": 1.3, "source": "manual"}
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[early], reload_chip=True)
        try:
            state = _timeline_at(view, 1.0 + 1.25)
        finally:
            view.close()
    assert state["chip"]["opacity"] == 1 and state["tag"] is not None
    assert not _overlaps(state["chip"]["box"], state["tag"])
    assert not _overlaps(state["chip"]["box"], state["clock"])


@pytest.mark.integration
def test_timeline_draws_no_chip_and_no_band_with_the_toggles_off() -> None:
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD, _TL_ACTIVATION])
        try:
            state = _timeline_at(view, 1.0 + 3.55)
        finally:
            view.close()
    assert state["chip"] is None and state["bands"] == []


@pytest.mark.integration
def test_timeline_stage_bar_draws_one_band_per_confirmed_region_as_it_happens() -> None:
    proposal = {**_TL_ACTIVATION, "id": "evt-4", "source": "auto"}
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(
            rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD, _TL_ACTIVATION, proposal], stage_bar=True
        )
        try:
            mid_movement = _timeline_at(view, 1.0 + 2.25)
            landed = _timeline_at(view, 1.0 + 6.0)
        finally:
            view.close()
    assert len(landed["bands"]) == 3, "the proposal draws nothing"
    assert [b["shown"] for b in mid_movement["bands"]] == [
        True,
        False,
        False,
    ], "a region shows once it starts"
    assert all(b["shown"] for b in landed["bands"])
    assert [b["colour"] for b in landed["bands"]][:2] == ["rgb(6, 182, 212)", "rgb(251, 191, 36)"]
    assert landed["chip"] is None


@pytest.mark.integration
def test_timeline_clamps_a_reload_past_the_last_shot_and_settles_after_its_fade() -> None:
    """Review Focus 2: a reload ending after the last shot (and past the
    stage time) keeps its band on the track, and ``settle()`` covers the
    chip's count and fade so the held frame is the faded one."""
    late = {"id": "evt-2", "kind": "reload", "start": 4.8, "end": 6.5, "source": "manual"}
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[late], reload_chip=True, stage_bar=True)
        try:
            settle = view.call("hud")["settle"]
            landed = _timeline_at(view, 1.0 + 6.2)
            held = _timeline_at(view, 1.0 + 4.96 + settle)
        finally:
            view.close()
    assert settle >= 6.5 + 0.4 - 4.96 - 1e-6
    (band,) = landed["bands"]
    assert band["shown"] and band["box"]["right"] <= landed["track"]["right"] + 0.5
    assert landed["chip"]["opacity"] == 1 and landed["chip"]["num"] == "1.40"
    assert held["chip"]["opacity"] == 0
