"""Shared pytest fixtures."""

import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith.compare.project_loader import trim_path_for_video
from splitsmith.match_model import Match, MatchStageDefinition
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo

# Re-export hosted-mode fixtures so pytest auto-discovers them without
# needing per-test-file imports (which trigger ruff F811 redefinition
# warnings when the fixture name also appears as a function parameter).
from tests.hosted_helpers import hosted_app, hosted_env  # noqa: F401
from tests.synthetic_media import build_synthetic_video, ffmpeg_available

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# rich detects GITHUB_ACTIONS and renders CliRunner ``--help`` output with
# ANSI colour/style escapes interleaved into the text -- so a literal
# substring check like ``"--format" in result.output`` can fail on CI while
# passing locally, even though the flag is genuinely documented in both. Any
# test asserting on rendered CLI help text should strip escapes first.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so CLI help assertions are colour-independent."""
    return _ANSI_ESCAPE_RE.sub("", text)


#: Exactly what ffmpeg 6.1.1 answers when asked for a filter it does not have.
#: The whole ``drawtext``-missing story is built on this string, and there is
#: no ffmpeg on this machine that lacks ``drawtext`` -- so the one place the
#: fake could drift from reality is pinned by an integration test
#: (``test_runtime_ffmpeg_capabilities.py``) that asks a real ffmpeg for a
#: filter that genuinely does not exist.
UNKNOWN_FILTER_STDERR = "Unknown filter 'drawtext'.\n"

#: And what it answers for a concat-list keyword it does not know. Same
#: reasoning: the local ffmpeg supports ``option``, so the shape of the
#: refusal is pinned against a keyword no ffmpeg will ever support.
UNKNOWN_KEYWORD_STDERR = (
    "[concat @ 0x0] Line 2: unknown keyword 'option'\n"
    "[in#0 @ 0x0] Error opening input: Invalid data found when processing input\n"
)


def fake_ffmpeg_probe(
    *,
    drawtext: bool = True,
    concat_option: bool = True,
    version: str = "6.1.1-3ubuntu5",
) -> Callable[..., subprocess.CompletedProcess]:
    """A ``probe_runner`` for :func:`splitsmith.runtime.ffmpeg_capabilities`.

    Answers the three probe commands the way a real ffmpeg with the
    requested build flags would, so a unit test can render against an
    ffmpeg that does not exist on any machine here -- which is the only
    way the ``--enable-libfreetype``-less host gets covered at all --
    without shelling out.
    """

    def runner(cmd, **_kwargs: object) -> subprocess.CompletedProcess:
        argv = [str(part) for part in cmd]
        joined = " ".join(argv)
        if "-version" in argv:
            return subprocess.CompletedProcess(
                argv, 0, f"ffmpeg version {version} Copyright (c) 2000-2023\n".encode(), b""
            )
        if "filter=drawtext" in argv:
            if not drawtext:
                return subprocess.CompletedProcess(argv, 0, b"", UNKNOWN_FILTER_STDERR.encode())
            return subprocess.CompletedProcess(
                argv,
                0,
                b"Filter drawtext\n  Draw text on top of video frames using libfreetype.\n",
                b"",
            )
        if "drawtext=" in joined:  # the one-frame exercise render
            return subprocess.CompletedProcess(argv, 0 if drawtext else 1, b"", b"")
        if "concat" in argv:
            if not concat_option:
                return subprocess.CompletedProcess(argv, 183, b"", UNKNOWN_KEYWORD_STDERR.encode())
            # Supported: the run still fails, on the file the probe list
            # deliberately names and never creates.
            return subprocess.CompletedProcess(
                argv, 183, b"", b"[concat @ 0x0] Impossible to open 'splitsmith-capability-probe.png'\n"
            )
        raise AssertionError(f"unexpected ffmpeg probe command: {argv}")

    return runner


