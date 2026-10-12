# Derived Media Self-Heal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trims and web renditions are published only when they decode, older-keyed caches are renamed once to the keys hosted reads, the reconciler rebuilds a trim that is missing on disk whatever its flag says, the push never uploads an invalid file, and `splitsmith match verify-media` says per stage video whether a match will play on hosted.

**Architecture:** A pure MP4 structure check (`media_check.valid_mp4`) gates every publish and every freshness decision. One rendition writer (`trim.write_web_rendition`) replaces the two copies of the transcode-then-rename code, with a per-process partial name and a per-file lock. A one-time migration (`sync/migrate.py`) renames legacy-keyed caches before each sync, recorded in `auto_sync.json`. The reconciler takes the set of videos whose trim is valid on disk. The verify command reads the same facts.

**Tech Stack:** Python 3.11, pydantic, pytest; ffmpeg only in integration tests.

**Spec:** `docs/superpowers/specs/2026-10-12-derived-media-self-heal-design.md`

## Global Constraints

- Never change an audit, a beep, a stage time, a note or a region.
- Never upload a file that fails `valid_mp4`.
- No new dependency; the MP4 check is pure Python over the box structure, no ffprobe on the hot path.
- Partial files end in `.partial.mp4` so every existing glob that excludes or sweeps partials still matches.
- `SPLITSMITH_AUTO_SYNC=0` for any server started while verifying; never sync the real `~/.splitsmith` from a test or script.
- Commits end with the session's Co-Authored-By and Claude-Session lines; `git` is `/usr/bin/git` in this worktree; never `git add src` (node_modules symlink).
- Black, line length 110; ruff clean.

## Rulings against the spec (made while planning)

- **Spec part 5 (hosted reports `missing`, the `heal_media` desktop command) is a separate plan.** It touches every SPA player and the hosted command queue; parts 1 to 4 are what heal the user's matches. Cost if wrong: hosted keeps redirecting a missing rendition to the source until that plan ships.
- **No new `web_trim` reconcile step.** Every sync already runs `backfill_web_trims` before the push; once it treats an invalid rendition as missing it re-cuts it. Cost if wrong: an invalid rendition waits for the next sync instead of a job.
- **Old-keyed objects already on hosted are left there.** Hosted's delete route refuses a `_web.mp4` without its trim in storage and every params sidecar; relaxing that is riskier than a few never-requested objects. Cost if wrong: some R2 bytes.
- **`verify-media` checks hosted presence through `sync_state.items`** (what this desktop uploaded, with sizes), not a new hosted listing route. Cost if wrong: an object deleted on hosted behind the desktop's back reads as present.
- **The command is `splitsmith match verify-media <match>`**, beside `match trims`; there is no `sync` CLI group.
- **The migration runs at the start of every sync** (cheap after the first run), not at match open.
- **No duration comparison between a rendition and its trim.** The structural check (a parseable `moov`, a video track with samples) is what catches the 2026-09-27 corruption, and a rendition is cut from its trim, so they share one window by construction. Cost if wrong: a rendition that decodes but is truncated passes; add the comparison if one turns up.

## Review Focus

1. Two processes cutting the same rendition at once must end with one valid file and no interleaved bytes (the 2026-09-27 failure).
2. A rendition that is structurally valid but covers a different window (a stale cut after a re-trim) must still read as not fresh: validity adds to the mtime rule, never replaces it.
3. A legacy-keyed cache registered on more than one stage (a multi-stage single take) must never be renamed.
4. A match folder on an unplugged volume, or a trim file that vanishes between listing and reading, must not crash a sync: the check returns "invalid", the step is planned, the failure memo stops a retry loop.
5. A real ffmpeg rendition, including the full-range `yuvj420p` ones and video tracks that start after 0 (39 of 89 local renditions), must pass the check.

---

### Task 1: `media_check.valid_mp4`

**Files:**
- Create: `src/splitsmith/media_check.py`
- Create: `tests/media_bytes.py` (test helper: tiny valid and corrupt MP4 bytes)
- Test: `tests/test_media_check.py`

**Interfaces:**
- Produces: `class MediaCheck(BaseModel): ok: bool; reason: str | None = None; duration_s: float | None = None; video_samples: int = 0`
- Produces: `def valid_mp4(path: Path) -> MediaCheck`
- Produces: `def valid_mp4_cached(path: Path) -> MediaCheck` (memoised on `(str(path), size, mtime_ns)`)
- Produces (tests): `tests.media_bytes.tiny_mp4(*, duration_s: float = 1.0, video_samples: int = 25, with_video: bool = True) -> bytes` and `tests.media_bytes.corrupt_moov_mp4() -> bytes`

- [ ] **Step 1: Write the test helper**

