"""Every script that reads source_video offers the same opt-out.

Four scripts besides the artifact build resolve a fixture's source
video. If one of them keeps skipping silently, the corpus can still
shrink under it -- and the inconsistency is exactly the kind of thing
that gets rediscovered a year later.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = [
    "regression_voter_e",
    "build_sweep_signals",
    "probe_visual_voter",
    "sweep_multiframe_voter_e",
]


def _load(name: str):
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_imports_the_shared_resolver(name: str) -> None:
    mod = _load(name)
    assert hasattr(mod, "resolve_source_video"), (
        f"{name} must resolve source_video through splitsmith.fixture_sources "
        "so the skip decision has one implementation"
    )


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_exposes_allow_missing_video(name: str) -> None:
    mod = _load(name)
    parser = mod.build_parser()
    options = {action.option_strings[0] for action in parser._actions if action.option_strings}
    assert "--allow-missing-video" in options
