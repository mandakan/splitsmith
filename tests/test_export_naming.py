"""One function names a stage's export files, so readers cannot miss them.

``stage<N>_<slug>_trimmed.mp4`` is written in one place and looked for in
six others -- the CLI, the SPA's export endpoints, ``match_project``, the
MCP export tools, and the compare grid's project loader. Every one of
them used to build that string by hand off a private ``_slugify``, and
there were three hand-copied ``_slugify`` implementations to choose
from, identical apart from the fallback they return for a name with no
alphanumerics: ``"stage"`` in ``cli`` and ``ui.exports``, ``"match"`` in
``ui.match_exports``.

Two readers picked the ``"match"`` one. For any stage whose name
slugifies to nothing, they looked for a file the exporter never wrote
and reported no trim -- which in the compare grid is a silent black
filler tile, not an error.
"""

from __future__ import annotations

import pytest

from splitsmith import cli, export_naming
from splitsmith import match_project as project
from splitsmith.compare import project_loader
from splitsmith.match_project import MatchProject
from splitsmith.mcp import export_tools
from splitsmith.ui import exports as exports_mod
from splitsmith.ui import server

#: Names whose slug is empty, so the fallback decides the filename. The
#: ordinary case agreed all along; only these ever diverged.
DEGENERATE = ["", "   ", "!!!", "---", "### @@@", "()"]

ORDINARY = ["Stage 1 -- H1", "El Presidente", "Långvägen", "ALL CAPS"]


@pytest.mark.parametrize("stage_name", DEGENERATE + ORDINARY)
def test_the_compare_grid_looks_where_the_exporter_writes(stage_name: str, tmp_path) -> None:
    """The bug, stated as the two real paths that have to match.

    ``ui.exports.export_stage`` writes the trim; the grid finds it
    through ``project_loader.trim_path_for_stage``. This compares the
    paths those two produce rather than the helper they share, so it
    still fails if one of them goes back to building the string by hand.
    """
    shooter = MatchProject.init(tmp_path, name="naming")
    looked_for = project_loader.trim_path_for_stage(shooter, tmp_path, 1, stage_name)
    written = shooter.exports_path(tmp_path) / f"{export_naming.stage_file_base(1, stage_name)}_trimmed.mp4"

    assert looked_for == written


def test_every_stage_filename_comes_from_the_one_function() -> None:
    """Identity, because a second copy is how this happened the first time.

    There were three hand-copied ``_slugify`` bodies to import; the two
    readers that picked the wrong one had no way to notice. With one
    function there is nothing left to choose between.
    """
    users = (cli, exports_mod, project, server, export_tools, project_loader)

    for module in users:
        assert (
            module.stage_file_base is export_naming.stage_file_base
        ), f"{module.__name__} does not use the one naming function"


def test_a_stage_with_no_nameable_characters_still_gets_a_stable_name() -> None:
    """The fallback is ``stage``, matching what is already on disk.

    Picking ``match`` here would rename every such file and orphan the
    exports users already have.
    """
    assert export_naming.stage_file_base(3, "!!!") == "stage3_stage"


def test_an_ordinary_name_is_unchanged_by_the_consolidation() -> None:
    """The regression that would matter most: renaming existing exports."""
    assert export_naming.stage_file_base(1, "Stage 1 -- H1") == "stage1_stage-1-h1"
    assert export_naming.slugify("All Symbols!@#", fallback="stage") == "all-symbols"
    assert export_naming.stage_file_base(12, "El Presidente") == "stage12_el-presidente"


def test_the_match_filename_keeps_its_own_fallback() -> None:
    """``match`` is right for a project name and wrong for a stage name.

    The two fallbacks are not a mistake to be unified away -- an unnamed
    *project* should not produce ``stage-match.fcpxml``. Keeping them
    distinct is why the slug helper takes the fallback explicitly rather
    than three modules each hard-coding one.
    """
    assert export_naming.slugify("", fallback="match") == "match"
    assert export_naming.slugify("", fallback="stage") == "stage"
    assert export_naming.slugify("Bromma 2026", fallback="match") == "bromma-2026"


def test_accents_are_not_silently_normalised() -> None:
    """``match_model.slugify_filename`` strips diacritics; this must not.

    They are different functions on purpose and merging them would
    rename existing files: ``Långvägen`` is ``l-ngv-gen`` on disk today,
    not ``langvagen``.
    """
    assert export_naming.slugify("Långvägen", fallback="stage") == "l-ngv-gen"
