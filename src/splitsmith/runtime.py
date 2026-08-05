"""Process-wide runtime config for artifacts and binaries (issue #130).

One resolver decides where ensemble artifacts live and which
``ffmpeg`` / ``ffprobe`` to invoke. Read everywhere via :func:`runtime`
so no module hardcodes ``"ffmpeg"`` at the orchestrator layer again.

The motivating use case is the (closed-source) desktop shell from
issue #129: a Tauri app bundles its own ffmpeg and ensemble artifacts
and points the embedded engine at them via env vars, without forking.
The same hooks let OSS users A/B-test custom model artifacts:

    SPLITSMITH_ARTIFACTS_DIR=/path/to/experimental splitsmith ui

Resolution priority for paths:

1. Explicit kwargs to :func:`resolve_runtime`.
2. Environment variables (see ``ENV_*`` constants below).
3. Built-in defaults: package data dir for artifacts,
   platform-appropriate cache/config dirs.

Binary resolution adds two more steps before falling back to PATH so
PyInstaller-bundled sidecars find their ffmpeg/ffprobe without env
vars (issue #370):

1. Explicit kwarg.
2. Environment variable.
3. ``sys._MEIPASS`` -- PyInstaller onefile temp dir, set on extracted
   bundle launch only.
4. Directory of ``sys.executable`` -- PyInstaller onedir layout, also
   covers the dev case where ffmpeg is dropped into the venv's ``bin``.
5. ``shutil.which`` on PATH.

Each candidate must be executable; non-executable entries fall through.

The first call has a side effect: it ``setdefault``-s ``HF_HOME`` and
``TORCH_HOME`` into the resolved cache dir so CLAP / PANN downloads
land somewhere a packaged app can clean up, instead of the user's
home dir.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]
"""Injectable ``subprocess.run``. Same hook ``trim.py`` and ``mp4_grid.py`` use."""

ENV_ARTIFACTS_DIR = "SPLITSMITH_ARTIFACTS_DIR"
ENV_FFMPEG = "SPLITSMITH_FFMPEG"
ENV_FFPROBE = "SPLITSMITH_FFPROBE"
ENV_CACHE_DIR = "SPLITSMITH_CACHE_DIR"
ENV_CONFIG_DIR = "SPLITSMITH_CONFIG_DIR"

_PACKAGE_DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Runtime:
    """Resolved locations of all process-wide engine resources.

    Read fields directly, or call :meth:`artifact` for files inside
    :attr:`artifacts_dir` (raises ``FileNotFoundError`` with an
    actionable message when the artifact is missing -- typically means
    a stale ``SPLITSMITH_ARTIFACTS_DIR`` override).
    """

    artifacts_dir: Path
    ffmpeg_binary: str
    ffprobe_binary: str
    cache_dir: Path
    user_config_dir: Path

    def artifact(self, name: str) -> Path:
        path = self.artifacts_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"splitsmith artifact {name!r} not found under "
                f"{self.artifacts_dir} -- check ${ENV_ARTIFACTS_DIR} or "
                "rebuild via scripts/build_ensemble_artifacts.py"
            )
        return path


def _honour_xdg(env_var: str) -> Path | None:
    """Return ``$<env_var>`` if it's set to an absolute path.

    Per the freedesktop XDG basedir spec, ``XDG_*_HOME`` values MUST be
    absolute. Empty / unset / non-absolute fall through to platform
    defaults. A non-absolute value is logged once so the user can see
    why their override didn't take effect.
    """
    raw = os.environ.get(env_var)
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        logger.warning(
            "ignoring %s=%r: XDG basedir values must be absolute paths; falling back to default",
            env_var,
            raw,
        )
        return None
    return path


def _platform_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "splitsmith"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "splitsmith"
        return Path.home() / "AppData" / "Local" / "splitsmith"
    xdg = _honour_xdg("XDG_CACHE_HOME")
    if xdg is not None:
        return xdg / "splitsmith"
    return Path.home() / ".cache" / "splitsmith"


def _platform_user_config_dir() -> Path:
    # Mirror the convention from ``user_config.py`` so both modules
    # agree on where per-user state lives.
    if sys.platform.startswith("linux"):
        xdg = _honour_xdg("XDG_CONFIG_HOME")
        if xdg is not None:
            return xdg / "splitsmith"
        return Path.home() / ".config" / "splitsmith"
    return Path.home() / ".splitsmith"


def _bundle_search_dirs() -> list[Path]:
    """Directories adjacent to the running executable to probe for bundled binaries.

    Returned in priority order:

    * ``sys._MEIPASS`` -- PyInstaller onefile mode extracts datas + binaries
      to a temp dir and points this attr at it.
    * ``Path(sys.executable).parent`` -- PyInstaller onedir layout, plus the
      dev case where ffmpeg/ffprobe live in the venv's ``bin`` next to
      the Python interpreter.
    """
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    exe_dir = Path(sys.executable).parent
    if exe_dir not in dirs:
        dirs.append(exe_dir)
    return dirs


def _executable_in_dir(directory: Path, name: str) -> Path | None:
    """Return ``directory/name`` (with ``.exe`` on Windows) if it's executable."""
    candidates = [directory / name]
    if sys.platform.startswith("win") and not name.lower().endswith(".exe"):
        candidates.append(directory / f"{name}.exe")
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _resolve_binary(explicit: str | None, env_name: str, default: str) -> str:
    """Pick the most specific binary the caller has provided or shipped.

    Order: explicit kwarg > env var > bundled (``_MEIPASS`` / executable
    dir) > the bare name (resolved by the OS via PATH at exec time).
    """
    if explicit:
        return explicit
    env_val = os.environ.get(env_name)
    if env_val:
        return env_val
    for directory in _bundle_search_dirs():
        bundled = _executable_in_dir(directory, default)
        if bundled is not None:
            return str(bundled)
    return default