```python
# tests/media_bytes.py
"""Tiny MP4 byte strings for tests: structurally valid (what valid_mp4 accepts)
and the corrupt shape found on 2026-10-12 (a moov whose first child claims the
size of the media data)."""

from __future__ import annotations

import struct


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def _full(typ: bytes, payload: bytes) -> bytes:
    return _box(typ, b"\x00\x00\x00\x00" + payload)


def _trak(handler: bytes, samples: int) -> bytes:
    hdlr = _full(b"hdlr", b"\x00\x00\x00\x00" + handler + b"\x00" * 12 + b"\x00")
    stsz = _full(b"stsz", struct.pack(">II", 1000, samples))
    stbl = _box(b"stbl", stsz)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", hdlr + minf)
    return _box(b"trak", mdia)


def tiny_mp4(*, duration_s: float = 1.0, video_samples: int = 25, with_video: bool = True) -> bytes:
    timescale = 1000
    mvhd = _full(b"mvhd", struct.pack(">IIII", 0, 0, timescale, int(duration_s * timescale)) + b"\x00" * 80)
    traks = (_trak(b"vide", video_samples) if with_video else b"") + _trak(b"soun", 40)
    moov = _box(b"moov", mvhd + traks)
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + moov + mdat


def corrupt_moov_mp4() -> bytes:
    """The Stockholm stage 1 shape: a full-length file whose moov's first
    child claims far more bytes than the moov holds."""
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    bogus_child = struct.pack(">I", 16_770_410) + b"mvhd" + b"\x00" * 92
    moov = _box(b"moov", bogus_child)
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + moov + mdat
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_media_check.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from splitsmith import media_check
from tests.media_bytes import corrupt_moov_mp4, tiny_mp4
from tests.synthetic_media import build_synthetic_video, ffmpeg_available


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_a_structurally_valid_file_passes_with_its_duration(tmp_path: Path) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "ok.mp4", tiny_mp4(duration_s=2.5)))
    assert check.ok, check.reason
    assert check.duration_s == pytest.approx(2.5)
    assert check.video_samples == 25


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (corrupt_moov_mp4(), "moov"),
        (tiny_mp4(with_video=False), "video"),
        (tiny_mp4(video_samples=0), "video"),
        (tiny_mp4()[:40], "moov"),
        (b"", "empty"),
    ],
)
def test_broken_files_fail_and_say_why(tmp_path: Path, data: bytes, reason: str) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "bad.mp4", data))
    assert not check.ok
    assert reason in (check.reason or "")


def test_a_missing_or_unreadable_file_is_invalid_not_an_error(tmp_path: Path) -> None:
    assert not media_check.valid_mp4(tmp_path / "gone.mp4").ok


def test_the_cache_rechecks_a_file_that_changed(tmp_path: Path) -> None:
    p = _write(tmp_path / "x.mp4", tiny_mp4())
    assert media_check.valid_mp4_cached(p).ok
    p.write_bytes(corrupt_moov_mp4())
    os.utime(p, (1_900_000_000, 1_900_000_000))
    assert not media_check.valid_mp4_cached(p).ok


@pytest.mark.integration
def test_a_real_ffmpeg_file_and_a_full_range_one_pass(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    src = build_synthetic_video(tmp_path / "src.mp4")
    assert media_check.valid_mp4(src).ok
    full_range = tmp_path / "full.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=in_range=tv:out_range=pc",
         "-pix_fmt", "yuvj420p", "-c:v", "libx264", "-movflags", "+faststart", str(full_range)],
        check=True,
    )
    assert media_check.valid_mp4(full_range).ok
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_media_check.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.media_check'`

- [ ] **Step 4: Implement**

```python
# src/splitsmith/media_check.py
"""Is an MP4 structurally playable? A pure walk of its boxes, no ffprobe.

Derived files (audit trims, web renditions) are published and trusted only
when this passes (spec 2026-10-12 derived-media self-heal): on 2026-09-27 two
processes interleaved their bytes in one partial file and 62 renditions with a
corrupt ``moov`` were renamed into place, trusted for being non-empty, and
uploaded. The check needs the top-level boxes to tile the file, a ``moov``
whose children parse inside it, a ``mvhd`` duration, and a video track
(``hdlr`` ``vide``) with samples. A few milliseconds per file.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


class MediaCheck(BaseModel):
    ok: bool
    reason: str | None = None
    duration_s: float | None = None
    video_samples: int = 0


class _Bad(Exception):
    pass


def _boxes(data: bytes, start: int, end: int):
    pos = start
    while pos < end:
        if end - pos < 8:
            raise _Bad("box header runs past its parent")
        size, typ = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            if end - pos < 16:
                raise _Bad("box header runs past its parent")
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise _Bad(f"{typ.decode('latin1')} box overruns its parent")
        yield typ, pos + header, pos + size
        pos += size


def _track(data: bytes, start: int, end: int) -> tuple[bytes | None, int]:
    handler: bytes | None = None
    samples = 0
    for typ, s, e in _boxes(data, start, end):
        if typ == b"hdlr" and e - s >= 12:
            handler = data[s + 8 : s + 12]
        elif typ == b"stsz" and e - s >= 12:
            samples = struct.unpack(">I", data[s + 8 : s + 12])[0]
        elif typ in _CONTAINERS:
            h, n = _track(data, s, e)
            handler = handler or h
            samples = samples or n
    return handler, samples


def _check(data: bytes) -> MediaCheck:
    if not data:
        return MediaCheck(ok=False, reason="empty file")
    moov = None
    for typ, s, e in _boxes(data, 0, len(data)):
        if typ == b"moov":
            moov = (s, e)
    if moov is None:
        return MediaCheck(ok=False, reason="no moov box")
    duration: float | None = None
    video_samples = 0
    for typ, s, e in _boxes(data, *moov):
        if typ == b"mvhd" and e - s >= 20:
            version = data[s]
            if version == 1 and e - s >= 32:
                timescale, dur = struct.unpack(">IQ", data[s + 20 : s + 32])
            else:
                timescale, dur = struct.unpack(">II", data[s + 12 : s + 20])
            if timescale:
                duration = dur / timescale
        elif typ == b"trak":
            handler, samples = _track(data, s, e)
            if handler == b"vide":
                video_samples = max(video_samples, samples)
    if video_samples <= 0:
        return MediaCheck(ok=False, reason="no video track with samples", duration_s=duration)
    return MediaCheck(ok=True, duration_s=duration, video_samples=video_samples)


def valid_mp4(path: Path) -> MediaCheck:
    """Structural check of the MP4 at ``path``; a missing or unreadable file
    is invalid, never an error."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return MediaCheck(ok=False, reason=f"unreadable: {exc.strerror or exc}")
    try:
        return _check(data)
    except _Bad as exc:
        return MediaCheck(ok=False, reason=f"moov or box structure broken: {exc}")


@lru_cache(maxsize=4096)
def _cached(path: str, size: int, mtime_ns: int) -> MediaCheck:
    return valid_mp4(Path(path))


def valid_mp4_cached(path: Path) -> MediaCheck:
    """``valid_mp4`` once per file version (path, size, mtime)."""
    try:
        st = path.stat()
    except OSError as exc:
        return MediaCheck(ok=False, reason=f"unreadable: {exc.strerror or exc}")
    return _cached(str(path), st.st_size, st.st_mtime_ns)
```

