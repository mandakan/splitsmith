"""Export presets: the recurring half of the Export form (spec 2026-09-15 s1).

A preset is "how I always render". It carries the settings that repeat
across matches (output, cut, cards, publish options) and never the
match-specific ones (stage selection, the title line, the description
lead, the bundle name, the reference shooter, a publish date). Those
are asked fresh every time.

:class:`ExportPresetBody` is the one shape three places share: the API,
``export_presets.json`` on a desktop install, and the SPA's last-used
entry in ``localStorage``. Every field has a default and unknown fields
are ignored (``extra="ignore"``), which is the whole answer to "a preset
saved before a new effect shipped": the missing field takes its
default, the unknown one is dropped, and nothing fails to load.

Built-ins are code, not rows: :data:`BUILTIN_PRESETS`. The API returns
them ahead of the user's own; a store refuses to persist one.

This module is imported by the local server, so it must stay free of
``sqlalchemy`` and ``splitsmith.db`` (the hosted store lives in
:mod:`splitsmith.db.export_presets`).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from . import user_config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
PRESETS_FILENAME = "export_presets.json"
BUILTIN_PREFIX = "builtin:"
MAX_NAME_LENGTH = 60

ExportMode = Literal["single", "trims", "compare"]
OutputFormat = Literal["fcpxml", "fcp7xml", "mp4"]
OverlayCodec = Literal["auto", "hevc-alpha", "prores-4444"]
Canvas = Literal["uhd", "hd"]
PipLayout = Literal["stacked", "pip-corners"]
PaddingPreset = Literal["full", "action", "highlight", "custom"]
TransitionKind = Literal["none", "zoom", "static"]
StageCardStyle = Literal["none", "slate", "lower-third"]
UploadPrivacy = Literal["private", "unlisted", "public"]


class ExportPresetBody(BaseModel):
    """The recurring settings. Defaults equal the Export page's own."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    # Output
    mode: ExportMode = "single"
    output_format: OutputFormat = "fcpxml"
    overlay_codec: OverlayCodec = "auto"
    canvas: Canvas = "uhd"
    include_secondaries: bool = True
    pip_layout: PipLayout = "stacked"
    youtube_preset: bool = False
    # Cut
    padding_preset: PaddingPreset = "full"
    head_pad_seconds: float = 5.0
    tail_pad_seconds: float = 5.0
    transition_kind: TransitionKind = "none"
    transition_seconds: float = 0.5
    # Look
    title_page: bool = False
    title_page_seconds: float = 3.0
    closing_card: bool = False
    stage_card_style: StageCardStyle = "none"
    stage_card_seconds: float = 1.5
    summary_hold_seconds: float = 0.0
    overlay: bool = False
    grid_overlay: bool = False
    grid_hold_seconds: float = 0.0
    # Publish
    upload_after_render: bool = False
    upload_privacy: UploadPrivacy = "unlisted"
    upload_playlist: str | None = None
    upload_playlist_id: str | None = None
    upload_notify: bool = True


class ExportPreset(BaseModel):
    preset_id: str
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    builtin: bool = False
    updated_at: datetime
    body: ExportPresetBody = Field(default_factory=ExportPresetBody)


def is_builtin_id(preset_id: str) -> bool:
    return preset_id.startswith(BUILTIN_PREFIX)


def _builtin(slug: str, name: str, **body: Any) -> ExportPreset:
    return ExportPreset(
        preset_id=f"{BUILTIN_PREFIX}{slug}",
        name=name,
        builtin=True,
        updated_at=datetime(2026, 9, 15, tzinfo=UTC),
        body=ExportPresetBody(**body),
    )


#: The four intents the page ships with. Bodies are tunable; the ids are
#: not (the SPA remembers the active id in last-used).
BUILTIN_PRESETS: tuple[ExportPreset, ...] = (
    _builtin("final-cut", "Final Cut bundle"),
    _builtin(
        "youtube",
        "YouTube match video",
        output_format="mp4",
        youtube_preset=True,
        padding_preset="action",
        head_pad_seconds=0.5,
        tail_pad_seconds=1.0,
        title_page=True,
        closing_card=True,
        stage_card_style="slate",
        summary_hold_seconds=3.0,
        overlay=True,
    ),
    _builtin(
        "trims",
        "Quick trims",
        mode="trims",
        padding_preset="action",
        head_pad_seconds=0.5,
        tail_pad_seconds=1.0,
    ),
    _builtin(
        "compare", "Compare grid", mode="compare", canvas="hd", grid_overlay=True, grid_hold_seconds=3.0
    ),
)


class ExportPresetStore(Protocol):
    """Per-user saved presets. Built-ins are not stored; the API merges them."""

    async def list(self) -> list[ExportPreset]: ...

    async def put(self, preset: ExportPreset) -> None: ...

    async def delete(self, preset_id: str) -> None: ...


def load_presets_payload(raw: Any) -> list[ExportPreset]:
    """Parse the on-disk / wire list, dropping entries that do not validate.

    Never raises. A payload that is not ``{"presets": [...]}`` yields an
    empty list; a single malformed entry is skipped with a warning and
    its siblings survive (the ``export_runs.load_log`` pattern).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("presets"), list):
        return []
    presets: list[ExportPreset] = []
    for entry in raw["presets"]:
        try:
            preset = ExportPreset.model_validate(entry)
        except Exception as exc:  # noqa: BLE001 -- one bad entry must not lose the file
            ident = entry.get("preset_id") if isinstance(entry, dict) else entry
            logger.warning("skipping malformed export preset %r: %s", ident, exc)
            continue
        if preset.builtin or is_builtin_id(preset.preset_id):
            continue
        presets.append(preset)
    return presets


def _sorted(presets: list[ExportPreset]) -> list[ExportPreset]:
    return sorted(presets, key=lambda p: (p.name.casefold(), p.preset_id))


class JsonExportPresetStore:
    """Local-mode store: ``<user_config home>/export_presets.json``.

    ``async`` to satisfy the Protocol; the body is plain file I/O through
    :mod:`splitsmith.user_config`'s atomic helpers. Disabled user config
    means an empty list and no-op writes, like every file in that dir.
    """

    def _path(self) -> Path:
        return user_config.user_config_dir() / PRESETS_FILENAME

    def _load(self) -> list[ExportPreset]:
        if user_config.is_disabled():
            return []
        return load_presets_payload(user_config._read_json(self._path()))

    def _save(self, presets: list[ExportPreset]) -> None:
        target = user_config._ensure_dir()
        if target is None:
            return
        payload = {
            "schema_version": SCHEMA_VERSION,
            "presets": [p.model_dump(mode="json") for p in presets],
        }
        try:
            user_config._atomic_write_text(
                target / PRESETS_FILENAME, json.dumps(payload, indent=2, sort_keys=True)
            )
        except OSError as exc:
            logger.warning("Could not write %s: %s", PRESETS_FILENAME, exc)

    async def list(self) -> list[ExportPreset]:
        return _sorted(self._load())

    async def put(self, preset: ExportPreset) -> None:
        if preset.builtin or is_builtin_id(preset.preset_id):
            raise ValueError(f"built-in preset {preset.preset_id!r} cannot be stored")
        remaining = [p for p in self._load() if p.preset_id != preset.preset_id]
        self._save(_sorted([*remaining, preset]))

    async def delete(self, preset_id: str) -> None:
        current = self._load()
        remaining = [p for p in current if p.preset_id != preset_id]
        if len(remaining) != len(current):
            self._save(remaining)