def _validate_binary(value: str, label: str) -> None:
    """Log (do not raise) when ``value`` doesn't look invocable."""
    # Absolute path: must exist on disk.
    candidate = Path(value)
    if candidate.is_absolute():
        if not candidate.exists():
            logger.warning(
                "%s binary %s does not exist on disk; subprocess calls will fail",
                label,
                value,
            )
        return
    if shutil.which(value) is None:
        logger.warning(
            "%s binary %r not found on PATH; subprocess calls will fail",
            label,
            value,
        )


@lru_cache(maxsize=1)
def resolve_runtime(
    *,
    artifacts_dir: Path | str | None = None,
    ffmpeg_binary: str | None = None,
    ffprobe_binary: str | None = None,
    cache_dir: Path | str | None = None,
    user_config_dir: Path | str | None = None,
) -> Runtime:
    """Build and cache the process-wide :class:`Runtime`.

    Each parameter follows the same priority: explicit kwarg > env var
    > built-in default. Pass kwargs to pin an override programmatically
    (tests, embedded host); leave them ``None`` for normal use.

    The result is cached for the lifetime of the process. Tests can
    reset the cache via :func:`_clear_runtime_cache`.
    """
    art_env = os.environ.get(ENV_ARTIFACTS_DIR)
    art = (
        Path(artifacts_dir) if artifacts_dir is not None else Path(art_env) if art_env else _PACKAGE_DATA_DIR
    )

    cache_env = os.environ.get(ENV_CACHE_DIR)
    cache = (
        Path(cache_dir) if cache_dir is not None else Path(cache_env) if cache_env else _platform_cache_dir()
    )

    cfg_env = os.environ.get(ENV_CONFIG_DIR)
    cfg = (
        Path(user_config_dir)
        if user_config_dir is not None
        else Path(cfg_env) if cfg_env else _platform_user_config_dir()
    )

    ffm = _resolve_binary(ffmpeg_binary, ENV_FFMPEG, "ffmpeg")
    ffp = _resolve_binary(ffprobe_binary, ENV_FFPROBE, "ffprobe")
    _validate_binary(ffm, "ffmpeg")
    _validate_binary(ffp, "ffprobe")

    # Point HuggingFace / Torch caches into our cleanable cache dir so
    # CLAP/PANN downloads don't pollute ``~``. ``setdefault`` so users
    # who already point these elsewhere stay in control.
    os.environ.setdefault("HF_HOME", str(cache / "hf"))
    os.environ.setdefault("TORCH_HOME", str(cache / "torch"))

    return Runtime(
        artifacts_dir=art,
        ffmpeg_binary=ffm,
        ffprobe_binary=ffp,
        cache_dir=cache,
        user_config_dir=cfg,
    )


def runtime() -> Runtime:
    """Return the cached process-wide :class:`Runtime` (resolving on first call)."""
    return resolve_runtime()