Note: `read_bytes` on a web rendition (5 to 60 MB) is acceptable for a check that runs once per file version; if profiling shows otherwise, read only the boxes (seek past `mdat`) in a follow-up. The `moov` reason string must contain "moov" for the corrupt shapes (the parametrised test checks it).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -n0 tests/test_media_check.py -q`
Expected: PASS (the integration test runs where ffmpeg is on PATH; CI has it).

- [ ] **Step 6: Check it against the real corpus (read only)**

Run a throwaway script in `~/.claude-tmp/` that calls `valid_mp4` on every `/Volumes/X9/matches/*/shooters/*/trimmed/*_web.mp4`.
Expected: exactly the 62 files the 2026-10-12 survey found unreadable (Stockholm 40, Oden 22) fail; every other rendition passes. Any other failure is a bug in the check: fix it before committing.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add src/splitsmith/media_check.py tests/media_bytes.py tests/test_media_check.py
/usr/bin/git commit -m "feat(media): a structural MP4 check for derived files"
```

### Task 2: One rendition writer; publish only what passes

**Files:**
- Modify: `src/splitsmith/trim.py` (`backfill_web_trims` at ~377; add `write_web_rendition`, `rendition_ok`, `unique_partial`)
- Modify: `src/splitsmith/ui/audio.py` (`_web_partial` ~854, `fresh_web_trim` ~915, `_ensure_web_trim` ~941, `ensure_video_audit_trim` ~1114)
- Modify: `src/splitsmith/match_project.py:221` (cleanup glob)
- Test: `tests/test_trim.py`, `tests/test_scrub_rendition.py`, plus every test the suite shows failing only because it fakes a rendition with a few bytes

**Interfaces:**
- Consumes: `media_check.valid_mp4`, `media_check.valid_mp4_cached`, `tests.media_bytes.tiny_mp4`
- Produces: `trim.unique_partial(final: Path) -> Path` (`<stem>.<pid>-<8 hex>.partial<suffix>`)
- Produces: `trim.rendition_ok(trimmed: Path) -> bool` (present, not older than the trim, `valid_mp4_cached` ok)
- Produces: `trim.write_web_rendition(trimmed: Path, config: WebTrimConfig, *, ffmpeg_binary: str = "ffmpeg", runner: Runner = subprocess.run) -> Path` (raises `FFmpegError` when the transcode fails or its output fails the check)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trim.py`:

```python
def test_two_writers_of_one_rendition_never_share_a_partial(tmp_path: Path) -> None:
    """The 2026-09-27 corruption: every process wrote <stem>.partial.mp4.
    Two concurrent writers now write their own partials, the lock makes the
    second find the first's valid file, and nothing interleaves."""
    import threading

    from splitsmith.config import WebTrimConfig
    from splitsmith.trim import web_trim_path, write_web_rendition
    from tests.media_bytes import tiny_mp4

    d = tmp_path / "trimmed"
    d.mkdir()
    trimmed = d / "stage1_cam_aaa_trimmed.mp4"
    trimmed.write_bytes(tiny_mp4(duration_s=10))
    outputs: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def runner(cmd, **kwargs):
        out = Path(cmd[-1])
        outputs.append(out.name)
        data = tiny_mp4(duration_s=10)
        with out.open("wb") as fh:
            fh.write(data[: len(data) // 2])
            started.set()
            release.wait(5)
            fh.write(data[len(data) // 2 :])
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    errors: list[BaseException] = []

    def work() -> None:
        try:
            write_web_rendition(trimmed, WebTrimConfig(), runner=runner)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    a = threading.Thread(target=work)
    b = threading.Thread(target=work)
    a.start()
    started.wait(5)
    b.start()
    release.set()
    a.join(10)
    b.join(10)
    assert not errors
    assert len(outputs) == 1, "the second writer must find the first's rendition, not encode again"
    assert outputs[0].endswith(".partial.mp4") and outputs[0] != "stage1_cam_aaa_web.partial.mp4"
    from splitsmith.media_check import valid_mp4

    assert valid_mp4(web_trim_path(trimmed)).ok


def test_a_rendition_that_fails_the_check_is_never_published(tmp_path: Path) -> None:
    from splitsmith.config import WebTrimConfig
    from splitsmith.trim import FFmpegError, web_trim_path, write_web_rendition
    from tests.media_bytes import corrupt_moov_mp4, tiny_mp4

    d = tmp_path / "trimmed"
    d.mkdir()
    trimmed = d / "stage1_cam_aaa_trimmed.mp4"
    trimmed.write_bytes(tiny_mp4(duration_s=10))

    def runner(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(corrupt_moov_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with pytest.raises(FFmpegError, match="check"):
        write_web_rendition(trimmed, WebTrimConfig(), runner=runner)
    assert not web_trim_path(trimmed).exists()
    assert not list(d.glob("*.partial.mp4"))


def test_backfill_recuts_a_corrupt_rendition(tmp_path: Path) -> None:
    from splitsmith.config import WebTrimConfig
    from splitsmith.trim import backfill_web_trims
    from tests.media_bytes import corrupt_moov_mp4, tiny_mp4

    d = tmp_path / "trimmed"
    d.mkdir()
    trimmed = d / "stage1_cam_aaa_trimmed.mp4"
    trimmed.write_bytes(tiny_mp4(duration_s=10))
    (d / "stage1_cam_aaa_web.mp4").write_bytes(corrupt_moov_mp4())  # newer than the trim

    def runner(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(tiny_mp4(duration_s=10))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    cut, errors = backfill_web_trims(d, WebTrimConfig(), runner=runner)
    assert errors == []
    assert [p.name for p in cut] == ["stage1_cam_aaa_web.mp4"]
```

Add to `tests/test_scrub_rendition.py`:

```python
def test_fresh_web_trim_treats_a_corrupt_rendition_as_absent(tmp_path: Path) -> None:
    from splitsmith.ui.audio import fresh_web_trim
    from tests.media_bytes import corrupt_moov_mp4, tiny_mp4

    trimmed = tmp_path / "stage1_cam_aaa_trimmed.mp4"
    trimmed.write_bytes(tiny_mp4())
    web = tmp_path / "stage1_cam_aaa_web.mp4"
    web.write_bytes(corrupt_moov_mp4())
    assert fresh_web_trim(trimmed) is None
    web.write_bytes(tiny_mp4())
    assert fresh_web_trim(trimmed) == web
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_trim.py tests/test_scrub_rendition.py -q -k "share_a_partial or never_published or corrupt_rendition"`
Expected: FAIL (`ImportError: cannot import name 'write_web_rendition'`; the fresh_web_trim test returns the corrupt file).

- [ ] **Step 3: Implement the writer in `trim.py`**

Add near `web_trim_path` (line ~303) and replace the body of `backfill_web_trims`' loop:

```python
import fcntl
import os
import uuid

from .media_check import valid_mp4, valid_mp4_cached


def unique_partial(final: Path) -> Path:
    """A partial name no other process can share (the 2026-09-27 corruption
    came from every process writing ``<stem>.partial.mp4``). Ends in
    ``.partial.mp4`` so every glob that skips or sweeps partials still does."""
    return final.with_name(f"{final.stem}.{os.getpid()}-{uuid.uuid4().hex[:8]}.partial{final.suffix}")


def rendition_ok(trimmed: Path) -> bool:
    """The trim's web rendition is present, not older than the trim, and
    structurally valid. Validity adds to the mtime rule, never replaces it:
    a valid rendition of an older window is still stale."""
    web = web_trim_path(trimmed)
    try:
        if web.stat().st_size == 0 or web.stat().st_mtime < trimmed.stat().st_mtime:
            return False
    except OSError:
        return False
    return valid_mp4_cached(web).ok


def write_web_rendition(
    trimmed: Path,
    config: WebTrimConfig,
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> Path:
    """Cut the trim's web rendition and publish it only when it passes the
    structural check. A per-file lock makes a second writer wait and then
    reuse the first's rendition; the partial name is per process. Raises
    ``FFmpegError`` when ffmpeg fails or its output fails the check; the
    partial is always removed, a previous good rendition never replaced."""
    web = web_trim_path(trimmed)
    lock_path = web.with_name(f"{web.stem}.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if rendition_ok(trimmed):
            return web
        partial = unique_partial(web)
        try:
            transcode_web_trim(trimmed, partial, config, ffmpeg_binary=ffmpeg_binary, runner=runner)
            check = valid_mp4(partial)
            if not check.ok:
                raise FFmpegError(f"web rendition failed its check: {check.reason}")
            partial.replace(web)
        finally:
            if partial.exists():
                partial.unlink()
    return web
```

`backfill_web_trims`: the "todo" filter becomes `if rendition_ok(trimmed): continue`, and the per-trim body becomes:

```python
        try:
            write_web_rendition(trimmed, config, ffmpeg_binary=ffmpeg_binary, runner=runner)
        except FFmpegError as exc:
            errors.append(f"{trimmed.name}: web rendition not cut ({exc})")
            continue
        cut.append(web_trim_path(trimmed))
```

- [ ] **Step 4: Point the app's paths at it (`ui/audio.py`)**

- `_web_partial(web)` is removed; its callers in `invalidate_video_audit_trim` (~694) and `ensure_video_audit_trim` (~1179) sweep `web.parent.glob(f"{web.stem}.*partial{web.suffix}")` and `web.with_name(f"{web.stem}.partial{web.suffix}")` (an old-style leftover) instead.
- `fresh_web_trim`: after the `fresh_rendition` test passes, `if not valid_mp4_cached(web).ok: return None`.
- `_ensure_web_trim`: keep its order (fresh local, storage pull, transcode) and replace the transcode block with:

```python
    try:
        trim_module.write_web_rendition(
            trimmed,
            trim_module.web_trim_config(),
            ffmpeg_binary=ffmpeg_binary,
            **({"runner": runner} if runner is not None else {}),
        )
    except trim_module.FFmpegError as exc:
        logger.warning("web trim: %s: %s", trimmed.name, exc)
        return None
    _try_push_web_trim_to_storage(project, web)
    return web
```

  A rendition pulled from storage is checked with `valid_mp4` before it is trusted; an invalid pull is deleted and falls through to the transcode.
- `ensure_video_audit_trim`: `partial = trim_module.unique_partial(output)`; after `trim_video` succeeds, `check = media_check.valid_mp4(partial)`; when not ok, unlink the partial and `raise AudioExtractionError(f"trim failed its check: {check.reason}")`. The cache-hit branch also requires `media_check.valid_mp4_cached(output).ok`; an invalid trim is swept and re-cut like a params mismatch.
- `match_project.py:221`: the sweep glob becomes `"stage*_trimmed.*partial.mp4"` and gains `(trimmed_dir, "stage*_web.*partial.mp4")` and `(trimmed_dir, "stage*_web.lock")`.

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest -n0 tests/test_trim.py tests/test_scrub_rendition.py -q`
Expected: the three new tests pass; older tests that fake a rendition or a trim with a few bytes fail on the check.

- [ ] **Step 6: Convert the tests that fake media with a few bytes**

Run: `uv run pytest -q 2>&1 | tail -40` and list the failures. For each one whose cause is a fake rendition or trim (`write_bytes(b"WEB")`, `b"W"`, `_touch(...)` on a `_trimmed.mp4` or `_web.mp4` the code now validates), write `tests.media_bytes.tiny_mp4()` instead (fake runners: `Path(cmd[-1]).write_bytes(tiny_mp4())`). Change nothing else in those tests. A failure with any other cause is a bug in Steps 3 to 4: fix the code, not the test. Known files (2026-10-12 count of tests naming `_web.mp4`): `test_trim.py`, `test_media_presign_serving.py`, `test_sync_push.py`, `test_sync_media_api.py`, `test_sync_integration.py`, `test_auto_sync_service.py`, `test_hosted_scrub.py`, `test_sync_plan.py`, `test_trim_storage_cache.py`, `test_sync_pull.py`, `test_sync_local_endpoints.py`, `test_compare_stage_endpoint.py`, `test_coach_api.py`.

- [ ] **Step 7: Full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
/usr/bin/git add src/splitsmith/trim.py src/splitsmith/ui/audio.py src/splitsmith/match_project.py tests/
/usr/bin/git commit -m "fix(media): derived files are published only when they decode, never from a shared partial"
```

### Task 3: Migrate legacy-keyed caches once

**Files:**
- Create: `src/splitsmith/sync/migrate.py`
- Modify: `src/splitsmith/sync/auto_state.py:39` (`AutoSyncPrefs.cache_schema: int = 0`)
- Modify: `src/splitsmith/sync/run.py` (`run_sync`, first statement of the preflight phase)
- Test: `tests/test_sync_migrate.py`

**Interfaces:**
- Consumes: `ui.audio.legacy_video_id`, `ui.audio._legacy_cache_fallback`, `ui.audio.trimmed_video_path`, `ui.audio.video_audit_audio_path`, `ui.audio.trim_params_path`, `trim.web_trim_path`, `sync.auto_state.load_auto_prefs`, `update_auto_prefs`
- Produces: `CACHE_SCHEMA = 1`; `class MigrationReport(BaseModel): renamed: list[str]; ambiguous: list[str]`; `def migrate_legacy_caches(match_root: Path) -> MigrationReport`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sync_migrate.py
from __future__ import annotations

from pathlib import Path

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.sync.auto_state import load_auto_prefs
from splitsmith.sync.migrate import CACHE_SCHEMA, migrate_legacy_caches
from splitsmith.ui.audio import trimmed_video_path, web_trim_available
from tests.media_bytes import tiny_mp4


def _match(root: Path, *, stages: list[int]) -> tuple[Path, MatchProject]:
    match = match_model.Match.init(root, name="M")
    match.add_shooter(root, match_model.Shooter(slug="me", name="Me"))
    sh = match_model.Match.shooter_root(root, "me")
    project = MatchProject.init(sh, name="M")
    project.stages = [
        StageEntry(stage_number=n, stage_name=f"S{n}", time_seconds=10.0,
                   videos=[StageVideo(path=Path("raw/v.mp4"), role="primary", beep_time=5.0,
                                      beep_reviewed=True)])
        for n in stages
    ]
    project.save(sh)
    return sh, MatchProject.load(sh)


def _legacy_files(sh: Path, stage: int, video: StageVideo) -> list[Path]:
    t = sh / "trimmed"
    t.mkdir(exist_ok=True)
    lid = video.legacy_video_id
    files = [t / f"stage{stage}_cam_{lid}_trimmed.mp4", t / f"stage{stage}_cam_{lid}_web.mp4"]
    for f in files:
        f.write_bytes(tiny_mp4())
    (t / f"stage{stage}_cam_{lid}_trimmed.params.json").write_text("{}")
    return files


def test_renames_a_legacy_trim_and_rendition_to_the_keys_hosted_reads(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1])
    video = project.stages[0].videos[0]
    _legacy_files(sh, 1, video)
    report = migrate_legacy_caches(tmp_path / "m")
    new = trimmed_video_path(sh, 1, video, project=project)
    assert new.exists() and new.with_name(new.name.replace("_trimmed.mp4", "_trimmed.params.json")).exists()
    assert web_trim_available(project, new)  # hosted's own lookup finds it now
    assert len(report.renamed) == 1 and report.ambiguous == []
    assert load_auto_prefs(tmp_path / "m").cache_schema == CACHE_SCHEMA


def test_leaves_a_multi_stage_single_take_alone(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1, 2])  # one source on two stages
    video = project.stages[0].videos[0]
    legacy = _legacy_files(sh, 1, video)
    report = migrate_legacy_caches(tmp_path / "m")
    assert all(f.exists() for f in legacy)
    assert report.renamed == [] and len(report.ambiguous) == 1


def test_a_second_run_does_nothing(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1])
    _legacy_files(sh, 1, project.stages[0].videos[0])
    migrate_legacy_caches(tmp_path / "m")
    assert migrate_legacy_caches(tmp_path / "m").renamed == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_sync_migrate.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.sync.migrate'`

- [ ] **Step 3: Implement**

```python
# src/splitsmith/sync/migrate.py
"""Rename caches cut under the pre-take-spec video id to the current keys.

The take-spec id change left trims, renditions, params and audit audio under
the old path-only id. The desktop read them through ``_legacy_cache_fallback``;
hosted reads only the current key, found no rendition and sent the player to
an original no sync uploads (spec 2026-10-12). Same bytes, new names, only
where the fallback itself would have used the old file (one registration of
the source in the project); recorded in ``auto_sync.json`` so it runs once.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ..match_model import load_match_or_legacy
from ..match_project import MatchProject
from ..trim import web_trim_path
from ..ui.audio import _legacy_cache_fallback, trim_params_path, trimmed_video_path, video_audit_audio_path
from .auto_state import load_auto_prefs, update_auto_prefs

CACHE_SCHEMA = 1


class MigrationReport(BaseModel):
    renamed: list[str] = []
    ambiguous: list[str] = []


def _pairs(new_trim: Path, legacy_trim: Path, new_wav: Path, legacy_wav: Path) -> list[tuple[Path, Path]]:
    pairs = [
        (legacy_trim, new_trim),
        (web_trim_path(legacy_trim), web_trim_path(new_trim)),
        (trim_params_path(legacy_trim), trim_params_path(new_trim)),
        (legacy_wav, new_wav),
    ]
    for peaks in legacy_wav.parent.glob(f"{legacy_wav.stem}.peaks-*.json"):
        pairs.append((peaks, new_wav.with_name(peaks.name.replace(legacy_wav.stem, new_wav.stem, 1))))
    return pairs


def migrate_legacy_caches(match_root: Path) -> MigrationReport:
    report = MigrationReport()
    if load_auto_prefs(match_root).cache_schema >= CACHE_SCHEMA:
        return report
    match, shooter_roots = load_match_or_legacy(match_root)
    for slug in match.shooters:
        root = shooter_roots[slug]
        project = MatchProject.load(root)
        for stage in project.stages:
            for video in stage.videos:
                new_trim = trimmed_video_path(root, stage.stage_number, video, project=project)
                legacy_trim = new_trim.with_name(
                    f"stage{stage.stage_number}_cam_{video.legacy_video_id}_trimmed.mp4"
                )
                if new_trim.exists() or not legacy_trim.exists() or legacy_trim == new_trim:
                    continue
                label = f"{slug} stage {stage.stage_number} {video.path.name}"
                if _legacy_cache_fallback(project, video, new_trim, legacy_trim) is None:
                    report.ambiguous.append(label)
                    continue
                new_wav = video_audit_audio_path(root, stage.stage_number, video, project=project)
                legacy_wav = new_wav.with_name(
                    f"stage{stage.stage_number}_cam_{video.legacy_video_id}_audit.wav"
                )
                for old, new in _pairs(new_trim, legacy_trim, new_wav, legacy_wav):
                    if old.exists() and not new.exists():
                        old.rename(new)
                report.renamed.append(label)
    update_auto_prefs(match_root, lambda p: setattr(p, "cache_schema", CACHE_SCHEMA))
    return report
```

`auto_state.AutoSyncPrefs` gains:

```python
    #: Derived-cache layout this match's files are in (sync/migrate.py).
    cache_schema: int = 0
```

`run_sync`: the first line inside `with timed_phase(timings, timer, "preflight"):` becomes `migrate_legacy_caches(match_root)` (import at the top of `run.py`). A migration error must not abort the sync: wrap it in `try/except OSError` and log it.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_sync_migrate.py tests/test_sync_integration.py -q`
Expected: PASS.

- [ ] **Step 5: Dry-check it on the real matches (read only)**

A throwaway script in `~/.claude-tmp/` that runs the same selection logic without renaming, over every `/Volumes/X9/matches/*`.
Expected: Bofors 18, VADS 12, Jinglebell 4 and Blacksmith Handgun Open 20 stage videos selected; nothing ambiguous unless a single take spans stages (report any). Do not run the real migration here: Task 6 does it through a sync.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/sync/migrate.py src/splitsmith/sync/auto_state.py src/splitsmith/sync/run.py tests/test_sync_migrate.py
/usr/bin/git commit -m "feat(sync): rename legacy-keyed caches once, so hosted finds them"
```

### Task 4: Reconcile files, not flags; push only valid media

**Files:**
- Modify: `src/splitsmith/sync/reconcile.py` (`video_step` ~42, `plan_reconcile` ~99, new `valid_trims_for`)
- Modify: `src/splitsmith/ui/server.py:661` (`_submit_reconcile_steps`)
- Modify: `src/splitsmith/sync/plan.py` (`build_push_plan`, the trimmed loop ~228)
- Test: `tests/test_sync_reconcile.py` (or the existing reconcile test file), `tests/test_sync_plan.py`

**Interfaces:**
- Consumes: `media_check.valid_mp4_cached`, `ui.audio.trimmed_video_path`
- Produces: `def valid_trims_for(match_root: Path, projects: Mapping[str, MatchProject]) -> set[tuple[str, str]]`
- Produces: `video_step(..., trim_valid: bool | None = None)` and `plan_reconcile(..., valid_trims: Collection[tuple[str, str]] | None = None)`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_stage_marked_trimmed_whose_trim_is_gone_gets_a_trim_step(tmp_path: Path) -> None:
    from splitsmith.sync.reconcile import plan_reconcile

    projects, audits = _one_stage_project(processed={"beep": True, "trim": True, "shot_detect": True})
    vid = projects["me"].stages[0].videos[0].video_id
    assert plan_reconcile(projects, audits, {}, valid_trims={("me", vid)}) == []
    steps = plan_reconcile(projects, audits, {}, valid_trims=set())
    assert [s.kind for s in steps] == ["trim"]


def test_without_the_file_facts_the_flag_still_decides(tmp_path: Path) -> None:
    from splitsmith.sync.reconcile import plan_reconcile

    projects, audits = _one_stage_project(processed={"beep": True, "trim": True, "shot_detect": True})
    assert plan_reconcile(projects, audits, {}) == []
```

`_one_stage_project` builds one shooter `me` with one primary video (beep reviewed, stage time 10 s) and returns `(projects, audits)` the way the existing reconcile tests do; reuse their helper if one exists.

In `tests/test_sync_plan.py`:

```python
def test_the_push_leaves_out_a_rendition_that_fails_the_check(tmp_path: Path) -> None:
    from tests.media_bytes import corrupt_moov_mp4, tiny_mp4

    root, trimmed_dir = _match_with_trim(tmp_path)  # existing helper shape: one shooter, one trim
    (trimmed_dir / "stage1_cam_aaa_trimmed.mp4").write_bytes(tiny_mp4())
    (trimmed_dir / "stage1_cam_aaa_web.mp4").write_bytes(corrupt_moov_mp4())
    plan = build_push_plan(root, sync_state=SyncState(), full_media=False)
    keys = [m.remote_key for m in plan.media]
    assert not any(k.endswith("_web.mp4") for k in keys)
```

(If `test_sync_plan.py` has no `_match_with_trim`, build the match the way its existing web-only tests do.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_sync_plan.py -q -k fails_the_check` and the reconcile tests by name.
Expected: FAIL (`unexpected keyword argument 'valid_trims'`; the corrupt rendition is planned).

- [ ] **Step 3: Implement**

`reconcile.py`:

```python
def video_step(stage, video, audit_doc, *, detect_enabled=True, explicit=False, trim_valid: bool | None = None):
    if not video.beep_reviewed or video.beep_time is None:
        return None
    # The file decides when the caller looked: a flag set by a trim that is
    # gone (deleted, never synced down, a migration that could not rename
    # it) must not keep the stage untrimmed forever.
    trimmed = video.processed.get("trim") and trim_valid is not False
    if not trimmed:
        return "trim" if stage.time_seconds > 0 else None
    ...  # unchanged below


def valid_trims_for(match_root: Path, projects: Mapping[str, MatchProject]) -> set[tuple[str, str]]:
    """``(slug, video_id)`` of every video whose current-keyed trim is on
    disk and passes the structural check. Plain stat and parse; no pull."""
    from ..media_check import valid_mp4_cached
    from ..ui.audio import trimmed_video_path

    _, shooter_roots = load_match_or_legacy(match_root)
    out: set[tuple[str, str]] = set()
    for slug, project in projects.items():
        root = shooter_roots.get(slug)
        if root is None:
            continue
        for stage in project.stages:
            for video in stage.videos:
                if valid_mp4_cached(trimmed_video_path(root, stage.stage_number, video, project=project)).ok:
                    out.add((slug, video.video_id))
    return out
```

`plan_reconcile` gains `valid_trims: Collection[tuple[str, str]] | None = None` and passes `trim_valid=None if valid_trims is None else (slug, video.video_id) in valid_trims` to `video_step`.

`server.py` `_submit_reconcile_steps`: pass `valid_trims=valid_trims_for(match_root, projects)`. When a trim step runs for a stage whose flag was true, the trim job sets the flag again on success as it does today; nothing else changes.

`plan.py` `build_push_plan`: in the trimmed loop, `has_web` also requires `valid_mp4_cached(web).ok`, and a candidate trim is skipped when `not valid_mp4_cached(clip_path).ok`. An invalid rendition is therefore neither pushed nor allowed to stand in for its trim.

- [ ] **Step 4: Run the tests and the sync suite**

Run: `uv run pytest -q tests/test_sync_plan.py tests/test_sync_push.py tests/test_sync_integration.py tests/test_auto_sync_service.py` and the reconcile tests.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/sync/reconcile.py src/splitsmith/ui/server.py src/splitsmith/sync/plan.py tests/
/usr/bin/git commit -m "fix(sync): rebuild a trim that is gone whatever its flag says; never push an invalid file"
```

### Task 5: `splitsmith match verify-media`

**Files:**
- Create: `src/splitsmith/sync/verify.py`
- Modify: `src/splitsmith/match_cli.py` (new command after `trims`)
- Test: `tests/test_sync_verify.py`

**Interfaces:**
- Consumes: `media_check.valid_mp4_cached`, `ui.audio.trimmed_video_path`, `trim.web_trim_path`, `trim.rendition_ok`, `sync.state.load_sync_state`, `sync.plan` key helpers (`_remote_key`)
- Produces: `class StageVideoMedia(BaseModel): slug: str; stage_number: int; video: str; state: Literal["playable", "healing", "blocked"]; reason: str | None`
- Produces: `def verify_match_media(match_root: Path) -> list[StageVideoMedia]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sync_verify.py
from __future__ import annotations

from pathlib import Path

from splitsmith.sync.verify import verify_match_media
from tests.media_bytes import corrupt_moov_mp4, tiny_mp4
from tests.test_sync_migrate import _match


def test_says_per_stage_video_whether_hosted_can_play_it(tmp_path: Path) -> None:
    sh, project = _match(tmp_path / "m", stages=[1, 2, 3])
    t = sh / "trimmed"
    t.mkdir()
    v1, v2 = (project.stages[i].videos[0] for i in (0, 1))
    for v, n in ((v1, 1), (v2, 2)):
        (t / f"stage{n}_cam_{v.video_id}_trimmed.mp4").write_bytes(tiny_mp4())
    (t / f"stage1_cam_{v1.video_id}_web.mp4").write_bytes(tiny_mp4())
    (t / f"stage2_cam_{v2.video_id}_web.mp4").write_bytes(corrupt_moov_mp4())
    states = {r.stage_number: (r.state, r.reason) for r in verify_match_media(tmp_path / "m")}
    assert states[1][0] == "healing"  # valid locally, not yet uploaded
    assert states[2] == ("healing", "web rendition invalid: re-cut on the next sync")
    assert states[3][0] == "blocked" and "original" in (states[3][1] or "")
```

(Stage 3 has no trim and its `raw/v.mp4` does not exist, so it is blocked on the missing original. A stage whose rendition is valid and recorded in `sync_state.items` with the same size reads `playable`; add that case once the state helpers are in hand, writing a `SyncState` with the item.)

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -n0 tests/test_sync_verify.py -q`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`verify.py` walks every stage video with a reviewed beep and a stage time:

- trim current-keyed and valid, rendition `rendition_ok`, and its remote key in `sync_state.items` with the local size: `playable`;
- trim and rendition valid, not uploaded (or a different size): `healing`, "valid locally, uploads on the next sync";
- trim valid, rendition missing or invalid: `healing`, "web rendition invalid: re-cut on the next sync" (or "missing");
- trim missing or invalid, original present: `healing`, "trim missing: re-trimmed on the next sync";
- trim missing, original absent: `blocked`, "original not on this disk: <name>";
- beep not reviewed: `blocked`, "beep not confirmed".

`match_cli.py`:

```python
@match_app.command("verify-media")
def verify_media(match_path: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    """Will this match play on splitsmith.app? One line per stage video that
    is not playable, then the counts. Exit 1 when anything is blocked."""
    from .sync.verify import verify_match_media

    rows = verify_match_media(match_path)
    for r in rows:
        if r.state != "playable":
            typer.echo(f"{r.state:8} {r.slug} stage {r.stage_number} {r.video}: {r.reason}")
    counts = {s: sum(r.state == s for r in rows) for s in ("playable", "healing", "blocked")}
    typer.echo(f"{counts['playable']} playable, {counts['healing']} healing, {counts['blocked']} blocked")
    raise typer.Exit(1 if counts["blocked"] else 0)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_sync_verify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/sync/verify.py src/splitsmith/match_cli.py tests/test_sync_verify.py
/usr/bin/git commit -m "feat(sync): match verify-media says whether a match will play on hosted"
```

### Task 6: Heal the user's matches and prove it (operational, after merge and release)

No code. Runs on the user's desktop data once tasks 1 to 5 are released and the desktop runs the new version.

- [ ] **Step 1:** `splitsmith match verify-media /Volumes/X9/matches/<each>` before: record the counts per match (expected: Stockholm 40 and Oden 22 healing on invalid renditions; Bofors, VADS, Jinglebell, Blacksmith healing on legacy keys; Tallmilan 2026 25, Tallmilan 2025 4, Blacksmith 7 healing on missing trims; Tallmilan 2025 2 and Blacksmith 4 blocked).
- [ ] **Step 2:** Let the desktop's sync run on each match (auto-sync, or Sync now). The migration renames, the backfill re-cuts, the reconciler queues the trims; a second sync uploads what the trims produced.
- [ ] **Step 3:** `verify-media` again: everything playable except the blocked stage videos, which match the expected list.
- [ ] **Step 4:** On hosted, open one stage per previously broken match and confirm in production's request log (Railway, `serve`, production) that the stream asks `kind=web` and is answered 307, never `kind=source`. Report the result per match to the user.
