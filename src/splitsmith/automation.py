"""Layered automation settings (issue #215).

Splitsmith chains certain follow-up jobs automatically -- the canonical
example is "user marks beep reviewed -> shot detection fires." Some
users want the rich auto-pipeline; others (CLI / agent flows) want
manual gates so they can decide each step. This module provides the
layered settings stack that lets a global default, a project-level
override, and a per-invocation CLI flag resolve cleanly into one
effective set of toggles -- with provenance so the UI can show *why*
a value is what it is.

Resolution order, top wins: **CLI > project > global > field default**.

The schema is one ``automation`` sub-object so siblings (for example
``overlay_render_on_audit``, ``beep_detect_on_ingest``) can land later
without re-shaping the on-disk format.

Backed by ``pydantic-settings`` so the global layer composes cleanly
with the rest of the codebase: YAML loading, env-var overrides
(``SPLITSMITH_AUTOMATION_*``), and the standard pydantic validation
story all come for free. The override types and the resolver are
plain pydantic / dataclasses because they aren't loaded from any
single source -- they're computed merges.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import user_config

logger = logging.getLogger(__name__)


# Field name in the YAML and on the project model. Centralised so a
# rename touches one place.
AUTOMATION_KEY = "automation"


class AutomationSettings(BaseSettings):
    """The effective set of automation toggles.

    Default values are the *global fallback* applied when nothing
    else weighs in. The same model is used as the resolved object
    after layering -- both shapes are identical (every field has a
    concrete value at the end), which keeps callers from juggling
    two near-duplicate types.

    Loaded from the user-config ``config.yaml`` (under the
    ``automation`` block) and from ``SPLITSMITH_AUTOMATION_*`` env
    vars; init args win over both.
    """

    model_config = SettingsConfigDict(
        env_prefix="SPLITSMITH_AUTOMATION_",
        extra="ignore",
        # We do the YAML load explicitly via :func:`load_global` so the
        # settings_customise_sources hook stays simple and the test
        # suite can construct AutomationSettings without a disk read.
    )

    shot_detect_on_beep_verified: bool = True
    """When the user marks a beep reviewed, fire shot detection."""

    beep_low_confidence_threshold: float = 0.6
    """Minimum auto-detector confidence in [0, 1] required to auto-trust a beep.

    Joins issue #219: an auto-detected beep with confidence at or above
    this threshold pre-sets ``beep_reviewed=True`` (the user is offered
    a one-click confirm but the chain doesn't wait), so shot detection
    fires immediately. Below the threshold the beep lands in the HITL
    queue and the user (or an agent driving the workflow) is asked to
    pick the right candidate from the ranked list.

    Empirically the labelled fixture set has ~95% top-1 precision in the
    ``confidence >= 0.7`` band; 0.6 is the conservative default that
    keeps a few ambiguous cases in HITL rather than wasting CLAP / GBDT
    / PANN cycles on a beep the agent will end up correcting. Manual
    entries always clamp confidence to 1.0 and bypass this gate.
    """


class AutomationOverride(BaseModel):
    """Layer override -- ``None`` on a field means "inherit from below".

    Used for the project-state and CLI-flag layers. Not loaded from
    disk by pydantic-settings: project overrides ride on the project
    JSON; CLI overrides come from typer.
    """

    shot_detect_on_beep_verified: bool | None = None
    beep_low_confidence_threshold: float | None = None


# Where each resolved field came from. The UI uses this for the
# provenance badge (#216).
ProvenanceSource = Literal["cli", "project", "global", "default"]


@dataclass(frozen=True)
class FieldProvenance:
    """One resolved field's source + the layer values that fed in.

    ``source`` is the layer that supplied the effective value.
    ``cli_value`` / ``project_value`` are ``None`` when that layer
    stayed silent. ``global_value`` is always present because the
    global settings always have a default.

    Field types are union-typed (``bool | float``) because the
    automation block is now mixed (toggles + thresholds). The UI
    provenance widget renders the values via JSON.stringify so the
    union is invisible on the wire.
    """

    source: ProvenanceSource
    cli_value: bool | float | None
    project_value: bool | float | None
    global_value: bool | float


@dataclass(frozen=True)
class ResolvedAutomation:
    """Resolved settings + per-field provenance."""

    settings: AutomationSettings
    provenance: dict[str, FieldProvenance]


def _yaml_block(path: Path) -> dict[str, Any]:
    """Read the ``automation`` block from ``path``. Empty dict on
    missing file / parse error / missing block -- callers fall back
    to the field defaults."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Ignoring unreadable automation block at %s: %s", path, exc)
        return {}
    block = data.get(AUTOMATION_KEY) if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return {}
    return block


def load_global(config_path: Path | None = None) -> AutomationSettings:
    """Load the global automation settings.

    ``config_path`` defaults to the user-config ``config.yaml``; pass
    an explicit path in tests. Env-var overrides
    (``SPLITSMITH_AUTOMATION_*``) layer on top of the YAML and below
    init args, mirroring pydantic-settings' standard precedence.
    """
    if user_config.is_disabled() and config_path is None:
        return AutomationSettings()
    path = config_path or (user_config.user_config_dir() / user_config.CONFIG_FILENAME)
    block = _yaml_block(path)
    try:
        # Pass the YAML block as init kwargs; pydantic-settings will
        # still let env vars override missing keys.
        return AutomationSettings(**block)
    except (
        Exception
    ) as exc:  # noqa: BLE001 -- log then fall back so a malformed config doesn't break the app
        logger.warning("Discarding malformed automation block at %s: %s", path, exc)
        return AutomationSettings()


def resolve_automation(
    *,
    global_settings: AutomationSettings | None = None,
    project_override: AutomationOverride | None = None,
    cli_override: AutomationOverride | None = None,
) -> ResolvedAutomation:
    """Layer the three sources into one resolved set of toggles.

    Resolution per field, top wins:

    1. CLI override (init args from typer flags).
    2. Project override (``MatchProject.automation``).
    3. Global setting (loaded from ``config.yaml`` + env vars).
    4. Field default on :class:`AutomationSettings` (only reached
       when ``global_settings`` is None and the field isn't set
       anywhere).

    Returns a :class:`ResolvedAutomation` carrying the effective
    settings and per-field provenance for the UI.
    """
    if global_settings is None:
        global_settings = load_global()

    resolved_kwargs: dict[str, Any] = {}
    provenance: dict[str, FieldProvenance] = {}
    for field_name in AutomationSettings.model_fields:
        cli_val = getattr(cli_override, field_name) if cli_override is not None else None
        proj_val = getattr(project_override, field_name) if project_override is not None else None
        global_val = getattr(global_settings, field_name)
        if cli_val is not None:
            effective = cli_val
            source: ProvenanceSource = "cli"
        elif proj_val is not None:
            effective = proj_val
            source = "project"
        else:
            effective = global_val
            source = "global"
        resolved_kwargs[field_name] = effective
        provenance[field_name] = FieldProvenance(
            source=source,
            cli_value=cli_val,
            project_value=proj_val,
            global_value=global_val,
        )

    return ResolvedAutomation(
        settings=AutomationSettings(**resolved_kwargs),
        provenance=provenance,
    )


__all__ = [
    "AUTOMATION_KEY",
    "AutomationOverride",
    "AutomationSettings",
    "FieldProvenance",
    "ProvenanceSource",
    "ResolvedAutomation",
    "load_global",
    "resolve_automation",
]