def _clear_runtime_cache() -> None:
    """Reset the ``lru_cache`` on :func:`resolve_runtime`. Test-only helper.

    Clears :func:`ffmpeg_capabilities` too: its default key resolves the
    binary through this cache, so leaving it behind would answer for the
    binary the previous resolution picked.
    """
    resolve_runtime.cache_clear()
    ffmpeg_capabilities.cache_clear()


# --- ffmpeg capability probing --------------------------------------------
#
# A version number does not tell you what an ffmpeg can do. ``drawtext``
# is compiled in only when the build was configured with
# ``--enable-libfreetype``, which plenty of distro and static builds omit,
# and there is no version at which that becomes true. So the binary is
# asked directly.


@dataclass(frozen=True)
class FFmpegCapabilities:
    """What one resolved ffmpeg binary can actually do.

    ``version`` is the string ffmpeg prints for itself (``"6.1.1-3ubuntu5"``),
    or ``"unknown"``. It is for the user-facing message only -- nothing
    branches on it, because build flags are not a function of version.

    ``drawtext`` is what the compare grid's running clock needs.
    ``concat_option_keyword`` is whether the concat demuxer understands
    ``option <name> <value>`` list entries, which the splits overlay's
    sprite input needs per entry or every state boundary snaps to a 25fps
    time base.

    ``probed`` is ``False`` when at least one probe could not be answered
    -- the binary would not run, or its output was not in a shape this
    recognises. The unanswered capabilities then read ``True``: a probe
    that failed says nothing about the build, and must never degrade a
    render that would have worked.
    """

    binary: str
    version: str
    drawtext: bool
    concat_option_keyword: bool
    probed: bool = True


#: Bounded so a wedged binary cannot hang the overlay render before a
#: single line is printed. Every probe here is trivial work -- a
#: ``-version`` print, a filter listing, one 32x32 frame, a concat list
#: that never opens a real file -- so this is generous headroom, not a
#: tuned budget.
_PROBE_TIMEOUT_S = 10.0


def _probe(runner: Runner, cmd: list[str]) -> subprocess.CompletedProcess | None:
    """Run one probe command, or ``None`` if it could not be run or answer in time.

    ``None`` joins a timeout to every other "could not be answered" case
    (see :class:`FFmpegCapabilities`), which is what keeps the affected
    capability reading ``True`` rather than ``False`` -- a probe that
    never returns says nothing about the build, and must never degrade a
    render that would have worked. ``subprocess.TimeoutExpired`` is not
    an ``OSError``, so it needs its own arm here rather than falling out
    of this function uncaught into :func:`render_grid_mp4`.
    """
    try:
        return runner(list(cmd), capture_output=True, timeout=_PROBE_TIMEOUT_S)
    except OSError as exc:  # missing binary, not executable, ...
        logger.debug("ffmpeg capability probe %r could not run: %s", cmd, exc)
        return None
    except subprocess.TimeoutExpired as exc:
        logger.debug("ffmpeg capability probe %r timed out: %s", cmd, exc)
        return None


def _probe_text(completed: subprocess.CompletedProcess) -> str:
    """Both streams of a probe, lowercased, decoded defensively.

    Both, because ffmpeg is not consistent about which one it uses:
    ``-h filter=...`` writes its help to stdout but reports an unknown
    filter through ``av_log``. A caller-supplied runner may hand back
    ``str`` or ``bytes``.
    """
    parts: list[str] = []
    for stream in (completed.stdout, completed.stderr):
        if not stream:
            continue
        parts.append(stream.decode(errors="replace") if isinstance(stream, bytes) else str(stream))
    return "\n".join(parts).lower()


def quote_filter_value(value: str) -> str:
    """Quote one ``filter_complex`` option value the way ffmpeg's escaping rules want.

    Inside ``'...'`` every character is literal, so quoting is all that is
    needed for the ``:`` and ``,`` an absolute path can contain. A literal
    ``'`` cannot appear inside the quotes at all -- the quote is closed,
    the character escaped outside it, and the quote reopened.

    Shared, not copied: this probe's drawtext exercise and
    ``compare.mp4_grid``'s own ``drawtext``/``text=`` filters both need
    it, and a diverging copy would mean the probe exercises a string the
    renderer never actually emits. ``mp4_grid`` imports this module (not
    the other way around), so there is no cycle to avoid by duplicating
    it -- ``mp4_grid`` imports this function instead.
    """
    return "'" + value.replace("'", r"'\''") + "'"


def _probe_version(binary: str, runner: Runner) -> str | None:
    completed = _probe(runner, [binary, "-version"])
    if completed is None:
        return None
    match = re.search(r"ffmpeg version (\S+)", _probe_text(completed))
    return match.group(1) if match else None


