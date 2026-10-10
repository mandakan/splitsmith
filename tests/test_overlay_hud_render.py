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
        {"hud_options": HudOptions(speed_colors=True)},
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
    fill: getComputedStyle(document.getElementById('fill')).backgroundColor,
    bands: [...document.querySelectorAll('#track .band')].map((b) => ({
      kind: b.dataset.kind,
      shown: getComputedStyle(b).visibility === 'visible' && b.getBoundingClientRect().width > 0,
      colour: getComputedStyle(b).backgroundColor, box: box(b)})),
  };
}"""


def _timeline_view(
    rasterizer: Any,
    *,
    events: list[dict[str, Any]],
    variant: str = "timeline",
    size: tuple[int, int] = (640, 360),
    shot_times: tuple[float, ...] = _TL_SHOTS,
    **options: Any,
) -> Any:
    from splitsmith.config import StageEvent
    from splitsmith.looks import load_look, overlay_template_for
    from splitsmith.overlay_hud import hud_options_data, hud_stage_data
    from splitsmith.overlay_hud_render import hud_context
    from splitsmith.overlay_theme import theme_for
    from splitsmith.stage_summary_data import TileShot

    look = load_look("splitsmith")
    template = overlay_template_for(look, variant)
    assert template is not None
    shots, previous = [], 0.0
    for index, t in enumerate(shot_times):
        shots.append(
            TileShot(time_from_beep=t, split=t - previous, interval_class="split" if index else "first_shot")
        )
        previous = t
    stage = hud_stage_data(shots, beep_in_clip=1.0, events=[StageEvent.model_validate(e) for e in events])
    context = hud_context(
        stage=stage,
        options=hud_options_data(HudOptions(**options), options.get("position")),
        theme=theme_for(look),
        width=size[0],
        height=size[1],
        fps=30.0,
    )
    return rasterizer._open_template(template, context=context, width=size[0], height=size[1])


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
@pytest.mark.parametrize("size", [(640, 360), (360, 640), (1080, 1920)])
def test_timeline_reload_chip_clears_the_tag_on_the_first_shot(size: tuple[int, int]) -> None:
    """The split tag sits furthest left on the first tick: a reload right
    after the draw puts the chip and that tag up together. Upright too,
    where the clock is wide enough to reach the track."""
    early = {"id": "evt-2", "kind": "reload", "start": 1.15, "end": 1.3, "source": "manual"}
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[early], size=size, reload_chip=True, stage_bar=True)
        try:
            state = _timeline_at(view, 1.0 + 1.25)
        finally:
            view.close()
    width, height = size
    chip, tag, clock, track = state["chip"]["box"], state["tag"], state["clock"], state["track"]
    assert state["chip"]["opacity"] == 1 and tag is not None
    assert _inside(chip, width, height) and _inside(tag, width, height)
    assert not _overlaps(chip, tag)
    assert not _overlaps(chip, clock)
    assert not _overlaps(tag, clock)
    # The track's ticks reach 1.6 vh under it: the clock sits clear of them.
    assert track["bottom"] + 0.016 * height <= clock["top"] or track["left"] >= clock["right"]


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
    kinds = [b["kind"] for b in landed["bands"]]
    # The reload is cut in two where it meets the movement; the proposal draws nothing.
    assert sorted(kinds) == ["activation", "movement", "reload", "reload"]
    shown = {b["kind"]: b["shown"] for b in mid_movement["bands"] if b["kind"] != "reload"}
    assert shown == {"movement": True, "activation": False}, "a region shows once it starts"
    assert not any(b["shown"] for b in mid_movement["bands"] if b["kind"] == "reload")
    assert all(b["shown"] for b in landed["bands"])
    colours = {b["kind"]: b["colour"] for b in landed["bands"]}
    assert colours["movement"] == "rgb(6, 182, 212)" and colours["reload"] == "rgb(251, 191, 36)"
    assert colours["activation"] != landed["fill"], "an activation band reads against the fill"
    assert landed["chip"] is None


@pytest.mark.integration
def test_plate_reload_chip_keeps_even_padding_with_the_stage_bar_on() -> None:
    """The stage bar pads the foot of the clock and count plates to make
    room for itself; the reload chip is a plate too, but has no bar to make
    room for, so its padding stays even."""
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(
            rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD], variant="plate", reload_chip=True, stage_bar=True
        )
        try:
            view.call("seek", 1.0 + 3.55)
            padding = view.page.evaluate(
                "() => { const s = getComputedStyle(document.getElementById('reloadChip'));"
                " return [s.paddingTop, s.paddingBottom]; }"
            )
            clock = view.page.evaluate(
                "() => { const s = getComputedStyle(document.getElementById('clockPlate'));"
                " return [s.paddingTop, s.paddingBottom]; }"
            )
            assert view.errors == []
        finally:
            view.close()
    assert padding[0] == padding[1]
    assert clock[0] != clock[1], "the clock plate still makes room for the bar"


def _rgb(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.removeprefix("rgb(").removesuffix(")").split(","))


@pytest.mark.integration
def test_timeline_activation_band_contrasts_with_the_fill() -> None:
    """Review minor: ``ink_2`` at half opacity over the ``ink`` fill came out
    near-white. Whatever the colour, the band as composited must stand off
    the fill."""
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[_TL_ACTIVATION], stage_bar=True)
        try:
            state = _timeline_at(view, 1.0 + 6.0)
            opacity = float(
                view.page.evaluate("() => getComputedStyle(document.querySelector('.band')).opacity")
            )
        finally:
            view.close()
    (band,) = state["bands"]
    fill, colour = _rgb(state["fill"]), _rgb(band["colour"])
    seen = [opacity * c + (1 - opacity) * f for c, f in zip(colour, fill, strict=True)]
    assert max(abs(s - f) for s, f in zip(seen, fill, strict=True)) > 60


@pytest.mark.integration
def test_timeline_reload_on_the_move_takes_the_top_half_of_the_track() -> None:
    """Where a reload meets a movement the reload draws on the track's top
    half over the full-height movement; outside it, the reload is full height."""
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD], stage_bar=True)
        try:
            state = _timeline_at(view, 1.0 + 6.0)
        finally:
            view.close()
    track = state["track"]
    height = track["bottom"] - track["top"]
    movement = [b for b in state["bands"] if b["kind"] == "movement"]
    reloads = sorted((b for b in state["bands"] if b["kind"] == "reload"), key=lambda b: b["box"]["left"])
    assert len(movement) == 1 and len(reloads) == 2
    on_move, after = reloads
    assert movement[0]["box"]["bottom"] - movement[0]["box"]["top"] == pytest.approx(height, abs=0.5)
    assert on_move["box"]["top"] == pytest.approx(track["top"], abs=0.5)
    assert on_move["box"]["bottom"] - on_move["box"]["top"] == pytest.approx(height / 2, abs=0.5)
    assert on_move["box"]["right"] == pytest.approx(movement[0]["box"]["right"], abs=0.5)
    assert after["box"]["bottom"] - after["box"]["top"] == pytest.approx(height, abs=0.5)
    assert after["box"]["left"] == pytest.approx(on_move["box"]["right"], abs=0.5)


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


# --- Plate, Pips, Ticker, Minimal: the same chip and bar, each in its own layout --

#: Each style's split display, which the chip must never cover, and the
#: corners it declares (``None`` for a style that places itself). Pips and
#: Minimal centre the split in a full-width block, so its text is measured.
_STYLE_SPLIT = {"plate": "#chip", "pips": "#splitValue", "ticker": "#list", "minimal": "#splitValue"}
_SPLIT_TEXT = ("pips", "minimal")
_STYLE_POSITIONS: dict[str, tuple[str | None, ...]] = {
    "plate": ("bottom-left", "top-left", "top-right", "bottom-right"),
    "pips": ("top-left", "top-right", "bottom-left", "bottom-right"),
    "ticker": ("top-right", "top-left"),
    "minimal": (None,),
}
_STYLES = tuple(_STYLE_SPLIT)

_STYLE_DOM_JS = """([split, splitText]) => {
  const box = (el) => { const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
  // The ink, not the block: a clock or a split may sit in a full-width box.
  const text = (el) => { const range = document.createRange(); range.selectNodeContents(el);
    return box(range); };
  const shown = (el) => { const s = getComputedStyle(el);
    return s.visibility === 'visible' && Number(s.opacity) > 0
      && (!el.parentElement || el.parentElement === document.body || shown(el.parentElement)); };
  const chip = document.getElementById('reloadChip');
  const bar = document.getElementById('stageBar');
  const fill = document.getElementById('stageFill');
  const splitEl = document.querySelector(split);
  return {
    chip: chip ? {opacity: Number(getComputedStyle(chip).opacity), label: chip.firstChild.textContent,
                  num: chip.lastChild.textContent, labelColour: getComputedStyle(chip.firstChild).color,
                  box: box(chip)} : null,
    clock: text(document.getElementById('clock')),
    split: splitEl && shown(splitEl) ? (splitText ? text(splitEl) : box(splitEl)) : null,
    bar: bar ? box(bar) : null,
    fill: fill ? getComputedStyle(fill).backgroundColor : null,
    bands: [...document.querySelectorAll('.band')].map((b) => ({
      kind: b.dataset.kind,
      shown: getComputedStyle(b).visibility === 'visible' && b.getBoundingClientRect().width > 0,
      colour: getComputedStyle(b).backgroundColor, opacity: Number(getComputedStyle(b).opacity),
      box: box(b)})),
  };
}"""


def _style_at(view: Any, variant: str, t: float) -> dict[str, Any]:
    view.call("seek", t)
    state = view.page.evaluate(_STYLE_DOM_JS, [_STYLE_SPLIT[variant], variant in _SPLIT_TEXT])
    assert view.errors == []
    return state


def _inside(a: dict[str, float], width: float = 640, height: float = 360) -> bool:
    return a["left"] >= 0 and a["top"] >= 0 and a["right"] <= width and a["bottom"] <= height


@pytest.mark.integration
@pytest.mark.parametrize("variant", _STYLES)
def test_style_reload_chip_counts_the_reload_and_holds_its_duration_through_the_fade(variant: str) -> None:
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(
            rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD], variant=variant, reload_chip=True
        )
        try:
            before = _style_at(view, variant, 1.0 + 2.5)
            mid = _style_at(view, variant, 1.0 + 2.55 + 1.0)
            fading = _style_at(view, variant, 1.0 + 3.7 + 0.2)
            gone = _style_at(view, variant, 1.0 + 3.7 + 0.45)
        finally:
            view.close()
    assert before["chip"]["opacity"] == 0
    chip = mid["chip"]
    assert chip["opacity"] == 1 and chip["label"].lower() == "reload" and chip["num"] == "1.00"
    assert chip["labelColour"] == "rgb(251, 191, 36)", "the reload colour, never the brand red"
    assert 0.4 < fading["chip"]["opacity"] < 0.6 and fading["chip"]["num"] == "1.15"
    assert gone["chip"]["opacity"] == 0


@pytest.mark.integration
@pytest.mark.parametrize(("variant", "position"), [(v, p) for v in _STYLES for p in _STYLE_POSITIONS[v]])
def test_style_chip_and_bar_clear_the_clock_and_the_split(variant: str, position: str | None) -> None:
    """16:9, every corner the style declares: the chip stays on the frame and
    off the clock and the split display, mid-stage and on the first shot
    (a reload right after the draw); the bar sits under the clock."""
    early = {"id": "evt-4", "kind": "reload", "start": 1.15, "end": 1.3, "source": "manual"}
    states = []
    with ChromiumRasterizer() as rasterizer:
        # Instants where a split is up in every style (Minimal's flashes for 0.5 s).
        for events, t in (([_TL_MOVEMENT, _TL_RELOAD], 2.6), ([_TL_MOVEMENT, early], 1.25)):
            view = _timeline_view(
                rasterizer,
                events=events,
                variant=variant,
                position=position,
                reload_chip=True,
                stage_bar=True,
            )
            try:
                states.append(_style_at(view, variant, 1.0 + t))
            finally:
                view.close()
    for state in states:
        chip, clock, bar = state["chip"], state["clock"], state["bar"]
        assert chip["opacity"] == 1 and state["split"] is not None
        assert _inside(chip["box"])
        assert not _overlaps(chip["box"], clock)
        assert not _overlaps(chip["box"], state["split"])
        # The clock's box is its line box, descent included: under means
        # below the digits, which fill the top three quarters of it.
        under = clock["top"] + 0.75 * (clock["bottom"] - clock["top"])
        assert _inside(bar) and bar["top"] >= under, "the bar runs under the clock"
        assert bar["left"] < clock["right"] and clock["left"] < bar["right"]
        assert not _overlaps(chip["box"], bar)


#: A twelve-second stage, so the clock reads five digits ("10.60").
_LONG_SHOTS = (1.1, 1.4, 1.7, 2.0, 4.0, 4.3, 6.5, 6.8, 9.9, 10.15, 11.4, 12.0)


@pytest.mark.integration
@pytest.mark.parametrize("position", _STYLE_POSITIONS["ticker"])
def test_ticker_chip_stays_on_an_upright_page_at_a_stage_time_of_ten_seconds_or_more(position: str) -> None:
    """9:16, a 12 s stage: a chip beside a five-digit clock is wider than
    the page, so upright the chip is the row under the stack -- inside the
    page and off the clock and the splits."""
    reload = {"id": "evt-2", "kind": "reload", "start": 10.2, "end": 11.0, "source": "manual"}
    width, height = 1080, 1920
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(
            rasterizer,
            events=[reload],
            variant="ticker",
            size=(width, height),
            shot_times=_LONG_SHOTS,
            position=position,
            reload_chip=True,
            stage_bar=True,
        )
        try:
            state = _style_at(view, "ticker", 1.0 + 10.6)
        finally:
            view.close()
    chip = state["chip"]
    assert chip["opacity"] == 1 and chip["num"] == "0.40" and state["split"] is not None
    assert _inside(chip["box"], width, height)
    assert not _overlaps(chip["box"], state["clock"])
    assert not _overlaps(chip["box"], state["split"])
    assert not _overlaps(chip["box"], state["bar"])


@pytest.mark.integration
@pytest.mark.parametrize("variant", _STYLES)
def test_style_draws_no_chip_and_no_bar_with_the_toggles_off(variant: str) -> None:
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(rasterizer, events=[_TL_MOVEMENT, _TL_RELOAD, _TL_ACTIVATION], variant=variant)
        try:
            state = _style_at(view, variant, 1.0 + 3.55)
        finally:
            view.close()
    assert state["chip"] is None and state["bar"] is None and state["bands"] == []


@pytest.mark.integration
@pytest.mark.parametrize("variant", _STYLES)
def test_style_stage_bar_draws_one_band_per_confirmed_region_as_it_happens(variant: str) -> None:
    proposal = {**_TL_ACTIVATION, "id": "evt-4", "source": "auto"}
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(
            rasterizer,
            events=[_TL_MOVEMENT, _TL_RELOAD, _TL_ACTIVATION, proposal],
            variant=variant,
            stage_bar=True,
            landing=False,
        )
        try:
            mid_movement = _style_at(view, variant, 1.0 + 2.25)
            landed = _style_at(view, variant, 1.0 + 6.0)
        finally:
            view.close()
    # The reload is cut in two where it meets the movement; the proposal draws nothing.
    assert sorted(b["kind"] for b in landed["bands"]) == ["activation", "movement", "reload", "reload"]
    shown = {b["kind"]: b["shown"] for b in mid_movement["bands"] if b["kind"] != "reload"}
    assert shown == {"movement": True, "activation": False}, "a region shows once it starts"
    assert not any(b["shown"] for b in mid_movement["bands"] if b["kind"] == "reload")
    assert all(b["shown"] for b in landed["bands"])
    bar = landed["bar"]
    for band in landed["bands"]:
        assert bar["left"] - 0.5 <= band["box"]["left"] and band["box"]["right"] <= bar["right"] + 0.5
    colours = {b["kind"]: b["colour"] for b in landed["bands"]}
    assert colours["movement"] == "rgb(6, 182, 212)" and colours["reload"] == "rgb(251, 191, 36)"
    (activation,) = [b for b in landed["bands"] if b["kind"] == "activation"]
    fill, colour, alpha = _rgb(landed["fill"]), _rgb(activation["colour"]), activation["opacity"]
    seen = [alpha * c + (1 - alpha) * f for c, f in zip(colour, fill, strict=True)]
    assert max(abs(s - f) for s, f in zip(seen, fill, strict=True)) > 60, "the activation reads on the fill"
    # The reload on the move: the slice over the movement takes the bar's top half.
    height = bar["bottom"] - bar["top"]
    on_move, after = sorted(
        (b for b in landed["bands"] if b["kind"] == "reload"), key=lambda b: b["box"]["left"]
    )
    assert on_move["box"]["top"] == pytest.approx(bar["top"], abs=0.5)
    assert on_move["box"]["bottom"] - on_move["box"]["top"] == pytest.approx(height / 2, abs=0.5)
    assert after["box"]["bottom"] - after["box"]["top"] == pytest.approx(height, abs=0.5)
    assert landed["chip"] is None


@pytest.mark.integration
@pytest.mark.parametrize("variant", _STYLES)
def test_style_clamps_a_reload_past_the_last_shot_and_settles_after_its_fade(variant: str) -> None:
    late = {"id": "evt-2", "kind": "reload", "start": 4.8, "end": 6.5, "source": "manual"}
    with ChromiumRasterizer() as rasterizer:
        view = _timeline_view(
            rasterizer, events=[late], variant=variant, reload_chip=True, stage_bar=True, landing=False
        )
        try:
            settle = view.call("hud")["settle"]
            landed = _style_at(view, variant, 1.0 + 6.2)
            held = _style_at(view, variant, 1.0 + 4.96 + settle)
        finally:
            view.close()
    assert settle >= 6.5 + 0.4 - 4.96 - 1e-6
    (band,) = landed["bands"]
    assert band["shown"] and band["box"]["right"] <= landed["bar"]["right"] + 0.5
    assert landed["chip"]["opacity"] == 1 and landed["chip"]["num"] == "1.40"
    assert held["chip"]["opacity"] == 0
