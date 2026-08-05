"""``runtime.ffmpeg_capabilities``: asking a binary what it can do.

A version number does not answer the question this module exists for.
``drawtext`` is compiled in only with ``--enable-libfreetype`` and plenty
of distro and static builds omit it, so 7.0.2 on one host draws a clock
and 7.0.2 on another does not. Hence probing the resolved binary.

The awkward part is that there is no ffmpeg on this machine (or in CI)
*without* ``drawtext``, so the interesting half of the behaviour can only
be covered with a fake runner. That leaves one thing a fake cannot prove:
that the strings the probe keys on are the strings a real ffmpeg emits.
The integration tests at the bottom pin exactly that, by asking a real
ffmpeg for a filter and a concat keyword that genuinely do not exist.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from splitsmith import runtime as runtime_mod
from splitsmith.compare import mp4_grid as mp4_grid_mod
from splitsmith.runtime import (
    _clear_ffmpeg_capabilities_cache,
    ffmpeg_capabilities,
    quote_filter_value,
)
from tests.conftest import fake_ffmpeg_probe

FFMPEG = shutil.which("ffmpeg")

BUNDLED_FONT = Path(runtime_mod.__file__).parent / "data" / "fonts" / "JetBrainsMono-Bold.ttf"


@pytest.fixture(autouse=True)
def _clear_cache():
    _clear_ffmpeg_capabilities_cache()
    yield
    _clear_ffmpeg_capabilities_cache()


def test_a_capable_ffmpeg_reports_every_capability():
    caps = ffmpeg_capabilities("/bin/ffmpeg", runner=fake_ffmpeg_probe())

    assert caps.binary == "/bin/ffmpeg"
    assert caps.version == "6.1.1-3ubuntu5"
    assert caps.drawtext is True
    assert caps.concat_option_keyword is True
    assert caps.probed is True


def test_a_build_without_libfreetype_reports_no_drawtext():
    caps = ffmpeg_capabilities("/bin/ffmpeg", runner=fake_ffmpeg_probe(drawtext=False))

    assert caps.drawtext is False
    # The other capability is independent -- an ffmpeg can lack one and
    # have the other, and conflating them would refuse a render that only
    # needed to lose its clock.
    assert caps.concat_option_keyword is True


def test_an_old_concat_demuxer_reports_no_option_keyword():
    caps = ffmpeg_capabilities("/bin/ffmpeg", runner=fake_ffmpeg_probe(concat_option=False))

    assert caps.concat_option_keyword is False
    assert caps.drawtext is True


def test_a_listed_drawtext_that_cannot_draw_is_reported_as_unavailable(tmp_path: Path):
    """ "Listed" and "works" are different claims, so the probe draws once.

    A build can carry the filter and still fail on the font file this
    render would hand it. Without the exercise step that surfaces as
    every stage failing after the encode started.
    """
    calls: list[list[str]] = []

    def runner(cmd, **_kwargs):
        argv = [str(c) for c in cmd]
        calls.append(argv)
        if "-version" in argv:
            return subprocess.CompletedProcess(argv, 0, b"ffmpeg version 7.0.2 x\n", b"")
        if "filter=drawtext" in argv:
            return subprocess.CompletedProcess(argv, 0, b"Filter drawtext\n", b"")
        if "drawtext=" in " ".join(argv):
            return subprocess.CompletedProcess(argv, 1, b"", b"Could not load font\n")
        return subprocess.CompletedProcess(argv, 183, b"", b"Impossible to open\n")

    font = tmp_path / "broken.ttf"
    font.write_bytes(b"not a font")
    caps = ffmpeg_capabilities("/bin/ffmpeg", font_path=font, runner=runner)

    assert caps.drawtext is False
    assert any("drawtext=" in " ".join(argv) for argv in calls), calls


def test_without_a_font_the_probe_stops_at_the_listing(tmp_path: Path):
    """No font to draw with means no exercise render, not a false negative."""
    calls: list[list[str]] = []

    def runner(cmd, **_kwargs):
        argv = [str(c) for c in cmd]
        calls.append(argv)
        return fake_ffmpeg_probe()(cmd, **_kwargs)

    caps = ffmpeg_capabilities("/bin/ffmpeg", runner=runner)

    assert caps.drawtext is True
    assert not any("-vf" in argv for argv in calls), calls


def test_a_font_path_with_a_colon_is_quoted_into_the_exercise(tmp_path: Path):
    """``:`` separates filter options, so an unquoted path splits the filter.

    A user whose font lives under a directory with a colon in it would
    otherwise get a probe that always fails and a clock silently dropped
    on a perfectly capable ffmpeg.
    """
    seen: list[str] = []

    def runner(cmd, **kwargs):
        argv = [str(c) for c in cmd]
        if "-vf" in argv:
            seen.append(argv[argv.index("-vf") + 1])
        return fake_ffmpeg_probe()(cmd, **kwargs)

    weird = tmp_path / "a:b" / "font.ttf"
    weird.parent.mkdir()
    weird.write_bytes(b"x")
    ffmpeg_capabilities("/bin/ffmpeg", font_path=weird, runner=runner)

    assert seen, "the exercise render never ran"
    assert f"fontfile='{weird}'" in seen[0], seen


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (":", "':'"),
        (",", "','"),
        ("[", "'['"),
        ("]", "']'"),
        ("'", r"''\'''"),
        ("\\", "'\\'"),
        (" ", "' '"),
        ("a:b,c[d]e'f\\g h", r"'a:b,c[d]e'\''f\g h'"),
    ],
)
def test_quote_filter_value_matches_ffmpegs_escaping_rule(value: str, expected: str) -> None:
    """The quoting table this probe and the renderer both depend on.

    ``mp4_grid``'s ``drawtext``/``text=`` filters and this module's
    drawtext exercise used to carry two hand-written copies of this
    rule. A drift between them would mean the probe exercises a string
    the renderer never actually emits, so the whole probe would stop
    being a proxy for what the render does. They are one function now
    (see ``test_mp4_grid_imports_the_one_quote_filter_value``); this
    pins its behaviour on the awkward characters a filter option value
    can carry: ``:`` and ``,`` separate options, ``[``/``]`` name pads,
    ``'`` has to be escaped without leaving the quotes, ``\\`` and a
    literal space must pass through untouched.
    """
    assert quote_filter_value(value) == expected


def test_mp4_grid_imports_the_one_quote_filter_value() -> None:
    """``mp4_grid`` uses this module's quoting rule, not a copy of it.

    Asserting object identity is the strongest form of "these agree":
    there is exactly one function for the two callers to disagree
    about, so they cannot drift apart by editing only one of them.
    """
    assert mp4_grid_mod.quote_filter_value is quote_filter_value


def test_a_binary_that_will_not_run_never_degrades_anything():
    """A probe that could not run says nothing about the build.

    Reporting "no drawtext" here would drop the clock on every host where
    the probe itself is broken. The render's own missing-binary error is
    the right place for that to surface.
    """

    def runner(cmd, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(cmd[0]))

    caps = ffmpeg_capabilities("/nope/ffmpeg", runner=runner)

    assert caps.drawtext is True
    assert caps.concat_option_keyword is True
    assert caps.version == "unknown"
    assert caps.probed is False


def test_a_wedged_binary_never_degrades_anything_either():
    """``subprocess.TimeoutExpired`` joins the same "unanswerable" bucket.

    ``TimeoutExpired`` is not an ``OSError``, so a probe that hangs (a
    genuinely wedged binary, not just a missing one) needs its own catch
    or it escapes ``_probe`` uncaught into ``render_grid_mp4`` and takes
    the whole overlay render down before a single stage runs. The rule
    is the same as a missing binary: an unanswerable probe must read as
    "capability available", never "capability missing" -- a false
    "missing" would silently strip the clock for everyone on a host
    where the probe merely timed out once.
    """

    def runner(cmd, **kwargs):
        assert kwargs.get("timeout"), "the probe must be bounded"
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    caps = ffmpeg_capabilities("/wedged/ffmpeg", runner=runner)

    assert caps.drawtext is True
    assert caps.concat_option_keyword is True
    assert caps.version == "unknown"
    assert caps.probed is False


def test_unrecognised_help_output_is_not_read_as_a_missing_filter():
    """A shape this does not understand must not be read as an answer."""

    def runner(cmd, **_kwargs):
        argv = [str(c) for c in cmd]
        if "-version" in argv:
            return subprocess.CompletedProcess(argv, 0, b"ffmpeg version 9.9 x\n", b"")
        if "filter=drawtext" in argv:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(argv, 183, b"", b"Impossible to open\n")

    caps = ffmpeg_capabilities("/bin/ffmpeg", runner=runner)

    assert caps.drawtext is True
    assert caps.probed is False


def test_the_probe_runs_once_per_binary_not_once_per_caller():
    """~150ms of subprocesses is cheap once and silly on every stage."""
    calls: list[list[str]] = []
    capable = fake_ffmpeg_probe()

    def runner(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return capable(cmd, **kwargs)

    first = ffmpeg_capabilities("/bin/ffmpeg", runner=runner)
    count = len(calls)
    second = ffmpeg_capabilities("/bin/ffmpeg", runner=runner)

    assert count > 0
    assert len(calls) == count
    assert first is second

    _clear_ffmpeg_capabilities_cache()
    ffmpeg_capabilities("/bin/ffmpeg", runner=runner)
    assert len(calls) == 2 * count


def test_clearing_the_runtime_cache_clears_the_capability_cache():
    """The default key resolves its binary through the runtime cache.

    Leaving the capabilities behind would answer for whichever ffmpeg the
    previous resolution picked, which is exactly the bug a test that
    repoints ``SPLITSMITH_FFMPEG`` is trying to avoid.
    """
    calls: list[list[str]] = []
    capable = fake_ffmpeg_probe()

    def runner(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return capable(cmd, **kwargs)

    ffmpeg_capabilities("/bin/ffmpeg", runner=runner)
    count = len(calls)
    runtime_mod._clear_runtime_cache()
    ffmpeg_capabilities("/bin/ffmpeg", runner=runner)

    assert len(calls) == 2 * count


# --- against a real ffmpeg -------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="needs a real ffmpeg on PATH")
def test_a_real_ffmpeg_answers_every_probe():
    caps = ffmpeg_capabilities(FFMPEG, font_path=BUNDLED_FONT)

    assert caps.probed is True, caps
    assert caps.version != "unknown"
    # This host's ffmpeg is the one the rest of the suite renders with, so
    # both of these must be true or the overlay tests are testing nothing.
    assert caps.drawtext is True
    assert caps.concat_option_keyword is True


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="needs a real ffmpeg on PATH")
def test_the_missing_filter_message_is_still_the_one_the_probe_keys_on():
    """The fake's ``Unknown filter`` string, checked against reality.

    No ffmpeg here lacks ``drawtext``, so ask for a filter that no ffmpeg
    will ever have. If this message ever changes, every unit test above
    keeps passing while the real degradation stops being detected.
    """
    done = subprocess.run(
        [FFMPEG, "-hide_banner", "-h", "filter=splitsmith_not_a_filter"],
        capture_output=True,
    )
    text = (done.stdout + done.stderr).decode(errors="replace").lower()

    assert "unknown filter" in text, text[:500]


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="needs a real ffmpeg on PATH")
def test_the_unknown_keyword_message_is_still_the_one_the_probe_keys_on(tmp_path: Path):
    """Same for the concat half, with a keyword no ffmpeg supports.

    Also pins the ordering the probe depends on: the demuxer parses the
    whole list before it opens the first file, so the keyword complaint
    arrives even though the file named on line 1 does not exist.
    """
    list_path = tmp_path / "probe.txt"
    list_path.write_text(
        f"file '{tmp_path / 'absent.png'}'\nsplitsmith_not_a_keyword framerate 30/1\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        [
            FFMPEG,
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
        capture_output=True,
    )
    text = (done.stdout + done.stderr).decode(errors="replace").lower()

    assert "unknown keyword" in text, text[:500]
    # And the supported spelling does *not* produce it, which is the whole
    # discriminator -- both runs fail, only one fails on the keyword.
    supported = tmp_path / "supported.txt"
    supported.write_text(f"file '{tmp_path / 'absent.png'}'\noption framerate 30/1\n", encoding="utf-8")
    done_ok = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(supported),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
    )
    ok_text = (done_ok.stdout + done_ok.stderr).decode(errors="replace").lower()
    assert "unknown keyword" not in ok_text, ok_text[:500]
