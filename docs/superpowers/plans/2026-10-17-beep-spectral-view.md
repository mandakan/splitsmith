# Beep Spectral View and Tone Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The beep step shows the beep in the timer's frequency band, as a spectrogram and as a band-passed waveform that Snap uses. The app also stores each confirmed beep's distance from its tone's start, and Overview flags a beep 20 ms or more off.

**Architecture:** One pure Python module, `splitsmith.beep_tone`, does the band-pass, the tone offset and the spectrogram. Three surfaces use it:
- the existing video peaks route, through a new `band` parameter;
- a new spectrum route;
- an offset stored on `StageVideo` when a beep is confirmed, which the triage payload carries to Overview.

The SPA adds a pure filter rule module, a spectrogram track on the shared timeline band, a filter control on `BeepTimeline` and a chip on Overview.

**Tech Stack:**
- Python 3.11, numpy, scipy.signal (already a dependency), soundfile, FastAPI, Pydantic.
- React 19, TypeScript, vitest, Tailwind 4.

**Spec:** `docs/superpowers/specs/2026-10-17-beep-spectral-view-design.md`

## Global Constraints

- The timer band default is 2200 to 3500 Hz, read from `BeepDetectionConfig.tonal_band_lo_hz` / `tonal_band_hi_hz`, never a new literal.
- The flag threshold is 20 ms (`BeepToneConfig.flag_ms`), carried to the SPA in the triage payload. The SPA has no literal of its own.
- Snap's reach for a beep is 0.1 s.
- The spectrogram covers 0 to 8000 Hz in about 5 ms columns, as an 8-bit dB grid over an 80 dB range. A request window is capped at 4 s.
- A request never runs ffmpeg. It reads the published WAV, and when that is missing it queues `derive_audio` and answers 202 (`_audio_pending`).
- A band parameter is `LO-HI` in Hz, with 100 <= LO < HI <= 12000. Anything else is 422.
- Colour is never the only cue. The spectrogram is a lightness ramp, and the Overview chip carries its label.
- No new dependency.
- What's new copy rules: ASCII only, no dash punctuation, title at most 48 characters, body at most 200.
- Run git as `/usr/bin/git` in this worktree.
- pytest: `env PATH=/Users/mathias/work/splitsmith/desktop/build/bin:/opt/homebrew/bin:/usr/bin:/bin /opt/homebrew/bin/uv run pytest -n0 -q -p no:cacheprovider <paths>`
- SPA tests: `pnpm -C src/splitsmith/ui_static exec vitest run <paths>`. Typecheck: `pnpm -C src/splitsmith/ui_static typecheck`. Lint: `pnpm -C src/splitsmith/ui_static lint`, where 45 warnings is the baseline.

## Deviations from the spec (rulings)

- **The stored offset carries the beep time it was measured for** (`beep_tone_for`). A value counts only while `beep_reviewed` is true and `beep_tone_for == beep_time`. The spec says to clear the offset on every unreview and every beep move. There are eight sites that flip `beep_reviewed` in `ui/server.py`, and one missed site would leave a stale flag. Self-invalidation cannot go stale. Cost if wrong: one more field.
- **`POST .../beep/snap` gets no band parameter.** Its only caller is the hosted phone path, which the spec puts out of scope. Desktop Snap uses the detail peaks, which do get the band. Cost if wrong: one parameter added later.

## Review Focus

1. **A beep near the start or end of the WAV.** The window for the band-pass, the offset and the spectrogram is clamped to the file, and none of them raises. Pinned in Task 1, `test_window_at_the_start_of_the_file`.
2. **A tone outside the default band, or silence.** `tone_offset_ms` answers `None`, never a number from noise. Pinned in Task 1, `test_silence_has_no_tone`.
3. **A stored offset from a beep that later moved or was unreviewed.** It never flags. Pinned in Task 4, `test_a_moved_beep_does_not_flag`.
4. **A malformed band, or a filter stored by an older browser in localStorage.** The route answers 422, and the SPA falls back to Timer tone. Pinned in Task 2, `test_band_validation`, and Task 6, `parseStored` cases.
5. **Spectrogram data for a window the band no longer shows**, after the beep moves or the user zooms. The track draws only data covering the visible range and fetches again. Pinned in Task 7, `refetches when the window leaves the data`.

---

### Task 1: `beep_tone` module and `BeepToneConfig`

**Files:**
- Create: `src/splitsmith/beep_tone.py`
- Modify: `src/splitsmith/rise_foot.py` (add a `tolerance_s` keyword to `rise_foot`)
- Modify: `src/splitsmith/config.py` (add `BeepToneConfig` next to `BeepDetectionConfig`, and a `beep_tone` field on the top-level `Config`)
- Test: `tests/test_beep_tone.py`
- Modify: `tests/fixtures/audio.ci.txt` (add the fixture WAV the test reads)