def _probe_drawtext(binary: str, font_path: Path | None, runner: Runner) -> bool | None:
    """Does this build have a ``drawtext`` that works?

    Two steps, because "the filter is listed" and "the filter works" are
    not the same claim.

    First ``-h filter=drawtext``, which is decisive for the failure this
    exists to catch: a build without ``--enable-libfreetype`` has no
    ``drawtext`` at all and answers ``Unknown filter 'drawtext'``.

    Then, when the caller has a ``font_path`` -- the compare grid
    materializes one before it renders anything -- actually draw with it:
    one 32x32 frame through ``lavfi``, ~50ms, once per run. This step
    catches a listed filter that cannot initialise, which otherwise
    surfaces as every stage failing after the encode has started.

    It does **not** reliably catch a bad ``font_path``. Measured on
    ffmpeg 6.1.1: a nonexistent path (``fontfile='/nope/missing.ttf'``)
    and a 2 KB random-bytes ``.ttf`` both exit ``0`` -- ``drawtext``
    silently falls back to fontconfig's ``Sans`` rather than failing the
    frame. The exercise only turns that into a failure when the host is
    *also* fontless, which is not the case this probe exists for.
    """
    completed = _probe(runner, [binary, "-hide_banner", "-h", "filter=drawtext"])
    if completed is None:
        return None
    text = _probe_text(completed)
    if "unknown filter" in text:
        return False
    if "drawtext" not in text:
        # Not a shape this recognises; say nothing rather than guess.
        return None
    if font_path is None:
        return True
    exercise = _probe(
        runner,
        [
            binary,
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:r=25",
            "-vf",
            f"drawtext=fontfile={quote_filter_value(str(font_path))}:text=0:fontsize=12",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
    )
    if exercise is None:
        return None
    return exercise.returncode == 0


def _probe_concat_option(binary: str, runner: Runner) -> bool | None:
    """Does the concat demuxer understand ``option <name> <value>``?

    The list names a file that deliberately does not exist. The demuxer
    parses the whole list before it opens anything (verified on ffmpeg
    6.1.1), so an unsupported keyword is reported as
    ``Line 2: unknown keyword 'option'`` and the missing file never gets
    that far. On a build that does support it the run still fails, on the
    missing file -- which is why the signal is that one message and not
    the exit code.
    """
    with tempfile.TemporaryDirectory(prefix="splitsmith-ffmpeg-probe-") as tmp:
        list_path = Path(tmp) / "probe.txt"
        absent = Path(tmp) / "splitsmith-capability-probe.png"
        list_path.write_text(f"file '{absent}'\noption framerate 30/1\n", encoding="utf-8")
        completed = _probe(
            runner,
            [
                binary,
                "-hide_banner",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-f",
                "null",
                "-",
            ],
        )
    if completed is None:
        return None
    return "unknown keyword" not in _probe_text(completed)


@lru_cache(maxsize=8)
def ffmpeg_capabilities(
    binary: str | None = None,
    *,
    font_path: Path | None = None,
    runner: Runner = subprocess.run,
) -> FFmpegCapabilities:
    """Probe ``binary`` (default: the resolved runtime's ffmpeg) and cache it.

    Cached for the lifetime of the process, keyed by every argument --
    including ``runner``, so a test's fake never answers for a later
    call's real one. Reset via :func:`_clear_ffmpeg_capabilities_cache`
    (or :func:`_clear_runtime_cache`, which also clears this).

    ``runner`` exists so unit tests never shell out; ``font_path``, when
    given, upgrades the ``drawtext`` probe from "is it listed" to "does
    it draw with this font" (see :func:`_probe_drawtext`).
    """
    resolved = binary or runtime().ffmpeg_binary
    version = _probe_version(resolved, runner)
    drawtext = _probe_drawtext(resolved, font_path, runner)
    concat_option = _probe_concat_option(resolved, runner)
    return FFmpegCapabilities(
        binary=resolved,
        version=version or "unknown",
        drawtext=True if drawtext is None else drawtext,
        concat_option_keyword=True if concat_option is None else concat_option,
        probed=version is not None and drawtext is not None and concat_option is not None,
    )


def _clear_ffmpeg_capabilities_cache() -> None:
    """Reset the ``lru_cache`` on :func:`ffmpeg_capabilities`. Test-only helper."""
    ffmpeg_capabilities.cache_clear()
