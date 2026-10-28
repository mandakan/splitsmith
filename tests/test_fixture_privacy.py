"""The public repo's fixtures carry no local path (lab/sources.py).

On this project's drive layout a path names a person, so a fixture JSON
stores a source video's file name and the full path stays in the
gitignored ``tests/fixtures/.local`` map. A writer that bypasses
``lab.sources.publish`` fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.lab import sources

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Third-party data as published (SSI scoreboard responses), not our corpus.
_EXEMPT_DIRS = {".local", ".cache", "scoreboard"}


def _fixture_jsons() -> list[Path]:
    return sorted(
        p for p in FIXTURES.rglob("*.json") if not _EXEMPT_DIRS.intersection(p.relative_to(FIXTURES).parts)
    )


def _absolute_strings(node: object, where: str = "") -> list[str]:
    if isinstance(node, dict):
        return [hit for k, v in node.items() for hit in _absolute_strings(v, f"{where}.{k}")]
    if isinstance(node, list):
        return [hit for i, v in enumerate(node) for hit in _absolute_strings(v, f"{where}[{i}]")]
    return [f"{where}: {node}"] if sources.is_absolute(node) else []


def test_no_fixture_json_carries_an_absolute_path() -> None:
    offenders = []
    for path in _fixture_jsons():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        offenders += [f"{path.relative_to(FIXTURES)}{hit}" for hit in _absolute_strings(data)]
    assert offenders == [], offenders[:10]


def test_source_video_is_a_file_name() -> None:
    offenders = []
    for path in _fixture_jsons():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        sv = data.get("source_video") if isinstance(data, dict) else None
        if isinstance(sv, str) and sources.file_name(sv) != sv:
            offenders.append(f"{path.relative_to(FIXTURES)}: {sv}")
    assert offenders == [], offenders[:10]


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/Volumes/X9/raw/m/someone/hand/IMG_1.MOV", "IMG_1.MOV"),
        ("C:\\footage\\someone\\IMG_1.MOV", "IMG_1.MOV"),
        ("IMG_1.MOV", "IMG_1.MOV"),
    ],
)
def test_publish_keeps_the_name_and_remembers_the_path(tmp_path, path, name) -> None:  # noqa: ANN001
    assert sources.publish("stem", path, tmp_path) == name
    assert sources.resolve("stem", name, tmp_path) == (path if path != name else name)


def test_resolve_falls_back_to_the_stored_value_without_a_map(tmp_path) -> None:  # noqa: ANN001
    assert sources.resolve("stem", "IMG_1.MOV", tmp_path) == "IMG_1.MOV"


def test_free_text_source_keeps_a_description_and_cuts_a_path() -> None:
    assert sources.public_text("IMG_1.MOV stage 3 'Skogen'") == "IMG_1.MOV stage 3 'Skogen'"
    assert sources.public_text("/Volumes/X9/raw/m/someone/IMG_1.MOV") == "IMG_1.MOV"
