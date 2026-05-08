"""User templates for export presets (issue #198).

Templates are YAML files describing reusable export choices -- pad
durations, PiP layout, transition / title style, output format, etc.
The user picks a template in the export dialog and the values
pre-fill the form; they can tweak before submitting. The server side
of templating is intentionally thin: load + list + validate.
Substitution into the actual export request happens client-side so
the contract between dialog state and template fields stays one
clear merge.

Layout:

- Built-in templates ship under
  ``src/splitsmith/data/templates/`` (read-only, packaged).
- User templates live under ``~/.splitsmith/templates/`` and
  override built-ins by name.
- Templates are versioned via ``schema_version: 1``; unknown
  versions raise with a migration hint instead of being silently
  applied.

The schema mirrors the export dialog's controls; new dialog fields
add to ``MatchExportTemplate`` with defaults so older templates
keep loading.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION: int = 1

# Default search roots. ``user_templates_dir`` is the override location;
# ``builtin_templates_dir`` is the packaged read-only fallback. The
# resolver merges them with user templates winning on name collision.
USER_TEMPLATES_DIR = Path("~/.splitsmith/templates").expanduser()
BUILTIN_TEMPLATES_DIR = Path(__file__).parent / "data" / "templates"


class TemplateError(RuntimeError):
    """Raised when a template fails to load (parse error, schema
    mismatch, missing required field, etc.). The endpoint surfaces
    this as a 400 so the user sees what's wrong with their YAML."""


class MatchExportTemplate(BaseModel):
    """A user-facing export preset.

    All fields are optional; missing ones leave the dialog's default
    value (or the previous selection) intact when the template is
    applied. ``schema_version`` is required so we can migrate the
    shape later without silently mis-interpreting old files.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(
        ..., description="Template schema version; must equal SCHEMA_VERSION."
    )
    name: str | None = Field(
        None,
        description=("Display name in the dropdown; falls back to the file stem."),
    )
    description: str | None = Field(
        None,
        description="Free-form note shown alongside the name in the UI.",
    )
    head_pad_seconds: float | None = None
    tail_pad_seconds: float | None = None
    include_secondaries: bool | None = None
    include_overlay: bool | None = None
    pip_layout: Literal["stacked", "pip-corners"] | None = None
    output_format: Literal["fcpxml", "fcp7xml", "mp4"] | None = None
    transition_kind: Literal["none", "cross-dissolve", "dip-to-color"] | None = None
    transition_duration_seconds: float | None = None
    title_kind: Literal["none", "slate", "lower-third"] | None = None
    title_duration_seconds: float | None = None
    intro_path: str | None = None  # filesystem path; ``~`` expands at apply time
    outro_path: str | None = None


class TemplateEntry(BaseModel):
    """One template + its identity, returned by the listing endpoint."""

    id: str  # filename stem; unique within the merged catalogue
    source: Literal["builtin", "user"]
    template: MatchExportTemplate


def list_templates(
    *,
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
) -> list[TemplateEntry]:
    """Return all valid templates from the built-in + user dirs.

    User templates override built-ins by ``id`` (filename stem).
    Invalid templates are skipped with a logged warning so one bad
    file doesn't hide the others -- the listing endpoint exposes
    the same reality the user sees in their templates dir.
    """
    builtin_dir = builtin_dir if builtin_dir is not None else BUILTIN_TEMPLATES_DIR
    user_dir = user_dir if user_dir is not None else USER_TEMPLATES_DIR

    by_id: dict[str, TemplateEntry] = {}
    for entry in _scan_dir(builtin_dir, source="builtin"):
        by_id[entry.id] = entry
    for entry in _scan_dir(user_dir, source="user"):
        by_id[entry.id] = entry  # user wins on collision

    return sorted(by_id.values(), key=lambda e: (e.source != "user", e.id))


def load_template(path: Path) -> MatchExportTemplate:
    """Parse + validate a single template file.

    Raises :class:`TemplateError` on any failure so callers get a
    consistent error type regardless of which layer caught the
    problem (YAML parse, schema mismatch, version skew).
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateError(f"failed to read {path}: {exc}") from exc
    if raw is None:
        raise TemplateError(f"{path}: file is empty or YAML-null")
    if not isinstance(raw, dict):
        raise TemplateError(f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}")
    if "schema_version" not in raw:
        raise TemplateError(
            f"{path}: missing required ``schema_version`` (expected {SCHEMA_VERSION})"
        )
    try:
        version = int(raw["schema_version"])
    except (TypeError, ValueError) as exc:
        raise TemplateError(
            f"{path}: schema_version must be an integer, got {raw['schema_version']!r}"
        ) from exc
    if version != SCHEMA_VERSION:
        raise TemplateError(
            f"{path}: schema_version {version} is not supported "
            f"(expected {SCHEMA_VERSION}). Re-author against the current schema "
            "or remove the file."
        )
    try:
        return MatchExportTemplate.model_validate(raw)
    except ValidationError as exc:
        raise TemplateError(f"{path}: {exc}") from exc


def _scan_dir(root: Path, *, source: Literal["builtin", "user"]) -> list[TemplateEntry]:
    if not root.exists() or not root.is_dir():
        return []
    out: list[TemplateEntry] = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".yaml", ".yml"):
            continue
        try:
            template = load_template(path)
        except TemplateError as exc:
            # Don't fail the whole listing on a single bad file; the
            # user can fix it and re-list. We surface the warning
            # only when ``SPLITSMITH_TEMPLATES_DEBUG`` is set so
            # production runs stay quiet.
            if os.environ.get("SPLITSMITH_TEMPLATES_DEBUG"):
                print(f"[templates] skipping {path}: {exc}")
            continue
        out.append(
            TemplateEntry(
                id=path.stem,
                source=source,
                template=template,
            )
        )
    return out


__all__ = [
    "BUILTIN_TEMPLATES_DIR",
    "MatchExportTemplate",
    "SCHEMA_VERSION",
    "TemplateEntry",
    "TemplateError",
    "USER_TEMPLATES_DIR",
    "list_templates",
    "load_template",
]
