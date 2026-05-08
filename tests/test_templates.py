"""Tests for the user-template loader (issue #198).

Templates pre-fill the export dialog; bad files surface as
``TemplateError`` so the listing endpoint can show a clear message
instead of returning whatever the YAML parser felt like raising.
The schema_version gate is the future-proofing pin -- a v2 file
loaded by a v1 binary must be rejected, not silently misinterpreted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import templates as templates_mod
from splitsmith.templates import (
    BUILTIN_TEMPLATES_DIR,
    SCHEMA_VERSION,
    MatchExportTemplate,
    TemplateError,
    list_templates,
    load_template,
)


def test_builtin_templates_load(tmp_path: Path) -> None:
    """The shipped built-ins must parse cleanly -- a regression here
    means the dialog dropdown ships broken to every user."""
    entries = list_templates(builtin_dir=BUILTIN_TEMPLATES_DIR, user_dir=tmp_path)
    ids = {e.id for e in entries}
    assert "match-recap" in ids
    assert "action-cut" in ids
    for e in entries:
        assert e.source == "builtin"
        assert e.template.schema_version == SCHEMA_VERSION


def test_user_templates_override_builtins(tmp_path: Path) -> None:
    """A user file with the same stem as a built-in wins -- lets
    the user redefine ``match-recap`` without touching the package."""
    custom = tmp_path / "match-recap.yaml"
    custom.write_text(
        "schema_version: 1\nname: My recap\nhead_pad_seconds: 1.0\n",
        encoding="utf-8",
    )
    entries = list_templates(builtin_dir=BUILTIN_TEMPLATES_DIR, user_dir=tmp_path)
    by_id = {e.id: e for e in entries}
    assert by_id["match-recap"].source == "user"
    assert by_id["match-recap"].template.name == "My recap"
    # Other built-ins still visible.
    assert by_id["action-cut"].source == "builtin"


def test_user_only_template_lands_with_user_source(tmp_path: Path) -> None:
    custom = tmp_path / "vertical.yaml"
    custom.write_text(
        "schema_version: 1\nname: Vertical\noutput_format: mp4\n",
        encoding="utf-8",
    )
    entries = list_templates(builtin_dir=BUILTIN_TEMPLATES_DIR, user_dir=tmp_path)
    custom_entry = next(e for e in entries if e.id == "vertical")
    assert custom_entry.source == "user"
    assert custom_entry.template.output_format == "mp4"


def test_load_template_rejects_unknown_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "future.yaml"
    p.write_text("schema_version: 999\nname: Future\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="schema_version 999 is not supported"):
        load_template(p)


def test_load_template_rejects_missing_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "noversion.yaml"
    p.write_text("name: No version\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="missing required ``schema_version``"):
        load_template(p)


def test_load_template_rejects_unknown_field(tmp_path: Path) -> None:
    """``extra="forbid"`` catches typos. Without this guard a user
    typing ``transitions_kind`` (vs ``transition_kind``) would see
    no effect when applying the template -- silent failure."""
    p = tmp_path / "typo.yaml"
    p.write_text(
        "schema_version: 1\ntransitions_kind: zoom\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateError):
        load_template(p)


def test_load_template_rejects_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("schema_version: 1\nname: [unclosed\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="failed to read"):
        load_template(p)


def test_load_template_rejects_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- schema_version: 1\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="top-level YAML must be a mapping"):
        load_template(p)


def test_invalid_files_skip_in_listing_without_breaking_others(
    tmp_path: Path,
) -> None:
    """One bad file shouldn't hide all the other valid ones; the
    listing endpoint should keep working so the user can still pick
    a template while they fix the broken file."""
    good = tmp_path / "good.yaml"
    good.write_text("schema_version: 1\nname: Good\n", encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 999\n", encoding="utf-8")
    entries = list_templates(builtin_dir=tmp_path / "_empty", user_dir=tmp_path)
    ids = {e.id for e in entries}
    assert "good" in ids
    assert "bad" not in ids


def test_template_validates_literal_choices(tmp_path: Path) -> None:
    """``output_format`` is constrained to a Literal; an invalid
    value gets caught at parse time instead of confusing the
    server later."""
    p = tmp_path / "weird.yaml"
    p.write_text(
        "schema_version: 1\noutput_format: avi\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateError):
        load_template(p)


def test_match_export_template_round_trip() -> None:
    """``model_dump(exclude_none=True)`` drops unset fields -- the
    listing endpoint relies on this so unset values don't override
    dialog defaults when applied client-side."""
    t = MatchExportTemplate(
        schema_version=1,
        head_pad_seconds=2.0,
        title_kind="slate",
    )
    payload = t.model_dump(exclude_none=True)
    assert payload == {
        "schema_version": 1,
        "head_pad_seconds": 2.0,
        "title_kind": "slate",
    }


def test_list_templates_handles_missing_dirs(tmp_path: Path) -> None:
    """Fresh installs may not have either dir present; the loader
    must not raise."""
    entries = templates_mod.list_templates(
        builtin_dir=tmp_path / "missing-builtin",
        user_dir=tmp_path / "missing-user",
    )
    assert entries == []