_MATCH_TRIMS_STAGE_DEFS: list[tuple[int, str]] = [
    (1, "Egg Grab"),
    (2, "Tower"),
    (3, "Long Range"),
]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def synthetic_source_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real H.264 + AAC MP4, encoded once per session by ffmpeg.

    See ``tests/synthetic_media`` for why the integration tests build
    their own media rather than depending on the gitignored 4K
    ``stage_sample.mp4``. Skips when ffmpeg is absent -- and under
    ``SPLITSMITH_REQUIRE_INTEGRATION`` that skip is escalated to a
    failure by the hooks below, so CI cannot quietly lose it.
    """
    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not available")
    return build_synthetic_video(tmp_path_factory.mktemp("synthetic-media") / "source.mp4")


# --- integration-suite skip gate (#670) -------------------------------------
#
# A skipped integration test reports as success. That is how the
# compare-grid renderer shipped six defects past a green suite: the only
# tests that render a file and measure it never ran in CI, because
# ffmpeg was not installed and the video fixture is gitignored.
#
# Installing ffmpeg fixes half of it. The other half is that nothing
# would have noticed if it silently stopped working -- a skip and a pass
# are the same colour. So when ``SPLITSMITH_REQUIRE_INTEGRATION`` is
# set (CI does), the environment is asserting it can run integration
# tests, and any integration test that skips anyway is a failure.

_REQUIRE_INTEGRATION_ENV = "SPLITSMITH_REQUIRE_INTEGRATION"
_FALSEY = frozenset({"", "0", "false", "no", "off"})

# nodeid -> reason, for every integration test that skipped this session.
_skipped_integration: dict[str, str] = {}
_integration_selected = 0


def _require_integration() -> bool:
    return os.environ.get(_REQUIRE_INTEGRATION_ENV, "").strip().lower() not in _FALSEY


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    # A skip's longrepr is (path, lineno, "Skipped: <reason>").
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr)


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    global _integration_selected
    _integration_selected = sum(1 for item in items if "integration" in item.keywords)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if not _require_integration():
        return
    # ``wasxfail`` rides on skipped reports for xfail; those are a
    # deliberate expectation, not a silently-missing test.
    if not report.skipped or hasattr(report, "wasxfail"):
        return
    if "integration" not in report.keywords:
        return
    _skipped_integration.setdefault(report.nodeid, _skip_reason(report))


def _integration_gate_failures() -> list[str]:
    """Reasons the integration gate should fail the session, if any."""
    if not _require_integration():
        return []
    problems: list[str] = []
    if _integration_selected == 0:
        problems.append(
            f"{_REQUIRE_INTEGRATION_ENV} is set but no test carrying the "
            "'integration' marker was selected -- the suite this gate "
            "exists to protect did not run at all."
        )
    for nodeid, reason in sorted(_skipped_integration.items()):
        problems.append(f"{nodeid} SKIPPED: {reason}")
    return problems


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    if not _require_integration():
        return
    problems = _integration_gate_failures()
    ran = _integration_selected - len(_skipped_integration)
    if problems:
        terminalreporter.section("integration gate FAILED", sep="=", red=True, bold=True)
        for line in problems:
            terminalreporter.write_line(line, red=True)
        terminalreporter.write_line(
            f"\n{_REQUIRE_INTEGRATION_ENV} promises this environment can run the "
            "integration suite. Install the missing tool, or provide the missing "
            "media -- do not re-add the skip."
        )
    else:
        terminalreporter.section("integration gate", sep="=")
        terminalreporter.write_line(f"{ran} integration test(s) ran, 0 skipped")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _integration_gate_failures():
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def submit_fn(backend, *, kind: str, fn, **kwargs):
    """Test shim bridging the pre-gamma ``submit(fn=callable)`` API to the
    ``kind`` + body-registry dispatch.

    Registers ``fn`` as the body for ``kind`` on ``backend.bodies`` then
    returns the ``backend.submit(kind=...)`` coroutine, so existing tests
    can keep writing ``asyncio.run(submit_fn(backend, kind=..., fn=...))``.
    The body adapter swallows any ``args`` the dispatch would pass, since
    these test callables take only the handle.
    """
    backend.bodies.register(kind, lambda handle, **_args: fn(handle))
    return backend.submit(kind=kind, **kwargs)


def scaffold_match(
    tmp_path: Path,
    *,
    name: str = "Test Match",
    shooter_slug: str = "me",
    shooter_name: str = "Me",
    subdir: str = "match",
) -> tuple[Path, Path]:
    """Create a minimal Match folder + one shooter at ``tmp_path/subdir``.

    Returns ``(match_root, shooter_root)``. Tier 1 step 3 of doc 10
    retired the legacy single-shooter layout, so tests that used to
    ``MatchProject.init(tmp_path / "match", ...)`` and bind it
    directly must scaffold a Match folder instead. The default
    shooter slug ``"me"`` matches what the retired ``legacy_slug``
    helper produced for unnamed single-shooter projects, keeping
    migrated URLs ergonomic.

    Callers that don't care about the shooter slot can ignore the
    returned ``shooter_root``; callers that previously wrote per-
    shooter ``project.json`` (stages, audit, etc) at the match root
    should now write at ``shooter_root`` instead.
    """
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject

    root = tmp_path / subdir
    match = match_model.Match.init(root, name=name)
    match.add_shooter(root, match_model.Shooter(slug=shooter_slug, name=shooter_name))
    shooter_root = match_model.Match.shooter_root(root, shooter_slug)
    MatchProject.init(shooter_root, name=name)
    return root, shooter_root


def bound_match_id(app) -> str:
    """Read the registered match id from a test app's state.

    Post Tier 1 step 4 of doc 10, the server has no "bound" project --
    matches are registered in :attr:`AppState.matches` and addressed
    by URL. Tests typically scaffold a single Match folder; this
    helper returns that match's id so they can construct
    ``/api/matches/{match_id}/...`` URLs.
    """
    ids = app.state.splitsmith_state.matches.known_ids()
    assert len(ids) == 1, f"expected exactly one match registered, got {len(ids)}: {ids}"
    return ids[0]


def _video(
    shooter_root: Path,
    rel: str,
    *,
    role: str = "primary",
    beep_time: float | None = 5.0,
    camera_mount: str | None = None,
) -> StageVideo:
    """Create the source blob on disk and return the StageVideo pointing at it.

    Real bytes (not just a model) so ``match_trims``'s reachability check sees
    a file where the project says one is. Shared by ``two_shooter_match`` and
    any test that needs to add a video after the fixture has been built.
    """
    source = shooter_root / rel
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")
    return StageVideo(
        path=Path(rel),
        role=role,  # type: ignore[arg-type]
        beep_time=beep_time,
        beep_source="auto" if beep_time is not None else None,
        camera_mount=camera_mount,
        match_timestamp=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def two_shooter_match(tmp_path: Path) -> Path:
    """A two-shooter match covering every ``match_trims`` classification branch.

    anders:  1 helmet primary w/ beep + time (eligible)
             2 primary with no beep (+ an unbeeped chest secondary)
             3 skipped
    mathias: 1 beep + time + a trim already in exports/
             2 beep + time, no trim, plus one chest secondary w/ beep
             3 beep but time_seconds=0.0, plus two unmounted secondaries
               (so ``camera="secondary"`` is ambiguous on this stage)
    """
    match_root = tmp_path / "match"
    match = Match.init(match_root, name="Bromma Classifier")
    match.stages = [
        MatchStageDefinition(stage_number=n, stage_name=name) for n, name in _MATCH_TRIMS_STAGE_DEFS
    ]
    match.shooters = ["anders", "mathias"]
    match.save(match_root)

    anders = Match.shooter_root(match_root, "anders")
    project = MatchProject.init(anders, name="Bromma Classifier")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Egg Grab",
            time_seconds=11.0,
            videos=[_video(anders, "raw/a1.mov", camera_mount="helmet")],
        ),
        StageEntry(
            stage_number=2,
            stage_name="Tower",
            time_seconds=14.0,
            videos=[
                _video(anders, "raw/a2.mov", beep_time=None),
                _video(
                    anders,
                    "raw/a2_chest.mov",
                    role="secondary",
                    beep_time=None,
                    camera_mount="chest",
                ),
            ],
        ),
        StageEntry(stage_number=3, stage_name="Long Range", time_seconds=13.0, skipped=True),
    ]
    project.save(anders)

    mathias = Match.shooter_root(match_root, "mathias")
    project = MatchProject.init(mathias, name="Bromma Classifier")
    project.stages = [
        StageEntry(
            stage_number=1,
            stage_name="Egg Grab",
            time_seconds=12.0,
            videos=[_video(mathias, "raw/m1.mov")],
        ),
        StageEntry(
            stage_number=2,
            stage_name="Tower",
            time_seconds=13.0,
            videos=[
                _video(mathias, "raw/m2.mov"),
                _video(mathias, "raw/m2_chest.mov", role="secondary", beep_time=4.0, camera_mount="chest"),
            ],
        ),
        StageEntry(
            stage_number=3,
            stage_name="Long Range",
            time_seconds=0.0,
            videos=[
                _video(mathias, "raw/m3.mov"),
                _video(mathias, "raw/m3_left.mov", role="secondary", beep_time=4.0),
                _video(mathias, "raw/m3_right.mov", role="secondary", beep_time=4.0),
            ],
        ),
    ]
    project.save(mathias)

    exports = project.exports_path(mathias)
    exports.mkdir(parents=True, exist_ok=True)
    # Ask the public helper where the exporter would write this trim rather
    # than spelling the filename with the private ``_slugify`` (#620): the
    # fixture then follows any rename of the naming convention instead of
    # silently seeding a file nothing looks for. Reload first so the stage's
    # videos carry their stamped ``video_id``.
    stamped = MatchProject.load(mathias)
    stage1 = stamped.stage(1)
    trim_path_for_video(stamped, mathias, 1, stage1.stage_name, stage1.primary()).write_bytes(b"old trim")

    return match_root


@pytest.fixture
def empty_match(tmp_path: Path) -> Path:
    """A one-shooter, one-stage match where the sole stage has no beep.

    Every entry in its plan is ineligible -- distinct from a match with no
    shooters or stages at all (a genuine no-op). Used to assert that a run
    finding real work but writing zero trims is a failure, not a no-op.
    """
    match_root = tmp_path / "empty_match"
    match = Match.init(match_root, name="Empty Match")
    match.stages = [MatchStageDefinition(stage_number=1, stage_name="Only Stage")]
    match.shooters = ["solo"]
    match.save(match_root)

    shooter_root = Match.shooter_root(match_root, "solo")
    project = MatchProject.init(shooter_root, name="Empty Match")
    project.stages = [
        StageEntry(stage_number=1, stage_name="Only Stage", time_seconds=10.0, videos=[]),
    ]
    project.save(shooter_root)

    return match_root


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Redirect ``~/.splitsmith/`` to a per-test tmp dir for every test.

    Without this, ``create_app`` (and anything else that calls
    ``user_config.record_project_open`` / writes scoreboard identity)
    persists test-only entries into the developer's real
    ``~/.splitsmith/projects.json``. The 50-entry cap then evicts the
    user's actual matches off the back of the list, which surfaces in
    the picker as "only `Beep Match` and `x`, no real projects".

    Tests that need to inspect the on-disk projects.json can read the
    returned path; the existing per-test ``_user_config_home`` fixture
    in ``test_ui_server.py`` continues to work since it overrides the
    same env var with a deterministic name.
    """
    import os

    home = tmp_path_factory.mktemp("user-config")
    prev = os.environ.get("SPLITSMITH_HOME")
    os.environ["SPLITSMITH_HOME"] = str(home)
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("SPLITSMITH_HOME", None)
        else:
            os.environ["SPLITSMITH_HOME"] = prev