**Interfaces:**
- Produces:
  - `band_pass(audio: np.ndarray, sr: int, lo_hz: float, hi_hz: float) -> np.ndarray`
  - `tone_offset_ms(audio: np.ndarray, sr: int, beep_s: float, band: tuple[float, float] | None) -> float | None` (positive means the beep is before the tone's rise)
  - `spectrogram(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> Spectrogram`
  - `class Spectrogram(BaseModel): start: float; duration: float; hz: list[float]; columns: int; data: str` (base64 uint8, column-major: `len(hz)` bytes per column, low to high frequency)
  - `default_band() -> tuple[float, float]`
  - `BeepToneConfig(flag_ms: float = 20.0, reach_s: float = 0.1, window_s: float = 1.0, max_hz: float = 8000.0, max_window_s: float = 4.0)`
  - `rise_foot.rise_foot(peaks, duration, time, *, tolerance_s: float = TOLERANCE_S)`

- [ ] **Step 1: Write the failing tests**

```python
"""splitsmith.beep_tone: the timer tone's start, in its band (spec
2026-10-17-beep-spectral-view-design)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np
import pytest

from splitsmith import beep_detect, beep_tone

SR = 48000
FIXTURES = Path(__file__).parent / "fixtures"


def _tone(seconds: float, hz: float, start: float, length: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros_like(t, dtype=np.float32)
    on = (t >= start) & (t < start + length)
    out[on] = 0.5 * np.sin(2 * np.pi * hz * t[on])
    return out


def _click(seconds: float, at: float) -> np.ndarray:
    """A broadband transient (a shot's shape: sharp rise, fast decay), at a
    cross-bay shot's level: louder than the tone over the full range, far
    weaker than it inside the timer band (a shot spreads its energy)."""
    rng = np.random.default_rng(1)
    out = np.zeros(int(seconds * SR), dtype=np.float32)
    i = int(at * SR)
    n = int(0.15 * SR)
    out[i : i + n] = 0.3 * rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / (0.02 * SR))
    return out


def test_band_pass_keeps_the_tone_and_drops_the_low_end() -> None:
    sig = _tone(1.0, 2800, 0.2) + 0.5 * np.sin(2 * np.pi * 200 * np.arange(SR) / SR).astype(np.float32)
    out = beep_tone.band_pass(sig, SR, 2200, 3500)
    assert np.abs(out[int(0.05 * SR) : int(0.15 * SR)]).max() < 0.05  # the 200 Hz hum is gone
    assert np.abs(out[int(0.3 * SR) : int(0.5 * SR)]).max() > 0.4  # the tone is kept


def test_a_shot_before_the_tone_moves_the_full_range_foot_not_the_band_one() -> None:
    # A click 40 ms before a 2.8 kHz tone: in the full range the rise is the
    # click's; in the timer band it is the tone's.
    sig = _tone(2.0, 2800, 1.0) + _click(2.0, 0.96)
    assert beep_tone.tone_offset_ms(sig, SR, 1.0, (2200, 3500)) == pytest.approx(0.0, abs=6)
    full = beep_tone.tone_offset_ms(sig, SR, 1.0, None)
    assert full is not None and full < -25


def test_silence_has_no_tone() -> None:
    assert beep_tone.tone_offset_ms(np.zeros(2 * SR, dtype=np.float32), SR, 1.0, (2200, 3500)) is None


def test_window_at_the_start_of_the_file() -> None:
    sig = _tone(1.0, 2800, 0.02)
    assert beep_tone.tone_offset_ms(sig, SR, 0.02, (2200, 3500)) == pytest.approx(0.0, abs=6)
    spec = beep_tone.spectrogram(sig, SR, -0.5, 0.5)
    assert spec.start == 0.0 and spec.duration == pytest.approx(0.5, abs=0.01)


def test_spectrogram_shape_and_tone_row() -> None:
    sig = _tone(2.0, 2800, 0.5, length=1.0)
    spec = beep_tone.spectrogram(sig, SR, 0.0, 2.0)
    assert spec.hz[0] == 0.0 and spec.hz[-1] <= 8000.0
    grid = np.frombuffer(base64.b64decode(spec.data), dtype=np.uint8).reshape(spec.columns, len(spec.hz))
    assert spec.columns == pytest.approx(2.0 / 0.005, rel=0.05)
    mid = grid[spec.columns // 2]
    assert abs(spec.hz[int(mid.argmax())] - 2800) < 100


def test_default_band_is_the_detectors_tonal_band() -> None:
    from splitsmith.config import BeepDetectionConfig

    cfg = BeepDetectionConfig()
    assert beep_tone.default_band() == (cfg.tonal_band_lo_hz, cfg.tonal_band_hi_hz)


def test_a_cross_bay_shot_on_a_real_beep() -> None:
    """Little Trouble stage 2 (Mathias, GO 3S): the full-range scan put the
    audited beep 79 ms off its tone. In the band the audited beep is on
    the tone. If this fails, the audited beep may be wrong: stop and
    report, do not tune the test."""
    stem = "stage-shots-little-trouble-in-big-satra-2026-stage2-s97dcec94"
    wav = FIXTURES / f"{stem}.wav"
    if not wav.exists():
        pytest.skip("fixture audio not fetched (scripts/fixture_audio.py fetch)")
    beep = json.loads((FIXTURES / f"{stem}.json").read_text())["beep_time"]
    audio, sr = beep_detect.load_audio(wav)
    banded = beep_tone.tone_offset_ms(audio, sr, beep, beep_tone.default_band())
    full = beep_tone.tone_offset_ms(audio, sr, beep, None)
    assert banded is not None and abs(banded) < 10
    assert full is not None and abs(full) >= 40
```

- [ ] **Step 2: Run them and watch them fail**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.beep_tone'`.

- [ ] **Step 3: Add the `tolerance_s` keyword to `rise_foot`**

In `src/splitsmith/rise_foot.py` change the signature and the one line that reads the global:

```python
def rise_foot(
    peaks: list[float] | np.ndarray, duration: float, time: float, *, tolerance_s: float = TOLERANCE_S
) -> float | None:
```

```python
    radius = max(1, round(tolerance_s / bin_w))
```

Every existing caller passes no keyword, so nothing else changes.

- [ ] **Step 4: Add `BeepToneConfig` to `config.py`**

Below `class BeepDetectionConfig`:

```python
class BeepToneConfig(BaseModel):
    """The beep step's tone check and spectral view (spec
    2026-10-17-beep-spectral-view-design). The band is the detector's own
    tonal band (``BeepDetectionConfig.tonal_band_*``); this holds the rest."""

    # A confirmed beep this far or farther from its tone's start is flagged.
    flag_ms: float = 20.0
    # Snap's reach for a beep: the rise looked for around the beep.
    reach_s: float = 0.1
    # Seconds either side of the beep the offset reads.
    window_s: float = 1.0
    # The spectrogram's top frequency and its longest request window.
    max_hz: float = 8000.0
    max_window_s: float = 4.0
```

On the top-level `Config` model, add `beep_tone: BeepToneConfig = Field(default_factory=BeepToneConfig)`, placed beside the existing `beep_detection` field and following its pattern.

- [ ] **Step 5: Write `src/splitsmith/beep_tone.py`**

```python
"""The timer tone's start, measured in its frequency band (spec
2026-10-17-beep-spectral-view-design).

A timer beep is a near-pure tone (2.3 to 3.2 kHz on the labelled corpus); a
cross-bay shot landing on it spreads energy across the spectrum. The full
range envelope rises with the shot, the band-passed one with the tone. Pure:
arrays in, numbers out, no I/O."""

from __future__ import annotations

import base64

import numpy as np
from pydantic import BaseModel
from scipy.signal import butter, sosfiltfilt, stft

from . import rise_foot
from .config import BeepDetectionConfig, BeepToneConfig

_CFG = BeepToneConfig()
_COLUMN_S = 0.005
_NPERSEG = 1024
_DB_RANGE = 80.0


class Spectrogram(BaseModel):
    """One window's magnitude, column-major uint8 (0 = -80 dB of the
    window's loudest bin, 255 = that bin), ``len(hz)`` bytes per column."""

    start: float
    duration: float
    hz: list[float]
    columns: int
    data: str


def default_band() -> tuple[float, float]:
    cfg = BeepDetectionConfig()
    return (cfg.tonal_band_lo_hz, cfg.tonal_band_hi_hz)


def band_pass(audio: np.ndarray, sr: int, lo_hz: float, hi_hz: float) -> np.ndarray:
    """Zero-phase 4th-order Butterworth band-pass: zero phase, because a
    phase shift would move the rise this exists to find."""
    hi = min(hi_hz, sr / 2 * 0.99)
    sos = butter(4, [lo_hz, hi], btype="band", fs=sr, output="sos")
    return sosfiltfilt(sos, np.asarray(audio, dtype=np.float64)).astype(np.float32)


def _window(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> tuple[np.ndarray, float]:
    lo = min(len(audio), max(0, int(round(start_s * sr))))
    hi = min(len(audio), max(lo, int(round(end_s * sr))))
    return audio[lo:hi], lo / sr


def tone_offset_ms(
    audio: np.ndarray, sr: int, beep_s: float, band: tuple[float, float] | None
) -> float | None:
    """The beep's distance from its tone's rise foot, ms; positive when the
    beep is before the rise. ``band`` None is the full range (the old rule).
    None when there is no rise within Snap's reach."""
    win, start = _window(audio, sr, beep_s - _CFG.window_s, beep_s + _CFG.window_s)
    if win.size == 0:
        return None
    sig = band_pass(win, sr, *band) if band is not None else win
    peaks, duration = rise_foot.peak_envelope(sig, sr)
    foot = rise_foot.rise_foot(peaks, duration, beep_s - start, tolerance_s=_CFG.reach_s)
    if foot is None:
        return None
    return round((foot + start - beep_s) * 1000, 1)


def spectrogram(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> Spectrogram:
    win, start = _window(audio, sr, start_s, min(end_s, start_s + _CFG.max_window_s))
    hop = max(1, int(round(_COLUMN_S * sr)))
    if win.size < _NPERSEG:
        win = np.pad(win, (0, _NPERSEG - win.size))
    freqs, _times, z = stft(
        win, fs=sr, nperseg=_NPERSEG, noverlap=_NPERSEG - hop, boundary="even", padded=False
    )
    keep = freqs <= _CFG.max_hz
    mag = np.abs(z[keep])
    ref = float(mag.max()) or 1.0
    db = 20 * np.log10(np.maximum(mag, ref * 10 ** (-_DB_RANGE / 20)) / ref)
    grid = np.round((db + _DB_RANGE) / _DB_RANGE * 255).astype(np.uint8)
    cols = grid.T.copy()  # column-major: one row per time column
    return Spectrogram(
        start=start,
        duration=len(win) / sr if win.size else 0.0,
        hz=[float(f) for f in freqs[keep]],
        columns=int(cols.shape[0]),
        data=base64.b64encode(cols.tobytes()).decode("ascii"),
    )
```

The `duration` reported for a window shorter than `_NPERSEG` is the padded length. That is acceptable, because the windows the beep step asks for are seconds long.

- [ ] **Step 6: Add the fixture WAV to the CI fetch list**

Append this line to `tests/fixtures/audio.ci.txt`:

```
stage-shots-little-trouble-in-big-satra-2026-stage2-s97dcec94.wav
```

- [ ] **Step 7: Run the tests and watch them pass**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone.py tests/test_rise_foot.py`
Expected: PASS.
- If `test_a_cross_bay_shot_on_a_real_beep` fails on the banded assertion, stop. The audited beep may be wrong; report that rather than change the test.
- If it fails only on the full-range assertion, the scan's 79 ms came from elsewhere. Ledger it, and keep only the banded assertion.

- [ ] **Step 8: Commit**

```bash
/usr/bin/git add src/splitsmith/beep_tone.py src/splitsmith/rise_foot.py src/splitsmith/config.py tests/test_beep_tone.py tests/fixtures/audio.ci.txt
/usr/bin/git commit -m "feat(beep): measure the tone's start in the timer band"
```

---

### Task 2: A `band` on the video peaks route

**Files:**
- Modify: `src/splitsmith/waveform.py` (`ensure_peaks_window` takes `band`)
- Modify: `src/splitsmith/ui/server.py` (`video_peaks` takes `band`, plus a `_parse_band` helper next to it)
- Test: `tests/test_beep_tone_routes.py` (new)

**Interfaces:**
- Consumes: `beep_tone.band_pass`.
- Produces:
  - `ensure_peaks_window(audio_path, bins, *, start, end, band: tuple[float, float] | None = None) -> PeaksResult`
  - `GET .../videos/{id}/peaks?bins&start&end&band=LO-HI`
  - `_parse_band(raw: str | None) -> tuple[float, float] | None`, which raises 422.

- [ ] **Step 1: Write the failing tests**

Build the project with `_seed_match_export_project` from `tests/test_ui_server.py`, the same way `tests/test_use_breakdown.py` does. That seeds a stage 1 with a primary video. Write a synthetic source WAV with `soundfile` where the published video WAV is read. Find the path through `audio_helpers.video_audio_path(root, 1, video, project=project)`, or by reading `published_video_audio` in `ui/audio.py`; the test writes to whatever path it reads. The WAV holds a click at 0.96 s and a 2.8 kHz tone from 1.0 s.

```python
"""The beep step's banded peaks and spectrum routes (spec 2026-10-17)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

SR = 48000


def _write_overlap_wav(path: Path) -> None:
    t = np.arange(3 * SR) / SR
    sig = np.where((t >= 1.0) & (t < 1.4), 0.5 * np.sin(2 * np.pi * 2800 * t), 0.0)
    rng = np.random.default_rng(1)
    i, n = int(0.96 * SR), int(0.15 * SR)
    # A cross-bay shot's level: over the tone full range, well under it in band.
    sig[i : i + n] += 0.3 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.02 * SR))
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), sig.astype(np.float32), SR)


@pytest.fixture
def overlap(tmp_path: Path):
    from splitsmith.match_project import MatchProject
    from splitsmith.ui import audio as audio_helpers

    from .test_ui_server import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter = root / "shooters" / "me"
    project = MatchProject.load(shooter)
    video = project.stage(1).primary()
    wav = audio_helpers.video_audio_path(shooter, 1, video, project=project)
    _write_overlap_wav(wav)
    return client, video.video_id


def _url(vid: str) -> str:
    return f"/api/shooters/me/stages/1/videos/{vid}/peaks?bins=2000&start=0.5&end=1.5"


def test_banded_peaks_lose_the_shot(overlap) -> None:
    client, vid = overlap
    full = client.get(_url(vid)).json()["peaks"]
    banded = client.get(_url(vid) + "&band=2200-3500").json()["peaks"]
    shot_bin = int((0.97 - 0.5) / 1.0 * 2000)
    assert full[shot_bin] > 0.3
    assert banded[shot_bin] < 0.3 * max(banded)


@pytest.mark.parametrize("band", ["3500-2200", "50-3000", "2200-13000", "abc", "2200"])
def test_band_validation(overlap, band: str) -> None:
    client, vid = overlap
    assert client.get(_url(vid) + f"&band={band}").status_code == 422
```

If `video_audio_path` is named differently, read `published_video_audio` in `src/splitsmith/ui/audio.py`, use the function that names the path it reads, and ledger the name.

- [ ] **Step 2: Run them and watch them fail**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_routes.py`
Expected:
- `test_banded_peaks_lose_the_shot` fails, because the band is ignored (unknown query parameters are dropped);
- `test_band_validation` fails with 200 instead of 422.

- [ ] **Step 3: Give `ensure_peaks_window` a band**

In `src/splitsmith/waveform.py`, change the function:

```python
def ensure_peaks_window(
    audio_path: Path, bins: int, *, start: float, end: float, band: tuple[float, float] | None = None
) -> PeaksResult:
    """... (keep the docstring) With ``band`` the window is band-passed
    first (``beep_tone.band_pass``) and scaled by its own loudest sample,
    so the tone fills the row however quiet it is beside a shot."""
    st = audio_path.stat()
    tag = f"-band{int(band[0])}-{int(band[1])}" if band else ""
    key = f"{audio_path.stem}.peaks-{bins}-{round(start * 1000)}-{round(end * 1000)}{tag}.json"
    cf = audio_path.with_name(key)
    hit = _read_cache(cf, audio_path)
    if hit is not None and hit.bins == bins:
        return hit
    audio, sr = beep_detect.load_audio(audio_path)
    n = audio.shape[0]
    lo = min(n, max(0, int(round(start * sr))))
    hi = min(n, max(lo, int(round(end * sr))))
    if band is not None:
        from .beep_tone import band_pass

        # Filter a little beyond the window so its edges carry no filter ramp.
        pad = int(0.05 * sr)
        a, b = max(0, lo - pad), min(n, hi + pad)
        window = band_pass(audio[a:b], sr, *band)[lo - a : lo - a + (hi - lo)]
        scale = float(np.abs(window).max()) if window.size else 0.0
    else:
        window = audio[lo:hi]
        scale = float(np.abs(audio).max()) if n else 0.0
    result = compute_peaks(window, sr, bins, scale=scale)
    result.start = lo / sr if sr else 0.0
    result.source_size = st.st_size
    result.source_mtime_ns = st.st_mtime_ns
    _write_cache(cf, result, st)
    return result
```

- [ ] **Step 4: Parse the band in the route**

In `src/splitsmith/ui/server.py`, define `_parse_band` just above `video_peaks`. It sits inside `create_app`, like its neighbours:

```python
    def _parse_band(raw: str | None) -> tuple[float, float] | None:
        """``LO-HI`` in Hz, 100 <= LO < HI <= 12000, or None for the full range."""
        if raw is None:
            return None
        try:
            lo_s, hi_s = raw.split("-")
            lo, hi = float(lo_s), float(hi_s)
        except ValueError:
            raise HTTPException(status_code=422, detail="band is LO-HI in Hz") from None
        if not (100 <= lo < hi <= 12000):
            raise HTTPException(status_code=422, detail="band is LO-HI in Hz, 100 <= LO < HI <= 12000")
        return (lo, hi)
```

Add `band: str | None = Query(default=None)` to `video_peaks`'s parameters. Parse it before resolving audio: `band_range = _parse_band(band)`. A band without a window is the whole file, so also refuse it:

```python
        if band_range is not None and start is None:
            raise HTTPException(status_code=422, detail="a band needs a window (start and end)")
```

Pass `band=band_range` to `ensure_peaks_window`.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_routes.py tests/test_waveform.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/waveform.py src/splitsmith/ui/server.py tests/test_beep_tone_routes.py
/usr/bin/git commit -m "feat(beep): band-passed peaks for one window of a video's audio"
```

---

### Task 3: The spectrum route

**Files:**
- Modify: `src/splitsmith/ui/server.py` (new route next to `video_peaks`)
- Test: `tests/test_beep_tone_routes.py`

**Interfaces:**
- Consumes: `beep_tone.spectrogram`, `beep_detect.load_audio`, `_resolve_video_audio`.
- Produces: `GET /api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/spectrum?start&end` -> `Spectrogram` JSON, or 202 pending.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_beep_tone_routes.py`)

```python
def test_spectrum_shows_the_tone(overlap) -> None:
    import base64

    client, vid = overlap
    r = client.get(f"/api/shooters/me/stages/1/videos/{vid}/spectrum?start=0.5&end=1.5")
    assert r.status_code == 200, r.text
    body = r.json()
    grid = np.frombuffer(base64.b64decode(body["data"]), dtype=np.uint8).reshape(body["columns"], len(body["hz"]))
    col = int((1.2 - body["start"]) / body["duration"] * body["columns"])
    assert abs(body["hz"][int(grid[col].argmax())] - 2800) < 100


def test_spectrum_window_is_capped(overlap) -> None:
    client, vid = overlap
    body = client.get(f"/api/shooters/me/stages/1/videos/{vid}/spectrum?start=0&end=60").json()
    assert body["duration"] <= 4.0 + 1e-6


def test_spectrum_without_audio_is_pending(tmp_path) -> None:
    from .test_ui_server import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    from splitsmith.match_project import MatchProject

    vid = MatchProject.load(root / "shooters" / "me").stage(1).primary().video_id
    r = client.get(f"/api/shooters/me/stages/1/videos/{vid}/spectrum?start=0&end=1")
    assert r.status_code == 202 and r.json()["pending"] is True
```

The pending test relies on the seeded project having no published WAV for the video. If the seed writes one, delete it in the test before the request.

- [ ] **Step 2: Run them and watch them fail**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_routes.py -k spectrum`
Expected: FAIL with 404 (no route).

- [ ] **Step 3: Add the route** below `video_peaks`

```python
    @app.get("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/spectrum")
    def video_spectrum(
        slug: str,
        stage_number: int,
        video_id: str,
        start: float = Query(ge=0),
        end: float = Query(gt=0),
    ) -> JSONResponse:
        """The beep step's spectrogram row (spec 2026-10-17): one window of
        the video's own WAV, 0 to 8 kHz, ~5 ms columns, the window capped at
        ``BeepToneConfig.max_window_s``. Reads the published WAV; when it is
        missing, queues ``derive_audio`` and answers 202 like the peaks."""
        from .. import beep_tone

        if end <= start:
            raise HTTPException(status_code=422, detail="start before end")
        project, _stage, video = _resolve_stage_video(slug, stage_number, video_id)
        audit = _resolve_video_audio(slug, project, stage_number, video)
        if isinstance(audit, JSONResponse):
            return audit
        audio, sr = beep_detect.load_audio(audit.audio_path)
        return JSONResponse(beep_tone.spectrogram(audio, sr, start, end).model_dump(mode="json"))
```

If `beep_detect` isn't already imported in `server.py`, import it inside the function: `from .. import beep_detect`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_routes.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/ui/server.py tests/test_beep_tone_routes.py
/usr/bin/git commit -m "feat(beep): a spectrogram route for the beep step"
```

---

### Task 4: The stored tone offset

**Files:**
- Modify: `src/splitsmith/match_project.py` (two fields on `StageVideo`, plus a pure `tone_flag_ms` function)
- Create: `src/splitsmith/ui/beep_tone_check.py` (`record_tone_offset`)
- Modify: `src/splitsmith/ui/server.py`. Call it from:
  - `_after_beep_reviewed`, before its mirror early return is fine;
  - `override_beep_for_video`, after `_apply_beep_override`, before `project.save`;
  - `_run_derive_audio`'s `target == "video"` branch, after `ensure_video_audio`. It reloads and saves the project only if the value changed.
- Test: `tests/test_beep_tone_check.py`

**Interfaces:**
- Consumes: `beep_tone.tone_offset_ms`, `beep_tone.default_band`, `audio_helpers.published_video_audio`.
- Produces:
  - `StageVideo.beep_tone_offset_ms: float | None = None` and `StageVideo.beep_tone_for: float | None = None`;
  - `tone_flag_ms(video: StageVideo) -> float | None`: the stored offset while it counts, else None;
  - `record_tone_offset(root: Path, project: MatchProject, stage_number: int, video: StageVideo) -> bool`: True when a measurement was written; reads only a published WAV and never derives one.

- [ ] **Step 1: Write the failing tests**

```python
"""The confirmed beep's distance from its tone, stored on the video
(spec 2026-10-17; the measured-for rule is the plan's ruling)."""

from __future__ import annotations

from splitsmith.match_project import StageVideo, tone_flag_ms


def _video(**kw) -> StageVideo:
    base = {"video_id": "v1", "path": "raw/v.mov", "role": "primary", "beep_time": 5.0, "beep_reviewed": True}
    return StageVideo(**{**base, **kw})


def test_a_measured_reviewed_beep_counts() -> None:
    assert tone_flag_ms(_video(beep_tone_offset_ms=-51.0, beep_tone_for=5.0)) == -51.0


def test_a_moved_beep_does_not_flag() -> None:
    assert tone_flag_ms(_video(beep_tone_offset_ms=-51.0, beep_tone_for=4.9)) is None


def test_an_unreviewed_beep_does_not_flag() -> None:
    assert tone_flag_ms(_video(beep_reviewed=False, beep_tone_offset_ms=-51.0, beep_tone_for=5.0)) is None


def test_an_older_project_without_the_fields_loads() -> None:
    v = StageVideo.model_validate({"video_id": "v1", "path": "raw/v.mov", "role": "primary"})
    assert v.beep_tone_offset_ms is None and tone_flag_ms(v) is None
```

Then a route-level test: confirm a beep through `POST .../videos/{id}/beep` (the manual override) on the `overlap` setup, with the click 40 ms before the tone and the beep placed at the tone's start, 1.0. Assert `tone_flag_ms` of the saved video is within 6 ms of 0. Reuse `overlap` by importing it from `tests/test_beep_tone_routes.py` (`from .test_beep_tone_routes import overlap  # noqa: F401`).

```python
def test_a_manual_beep_stores_its_tone_offset(overlap) -> None:  # noqa: F811
    from splitsmith.match_project import MatchProject

    client, vid = overlap
    r = client.post(f"/api/shooters/me/stages/1/videos/{vid}/beep", json={"beep_time": 1.0})
    assert r.status_code == 200, r.text
    video = MatchProject.model_validate(r.json()).stage(1).find_video_by_id(vid)
    assert video.beep_tone_for == 1.0
    assert abs(tone_flag_ms(video)) < 6
```

The override answers with the project (`project.model_dump`), so read the video from `r.json()`.

- [ ] **Step 2: Run them and watch them fail**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_check.py`
Expected: FAIL with `ImportError: cannot import name 'tone_flag_ms'`.

- [ ] **Step 3: Add the fields and the rule** to `match_project.py`

On `StageVideo`, next to `beep_confidence`:

```python
    # The confirmed beep's distance from its tone's start, ms, measured in
    # the timer band (``beep_tone.tone_offset_ms``), and the ``beep_time``
    # it was measured for. A value counts only while the beep is reviewed
    # and still at that time (``tone_flag_ms``), so a moved or unreviewed
    # beep never shows a stale flag. Derived: an older install that drops
    # it loses nothing the next confirm or the backfill does not write again.
    beep_tone_offset_ms: float | None = None
    beep_tone_for: float | None = None
```

At module level, after the class:

```python
def tone_flag_ms(video: StageVideo) -> float | None:
    """The stored tone offset while it describes the confirmed beep, else None."""
    if not video.beep_reviewed or video.beep_time is None or video.beep_tone_offset_ms is None:
        return None
    if video.beep_tone_for is None or abs(video.beep_tone_for - video.beep_time) > 1e-6:
        return None
    return video.beep_tone_offset_ms
```

- [ ] **Step 4: Write `ui/beep_tone_check.py`**

```python
"""Write a confirmed beep's tone offset onto its video (spec 2026-10-17).

Reads only a published WAV (``published_video_audio``); a missing one is
not derived here, since requests read and jobs make. The derive job calls this
again once the WAV exists."""

from __future__ import annotations

from pathlib import Path

from .. import beep_detect, beep_tone
from ..match_project import MatchProject, StageVideo
from . import audio as audio_helpers


def record_tone_offset(root: Path, project: MatchProject, stage_number: int, video: StageVideo) -> bool:
    if not video.beep_reviewed or video.beep_time is None:
        return False
    wav = audio_helpers.published_video_audio(root, stage_number, video, project=project, source=None)
    if wav is None:
        return False
    audio, sr = beep_detect.load_audio(wav)
    video.beep_tone_offset_ms = beep_tone.tone_offset_ms(audio, sr, float(video.beep_time), beep_tone.default_band())
    video.beep_tone_for = float(video.beep_time)
    return True
```

Check `published_video_audio`'s signature in `ui/audio.py`. If `source=None` is not accepted, pass `source=_local_source(...)` from the caller, add a `source` parameter here, and ledger it.

- [ ] **Step 5: Call it from the three sites**

- In `override_beep_for_video`, after `_apply_beep_override(...)` and before `project.save(...)`:

  ```python
          beep_tone_check.record_tone_offset(state.shooter_root(slug), project, stage.stage_number, video)
  ```

- In `_after_beep_reviewed`, at the top, before the mirror early return: reload the project, find the video, record and save.

  ```python
          proj = state.shooter_project(slug)
          target = proj.stage(stage_number).find_video_by_id(video.video_id)
          if target is not None and beep_tone_check.record_tone_offset(state.shooter_root(slug), proj, stage_number, target):
              proj.save(state.shooter_root(slug))
  ```

- In `_run_derive_audio`'s `target == "video"` branch, after `ensure_video_audio(...)`:

  ```python
              if beep_tone_check.record_tone_offset(root, proj, stage_number, video):
                  proj.save(root)
  ```

Add `from . import beep_tone_check` to `server.py`'s local imports.

- [ ] **Step 6: Run the tests and watch them pass**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_check.py tests/test_beep_tone_routes.py`
Expected: PASS.

- [ ] **Step 7: Prove the override test bites**

Comment out the `record_tone_offset` call in `override_beep_for_video` (back the file up with `cp`), rerun `test_a_manual_beep_stores_its_tone_offset`, and see it fail. Restore with `cp` and confirm with `cmp`.

- [ ] **Step 8: Commit**

```bash
/usr/bin/git add src/splitsmith/match_project.py src/splitsmith/ui/beep_tone_check.py src/splitsmith/ui/server.py tests/test_beep_tone_check.py
/usr/bin/git commit -m "feat(beep): store a confirmed beep's distance from its tone"
```

---

### Task 5: Triage carries the flag; the backfill script

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`TriageCell.beep_tone_offset_ms`, `TriageResponse.beep_tone_flag_ms`, and setting both in the triage builder)
- Create: `scripts/measure_beep_tones.py`
- Test: `tests/test_beep_tone_check.py` (a triage test)

**Interfaces:**
- Consumes: `tone_flag_ms`, `record_tone_offset`, `beep_tone.tone_offset_ms`, `BeepToneConfig.flag_ms`.
- Produces:
  - `TriageCell.beep_tone_offset_ms: float | None`: the stage's largest-magnitude counted offset across its videos;
  - `TriageResponse.beep_tone_flag_ms: float`.

- [ ] **Step 1: Write the failing test**

```python
def test_triage_carries_the_worst_counted_offset(overlap) -> None:  # noqa: F811
    client, vid = overlap
    client.post(f"/api/shooters/me/stages/1/videos/{vid}/beep", json={"beep_time": 0.96})
    body = client.get("/api/match/triage").json()
    cell = next(c for c in body["cells"] if c["stage_number"] == 1)
    assert body["beep_tone_flag_ms"] == 20.0
    assert cell["beep_tone_offset_ms"] is not None and cell["beep_tone_offset_ms"] > 20
```

Placing the beep on the click, 40 ms before the tone, makes the band-limited offset about +40.

- [ ] **Step 2: Run it and watch it fail**

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_beep_tone_check.py -k triage`
Expected: FAIL with `KeyError: 'beep_tone_flag_ms'`.

- [ ] **Step 3: Add the fields and fill them**

- In `class TriageCell`, add `beep_tone_offset_ms: float | None = None`.
- In `class TriageResponse`, add `beep_tone_flag_ms: float = 20.0`.
- In the builder, before `cells.append(TriageCell(...))`:

  ```python
                  tones = [o for o in (tone_flag_ms(v) for v in stg.videos if v.role != "ignored") if o is not None]
  ```

  and pass `beep_tone_offset_ms=max(tones, key=abs) if tones else None`.
- In the `TriageResponse(...)` return, pass `beep_tone_flag_ms=Config.load(None).beep_tone.flag_ms`, using the config accessor the module already uses elsewhere.
- Import `tone_flag_ms` from `..match_project`.

- [ ] **Step 4: Run it and watch it pass**

Same command. Expected: PASS.

- [ ] **Step 5: Write the backfill script `scripts/measure_beep_tones.py`**

```python
"""Measure every confirmed beep's distance from its tone (spec 2026-10-17).

For each match on X9 (or ``--matches``), each shooter, each stage video with
a reviewed beep and a published WAV: the band-limited offset
(``beep_tone.tone_offset_ms``) and, for comparison, the full-range one the
old scan used. Prints both; ``--write`` stores the band-limited one on the
video (``beep_tone_offset_ms`` / ``beep_tone_for``) and saves the project.

Usage:
    uv run python scripts/measure_beep_tones.py [--matches DIR ...] [--write]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from splitsmith import beep_detect, beep_tone
from splitsmith.config import BeepToneConfig
from splitsmith.match_project import MatchProject
from splitsmith.ui import audio as audio_helpers

DEFAULT_MATCHES = Path("/Volumes/X9/matches")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--matches", type=Path, nargs="*")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    flag = BeepToneConfig().flag_ms
    n = flagged = 0
    for m in args.matches or sorted(p for p in DEFAULT_MATCHES.iterdir() if p.is_dir()):
        shooters = m / "shooters"
        if not shooters.is_dir():
            continue
        for root in sorted(p for p in shooters.iterdir() if (p / "project.json").exists()):
            project = MatchProject.load(root)
            changed = False
            for stage in project.stages:
                for video in stage.videos:
                    if not video.beep_reviewed or video.beep_time is None:
                        continue
                    wav = audio_helpers.published_video_audio(root, stage.stage_number, video, project=project, source=None)
                    if wav is None:
                        print(f"  {m.name} {root.name} stage {stage.stage_number} {video.video_id}: no published WAV")
                        continue
                    audio, sr = beep_detect.load_audio(wav)
                    banded = beep_tone.tone_offset_ms(audio, sr, float(video.beep_time), beep_tone.default_band())
                    full = beep_tone.tone_offset_ms(audio, sr, float(video.beep_time), None)
                    n += 1
                    off = banded is not None and abs(banded) >= flag
                    flagged += off
                    print(
                        f"{'!' if off else ' '} {m.name} {root.name} stage {stage.stage_number} {video.role:9s} "
                        f"band {banded if banded is not None else '-':>7} ms  full {full if full is not None else '-':>7} ms"
                    )
                    if args.write:
                        video.beep_tone_offset_ms = banded
                        video.beep_tone_for = float(video.beep_time)
                        changed = True
            if changed:
                project.save(root)
    print(f"{n} beeps measured; {flagged} at {flag:.0f} ms or more from their tone{'; written' if args.write else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it as a dry run on the four matches of PR #1530. Paste its output for the nine stages the old scan flagged into the PR description. Do not run `--write` without the user saying so. It writes into the user's match folders, which auto-sync pushes to hosted.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/ui/server.py scripts/measure_beep_tones.py tests/test_beep_tone_check.py
/usr/bin/git commit -m "feat(beep): triage carries the tone check; a backfill script"
```

---

### Task 6: SPA rules and API (`lib/beepFilter`, `api`)

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/beepFilter.ts`, `src/splitsmith/ui_static/src/lib/beepFilter.test.ts`
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (a `band` on `getVideoPeaks`, a new `getVideoSpectrum` and the `Spectrogram` type, and the two triage fields on the types)

**Interfaces:**
- Produces:
  - `type BeepFilter = { kind: "off" } | { kind: "tone" } | { kind: "custom"; lo: number; hi: number }`
  - `TIMER_BAND = { lo: 2200, hi: 3500 }`. This mirrors `BeepDetectionConfig`; the server is authoritative, and the comment says to change both together.
  - `bandOf(f: BeepFilter): { lo: number; hi: number } | null`
  - `bandParam(f: BeepFilter): string | undefined`, giving `"2200-3500"`
  - `validBand(lo: number, hi: number): boolean`
  - `parseStored(raw: string | null): BeepFilter`, which defaults to `{ kind: "tone" }`
  - `useBeepFilter(): [BeepFilter, (f: BeepFilter) => void]`, stored per browser under `splitsmith.beepFilter` with try/catch
  - `api.getVideoPeaks(slug, stage, videoId, bins, window?, opts?, band?: string)`
  - `api.getVideoSpectrum(slug, stage, videoId, window: {start: number; end: number}): Promise<Spectrogram | Pending>`, which goes through `whenReady` like peaks
  - `interface Spectrogram { start: number; duration: number; hz: number[]; columns: number; data: string }`
  - `TriageCell.beep_tone_offset_ms?: number | null`, `TriageResponse.beep_tone_flag_ms?: number`

- [ ] **Step 1: Write the failing tests** (`lib/beepFilter.test.ts`)

```ts
import { describe, expect, it } from "vitest";

import { TIMER_BAND, bandOf, bandParam, parseStored, validBand } from "@/lib/beepFilter";

describe("beepFilter", () => {
  it("names the band each choice filters to", () => {
    expect(bandOf({ kind: "off" })).toBeNull();
    expect(bandOf({ kind: "tone" })).toEqual(TIMER_BAND);
    expect(bandOf({ kind: "custom", lo: 2000, hi: 4000 })).toEqual({ lo: 2000, hi: 4000 });
    expect(bandParam({ kind: "tone" })).toBe("2200-3500");
    expect(bandParam({ kind: "off" })).toBeUndefined();
  });

  it("accepts the server's band range only", () => {
    expect(validBand(2200, 3500)).toBe(true);
    expect(validBand(3500, 2200)).toBe(false);
    expect(validBand(50, 3000)).toBe(false);
    expect(validBand(2200, 13000)).toBe(false);
  });

  it("reads what a browser stored, falling back to the timer tone", () => {
    expect(parseStored(null)).toEqual({ kind: "tone" });
    expect(parseStored("not json")).toEqual({ kind: "tone" });
    expect(parseStored(JSON.stringify({ kind: "off" }))).toEqual({ kind: "off" });
    expect(parseStored(JSON.stringify({ kind: "custom", lo: 9000, hi: 100 }))).toEqual({ kind: "tone" });
    expect(parseStored(JSON.stringify({ kind: "custom", lo: 2000, hi: 4000 }))).toEqual({ kind: "custom", lo: 2000, hi: 4000 });
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm -C src/splitsmith/ui_static exec vitest run src/lib/beepFilter.test.ts`
Expected: FAIL, because the module is missing.

- [ ] **Step 3: Write `lib/beepFilter.ts`**

```ts
/**
 * The beep step's frequency filter (spec 2026-10-17-beep-spectral-view-design):
 * Off, the timer tone, or a custom band. With a band, the Audio row and Snap
 * read band-passed peaks, so the tone's rise shows under a cross-bay shot.
 */
import { useCallback, useState } from "react";

export type BeepFilter = { kind: "off" } | { kind: "tone" } | { kind: "custom"; lo: number; hi: number };

/** The detector's tonal band (``BeepDetectionConfig.tonal_band_*``); the
 *  server is authoritative, change both together. */
export const TIMER_BAND = { lo: 2200, hi: 3500 } as const;

const KEY = "splitsmith.beepFilter";

export function validBand(lo: number, hi: number): boolean {
  return Number.isFinite(lo) && Number.isFinite(hi) && lo >= 100 && hi <= 12000 && lo < hi;
}

export function bandOf(f: BeepFilter): { lo: number; hi: number } | null {
  if (f.kind === "off") return null;
  if (f.kind === "tone") return { ...TIMER_BAND };
  return { lo: f.lo, hi: f.hi };
}

export function bandParam(f: BeepFilter): string | undefined {
  const b = bandOf(f);
  return b ? `${Math.round(b.lo)}-${Math.round(b.hi)}` : undefined;
}

export function parseStored(raw: string | null): BeepFilter {
  try {
    const v = raw ? (JSON.parse(raw) as Partial<BeepFilter> & { lo?: number; hi?: number }) : null;
    if (v?.kind === "off") return { kind: "off" };
    if (v?.kind === "custom" && typeof v.lo === "number" && typeof v.hi === "number" && validBand(v.lo, v.hi)) {
      return { kind: "custom", lo: v.lo, hi: v.hi };
    }
  } catch {
    // A value an older build stored, or garbage: the default.
  }
  return { kind: "tone" };
}

export function useBeepFilter(): [BeepFilter, (f: BeepFilter) => void] {
  const [filter, setFilter] = useState<BeepFilter>(() => {
    try {
      return parseStored(window.localStorage.getItem(KEY));
    } catch {
      return { kind: "tone" };
    }
  });
  const set = useCallback((f: BeepFilter) => {
    setFilter(f);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(f));
    } catch {
      // Private mode: the choice lasts the page.
    }
  }, []);
  return [filter, set];
}
```

- [ ] **Step 4: Extend `api.ts`**
  - Add the trailing `band?: string` parameter to `getVideoPeaks`, and append `+ (band ? `&band=${band}` : "")` to its URL.
  - Add `getVideoSpectrum`, built like `getVideoPeaks`: `whenReady` around `request<Spectrogram | Pending>(\`.../videos/${id}/spectrum?start=${w.start}&end=${w.end}\`)`.
  - Export `interface Spectrogram`.
  - Add `beep_tone_offset_ms?: number | null` to `TriageCell` and `beep_tone_flag_ms?: number` to `TriageResponse`.

- [ ] **Step 5: Run the tests and the typecheck**

Run: `pnpm -C src/splitsmith/ui_static exec vitest run src/lib/beepFilter.test.ts && pnpm -C src/splitsmith/ui_static typecheck`
Expected: PASS, 0 errors.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/lib/beepFilter.ts src/splitsmith/ui_static/src/lib/beepFilter.test.ts src/splitsmith/ui_static/src/lib/api.ts
/usr/bin/git commit -m "feat(beep): the beep filter rules and the spectrum API"
```

---

### Task 7: `SpectrogramTrack` and the filter on `BeepTimeline`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/audit/SpectrogramTrack.tsx`, `src/splitsmith/ui_static/src/components/audit/SpectrogramTrack.test.tsx`
- Modify: `src/splitsmith/ui_static/src/components/audit/BeepTimeline.tsx`
- Modify: `src/splitsmith/ui_static/src/components/audit/BeepTimeline.test.tsx` (it exists; extend it)

**Interfaces:**
- Consumes: `api.getVideoSpectrum`, `api.getVideoPeaks(..., band)`, `useBeepFilter`, `bandParam`, `bandOf`, `TIMER_BAND`, `Segmented`, `TimelineGeom`.
- Produces: `SpectrogramTrack({ spectrum, from, to, geom, height, band }: { spectrum: Spectrogram | null; from: number; to: number; geom: TimelineGeom; height: number; band: { lo: number; hi: number } | null })`.

- [ ] **Step 1: Write the failing tests**

`SpectrogramTrack.test.tsx`: render with a 2-column, 4-bin spectrum and check that it only draws the visible window. jsdom has no canvas; mock `HTMLCanvasElement.prototype.getContext` to return a recorder with `putImageData` and `createImageData`, then assert the image width equals `Math.floor(viewportWidth)`. Also check the band hairlines:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SpectrogramTrack } from "@/components/audit/SpectrogramTrack";

const SPEC = { start: 0, duration: 2, hz: [0, 2000, 4000, 6000], columns: 2, data: btoa(String.fromCharCode(0, 10, 200, 0, 0, 10, 200, 0)) };
const GEOM = { contentWidth: 1000, viewportWidth: 300, scrollLeft: 100, pxPerSec: 500 };

describe("SpectrogramTrack", () => {
  it("draws only the visible window and outlines the band", () => {
    const created: number[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      () =>
        ({
          setTransform() {},
          clearRect() {},
          createImageData: (w: number, h: number) => {
            created.push(w);
            return { data: new Uint8ClampedArray(w * h * 4), width: w, height: h };
          },
          putImageData() {},
        }) as unknown as CanvasRenderingContext2D,
    );
    render(<SpectrogramTrack spectrum={SPEC} from={0} to={2} geom={GEOM} height={60} band={{ lo: 2200, hi: 3500 }} />);
    expect(created[0]).toBe(300);
    expect(screen.getByTestId("spectrum-band-lo")).toBeInTheDocument();
    expect(screen.getByTestId("spectrum-band-hi")).toBeInTheDocument();
  });

  it("says when there is no spectrum yet", () => {
    render(<SpectrogramTrack spectrum={null} from={0} to={2} geom={GEOM} height={60} band={null} />);
    expect(screen.getByText("Preparing spectrum")).toBeInTheDocument();
  });
});
```

`BeepTimeline.test.tsx` (extend it with the existing mocks of `api.getVideoPeaks`; mock `api.getVideoSpectrum` to resolve the `SPEC` above):

```tsx
it("asks for the timer band's peaks by default and none with the filter off", async () => {
  // render BeepTimeline as the existing tests do, wait for the detail fetch
  await waitFor(() => expect(api.getVideoPeaks).toHaveBeenCalledWith(expect.anything(), expect.anything(), expect.anything(), expect.anything(), expect.objectContaining({ start: expect.any(Number) }), undefined, "2200-3500"));
  await userEvent.click(within(screen.getByRole("group", { name: "Filter" })).getByRole("button", { name: "Off" }));
  await waitFor(() => expect(vi.mocked(api.getVideoPeaks).mock.calls.at(-1)?.[6]).toBeUndefined());
  expect(screen.getByRole("button", { name: "Snap" })).toBeInTheDocument();
});

it("labels Snap as to the tone while a band is on and draws the spectrogram row", async () => {
  // render as above
  expect(await screen.findByRole("button", { name: "Snap to tone" })).toBeInTheDocument();
  expect(screen.getByText("Spectrum")).toBeInTheDocument();  // the row's gutter label
});

it("refetches when the window leaves the data", async () => {
  // render, then move the draft beep 3 s later through the candidate pins
  // or the Audio row's seek the existing tests use
  await waitFor(() => expect(api.getVideoSpectrum).toHaveBeenCalledTimes(2));
});
```

Write these the way the existing `BeepTimeline.test.tsx` renders and drives the component: same props, same mocks, same way of moving the beep. Match the exact Snap button's accessible name to the existing one, "Snap" (read it first).

- [ ] **Step 2: Run them and watch them fail**

Run: `pnpm -C src/splitsmith/ui_static exec vitest run src/components/audit/SpectrogramTrack.test.tsx src/components/audit/BeepTimeline.test.tsx`
Expected: FAIL, because the module is missing and there is no Filter group.

- [ ] **Step 3: Write `SpectrogramTrack.tsx`**

```tsx
/**
 * The beep step's spectrogram row (spec 2026-10-17): time across, 0 to
 * 8 kHz up, drawn only for the visible window into a viewport-sized sticky
 * canvas like WaveformTrack. A lightness ramp, so it reads without colour;
 * the timer band is two hairlines with a label.
 */
import { useEffect, useMemo, useRef } from "react";

import type { TimelineGeom } from "@/components/timeline/types";
import type { Spectrogram } from "@/lib/api";

export interface SpectrogramTrackProps {
  spectrum: Spectrogram | null;
  /** Clip seconds at the band's left and right edge. */
  from: number;
  to: number;
  geom: TimelineGeom;
  height: number;
  band: { lo: number; hi: number } | null;
}

export function SpectrogramTrack({ spectrum, from, to, geom, height, band }: SpectrogramTrackProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const grid = useMemo(() => {
    if (!spectrum) return null;
    const raw = atob(spectrum.data);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }, [spectrum]);
  const { contentWidth, viewportWidth, scrollLeft } = geom;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !spectrum || !grid || viewportWidth <= 0) return;
    const w = Math.max(1, Math.floor(viewportWidth));
    const h = Math.max(1, Math.floor(height));
    canvas.width = w;
    canvas.height = h;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(w, h);
    const bins = spectrum.hz.length;
    const top = spectrum.hz[bins - 1] || 1;
    const span = to - from;
    for (let x = 0; x < w; x++) {
      const t = from + ((scrollLeft + x) / contentWidth) * span;
      const col = Math.floor(((t - spectrum.start) / spectrum.duration) * spectrum.columns);
      if (col < 0 || col >= spectrum.columns) continue; // outside the data: left dark
      for (let y = 0; y < h; y++) {
        const hz = (1 - y / (h - 1)) * top;
        const bin = Math.min(bins - 1, Math.round((hz / top) * (bins - 1)));
        const v = grid[col * bins + bin];
        const l = Math.round(Math.pow(v / 255, 1.6) * 255); // dark floor, bright tone
        const o = (y * w + x) * 4;
        img.data[o] = l;
        img.data[o + 1] = l;
        img.data[o + 2] = l;
        img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [spectrum, grid, from, to, contentWidth, viewportWidth, scrollLeft, height]);

  if (!spectrum) {
    return (
      <div className="sticky left-0 flex items-center px-2 text-sm text-muted" style={{ width: viewportWidth || "100%", height }}>
        Preparing spectrum
      </div>
    );
  }
  const top = spectrum.hz[spectrum.hz.length - 1] || 1;
  const yPct = (hz: number) => `${(1 - hz / top) * 100}%`;
  return (
    <div className="relative" style={{ height }}>
      <canvas ref={ref} data-testid="spectrogram-track" className="pointer-events-none sticky left-0 block" />
      {band ? (
        <>
          <div data-testid="spectrum-band-hi" className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-2/70" style={{ top: yPct(band.hi) }} />
          <div data-testid="spectrum-band-lo" className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-2/70" style={{ top: yPct(band.lo) }} />
          <span className="pointer-events-none sticky left-1 text-xs text-ink-2" style={{ position: "absolute", top: yPct(band.hi) }}>
            timer
          </span>
        </>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Wire it into `BeepTimeline.tsx`**
- `const [filter, setFilter] = useBeepFilter();` and `const band = bandParam(filter);`.
- The detail fetch passes `band` as the last argument to `api.getVideoPeaks(...)`, and `band` is added to that effect's dependencies. `setDetail(null)` runs when the band changes, so the old detail never draws.
- Spectrum state: `const [spectrum, setSpectrum] = useState<Spectrogram | null>(null)`, fetched for `[chosenLocalNow - 1.5, chosenLocalNow + 1.5]` clamped to `[0, peaks.duration]`. Fetch again when the chosen beep is within 0.5 s of the held window's edge, the same rule the detail uses. Ignore a `Pending` answer: `whenReady` retries.
- A new track between `candidates` and `audio`:

  ```tsx
      {
        id: "spectrum",
        rows: [{ label: "Spectrum", height: SPECTRUM_ROW_H }],
        seekable: true,
        render: (geom) => (
          <SpectrogramTrack spectrum={spectrum} from={0} to={peaks.duration} geom={geom} height={SPECTRUM_ROW_H} band={bandOf(filter)} />
        ),
      },
  ```

  with `const SPECTRUM_ROW_H = 72;` beside the other row heights.
- The filter control goes in `actionsEnd`, before the distance line:

  ```tsx
            <Segmented
              label="Filter"
              value={filter.kind}
              onChange={(k) => setFilter(k === "custom" ? { kind: "custom", lo: TIMER_BAND.lo, hi: TIMER_BAND.hi } : { kind: k })}
              options={[
                { value: "off", label: "Off" },
                { value: "tone", label: "Timer tone" },
                { value: "custom", label: "Custom" },
              ]}
            />
            {filter.kind === "custom" ? (
              <span className="inline-flex items-center gap-1 text-sm text-muted">
                <input aria-label="Low Hz" type="number" className="numeral w-16 rounded-md border border-rule bg-surface-2 px-1" value={filter.lo}
                  onChange={(e) => { const lo = Number(e.target.value); if (validBand(lo, filter.hi)) setFilter({ ...filter, lo }); }} />
                to
                <input aria-label="High Hz" type="number" className="numeral w-16 rounded-md border border-rule bg-surface-2 px-1" value={filter.hi}
                  onChange={(e) => { const hi = Number(e.target.value); if (validBand(filter.lo, hi)) setFilter({ ...filter, hi }); }} />
                Hz
              </span>
            ) : null}
  ```

- The Snap button's text: `{filter.kind === "off" ? "Snap" : "Snap to tone"}`, with the title "Move the beep onto the tone's rising edge" kept.

Check the 1440 px header fit (CLAUDE.md: a new control goes in the header if it fits one row at 1440 wide, else the band's `...` menu). If it doesn't fit in the browser check (Task 9), move Custom's number fields into the band's `menuExtra` and ledger the move.

- [ ] **Step 5: Run the tests, the typecheck and lint**

Run: `pnpm -C src/splitsmith/ui_static exec vitest run src/components/audit src/lib/beepFilter.test.ts && pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static lint`
Expected: PASS, 0 type errors, lint at 45 warnings.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/components/audit/SpectrogramTrack.tsx src/splitsmith/ui_static/src/components/audit/SpectrogramTrack.test.tsx src/splitsmith/ui_static/src/components/audit/BeepTimeline.tsx src/splitsmith/ui_static/src/components/audit/BeepTimeline.test.tsx
/usr/bin/git commit -m "feat(beep): the spectrogram row and the timer tone filter on the beep step"
```

---

### Task 8: The Overview "Check beep" chip

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/overview.ts` (`OverviewCell.checkBeep: number | null`, filled in `toCell` from the triage cell and response)
- Modify: `src/splitsmith/ui_static/src/components/overview/OverviewTable.tsx` (`BeepCell` shows the chip)
- Modify: `src/splitsmith/ui_static/src/components/overview/OverviewCards.tsx` (the same chip, beside the flag chip)
- Test: `src/splitsmith/ui_static/src/lib/overview.test.ts` (existing; extend it)

**Interfaces:**
- Consumes: `TriageCell.beep_tone_offset_ms`, `TriageResponse.beep_tone_flag_ms`.
- Produces: `OverviewCell.checkBeep: number | null`, the offset in ms when it is at or over the threshold, else null.

- [ ] **Step 1: Write the failing test** (in `overview.test.ts`, using its existing triage-cell factory)

```ts
it("flags a beep at or past the tone threshold, and only then", () => {
  const rows = (offset: number | null) =>
    buildOverviewRows({
      triage: { ...TRIAGE, beep_tone_flag_ms: 20, cells: [{ ...CELL, beep_tone_offset_ms: offset }] },
      shooters: SHOOTERS,
      leadSlug: null,
      jobs: [],
    })[0].cells[0].checkBeep;
  expect(rows(-51)).toBe(-51);
  expect(rows(20)).toBe(20);
  expect(rows(19.9)).toBeNull();
  expect(rows(null)).toBeNull();
});
```

Name the factory objects after what the file already defines. Read it first.

- [ ] **Step 2: Run it and watch it fail**

Run: `pnpm -C src/splitsmith/ui_static exec vitest run src/lib/overview.test.ts`
Expected: FAIL, because `checkBeep` is undefined.

- [ ] **Step 3: Implement**
- `toCell(raw, jobs, threshold, toneFlag)` computes `checkBeep: raw.beep_tone_offset_ms != null && Math.abs(raw.beep_tone_offset_ms) >= toneFlag ? raw.beep_tone_offset_ms : null`.
- `buildOverviewRows` passes `triage.beep_tone_flag_ms ?? Infinity`, so an older server never flags.
- In `BeepCell` (table), after the confidence chip:

  ```tsx
        {cell.checkBeep != null ? (
          <Chip tone="warn" title={`${Math.abs(Math.round(cell.checkBeep))} ms ${cell.checkBeep > 0 ? "before" : "after"} the tone starts`}>
            check beep
          </Chip>
        ) : null}
  ```

- Add the same chip in `OverviewCards`, next to the flag chip.

- [ ] **Step 4: Run it and watch it pass**, plus the overview component tests

Run: `pnpm -C src/splitsmith/ui_static exec vitest run src/lib/overview.test.ts src/components/overview src/pages/Home*.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/lib/overview.ts src/splitsmith/ui_static/src/lib/overview.test.ts src/splitsmith/ui_static/src/components/overview/OverviewTable.tsx src/splitsmith/ui_static/src/components/overview/OverviewCards.tsx
/usr/bin/git commit -m "feat(overview): a Check beep chip when a beep sits off its tone"
```

---

### Task 9: What's new, CLAUDE.md, browser check, suites

**Files:**
- Modify: `src/splitsmith/data/whats_new.json`
- Modify: `CLAUDE.md` (a short paragraph after the Audit beep-step text in "UI: the visual budget": `BeepTimeline`'s Spectrum row, `lib/beepFilter`, the band on peaks, `beep_tone`, the measured-for rule, the Overview chip and `scripts/measure_beep_tones.py`)

- [ ] **Step 1: What's new entry** (at the top of `entries`)

```json
    {
      "id": "beep-timer-tone",
      "date": "2026-10-17",
      "title": "Find the beep under a shot",
      "body": "The beep step on Audit shows a spectrogram and filters to the timer's tone, so Snap finds the beep even when a shot lands on it. Overview marks a beep that sits off its tone."
    },
```

Run: `env PATH=... uv run pytest -n0 -q -p no:cacheprovider tests/test_whats_new.py`
Expected: PASS.

- [ ] **Step 2: Look at it in a browser**
- Seed `scripts/seed_demo_match.py ~/.claude-tmp/beep-demo --media`, so there's a real clip with a beep.
- Start `splitsmith ui` with `SPLITSMITH_HOME` set to a scratch dir and `SPLITSMITH_AUTO_SYNC=0`, on a port other than 5174.
- Open the match through the recent-projects API by path.
- Open a stage's beep step at 1440x900. Screenshot it with Timer tone on and with Filter off, and read both screenshots: the Spectrum row draws, the band lines sit at 2.2 and 3.5 kHz, Snap's label switches, and the header fits one row.
- Stop the server and remove the scratch dirs.

- [ ] **Step 3: Full suites**
- Run the full pytest suite with `-q`. The known flaky ones are `test_import_waitlist` and `test_audio_storage_cache`; name them if they fail.
- Run `pnpm -C src/splitsmith/ui_static exec vitest run`. The known failure is the Node 26 one in `lazyPage.test.tsx`.
- Run the typecheck and lint.
- Run `uv run black --check src tests scripts` and `uv run ruff check src tests scripts`.

- [ ] **Step 4: Dry-run the backfill** on the PR #1530 matches and keep its output for the PR description:

```bash
env PATH=... uv run python scripts/measure_beep_tones.py --matches /Volumes/X9/matches/bofors-bombardment-2026 /Volumes/X9/matches/little-trouble-in-big-satra-2026 /Volumes/X9/matches/hugelsta-six-station-fall-2026 /Volumes/X9/matches/oden-cup-2026
```

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/data/whats_new.json CLAUDE.md
/usr/bin/git commit -m "docs(beep): What's new and CLAUDE.md for the tone check"
```
