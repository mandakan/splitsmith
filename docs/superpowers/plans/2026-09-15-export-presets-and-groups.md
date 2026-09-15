# Export presets and option groups (part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Export page opens on a preset row (four built-ins plus the user's saved ones), its options fold into four groups with one-line summaries, and the recurring settings survive a reload.

**Architecture:** One Pydantic model (`ExportPresetBody`, every field defaulted, unknown fields ignored) is the wire shape, the on-disk shape and the `localStorage` shape. Two stores behind one Protocol, following `recent_projects` (JSON file locally, a per-user table hosted), exposed by a small router. In the SPA the page's ~16 recurring `useState` hooks collapse into one `ExportSettings` object so `applyPreset` / `isDirty` / last-used are pure functions over it; the JSX regroups under a collapsible `Section` and the option blocks move into group components.

**Tech Stack:** Python 3.11+, Pydantic, FastAPI, SQLAlchemy 2 + alembic (hosted only); React 19, Tailwind 4, vitest + testing-library.

**Spec:** `docs/superpowers/specs/2026-09-15-export-presets-and-look-gallery-design.md`, section 1 ("Presets and groups"). Parts 2 and 3 (gallery, preview) are separate plans.

## Global Constraints

- `uv` for Python, never pip; `pnpm` in `src/splitsmith/ui_static`.
- Python: type hints everywhere, `pathlib.Path`, Black at 110, Ruff. Imports: stdlib / third-party / local, blank-line separated; no `..module` imports deeper than one dot.
- `src/splitsmith/export_presets.py` must not import `sqlalchemy` or `splitsmith.db` at module level (`tests/test_local_mode_no_hosted_imports.py` pins this).
- Every hosted store method filters on `user_id`; every new method gets an isolation test.
- Migrations must match the models exactly (`scripts/ci/assert_migrations_match_models.py` runs `compare_metadata` in CI). Head revision at the time of writing: `58603835d0bd`.
- Presets never enter `state_docs` or the sync manifest.
- SPA visual budget: primitives from `components/ui` only; `Segmented` for closed choices, `Chip` neutral with a tick, one `primary` button per view (the Export button), no `text-[...]` outside `components/ui`, no issue numbers in UI copy.
- Prose in code comments and UI: ASCII punctuation, no dashes as punctuation.
- Run Python tests targeted (`uv run pytest tests/<file> -n0`); a full run segfaults on this Mac's Python 3.14.
- Match-specific fields (stage selection, `titleInfo`, description lead, project name, `audioFrom`, `publishAt`) are never stored in a preset or in last-used.

---

## File map

Create:
- `src/splitsmith/export_presets.py`: `ExportPresetBody`, `ExportPreset`, `BUILTIN_PRESETS`, `is_builtin_id`, `ExportPresetStore` Protocol, `JsonExportPresetStore`, `load_presets_payload`.
- `src/splitsmith/db/export_presets.py`: `PostgresExportPresetStore`.
- `alembic/versions/c3e8a1d47f92_create_export_presets_table.py`.
- `src/splitsmith/ui/export_presets_api.py`: the three routes.
- `tests/test_export_presets.py`, `tests/test_export_presets_store.py`, `tests/test_export_presets_api.py`.
- `src/splitsmith/ui_static/src/lib/exportPresets.ts` + `.test.ts`.
- `src/splitsmith/ui_static/src/components/export/Section.tsx` (moved out of the page, gains collapse).
- `src/splitsmith/ui_static/src/components/export/PresetRow.tsx` + `.test.tsx`.
- `src/splitsmith/ui_static/src/components/export/OutputGroup.tsx`, `CutGroup.tsx`, `LookGroup.tsx`, `DetailsGroup.tsx`.
- `src/splitsmith/ui_static/src/pages/Export.presets.test.tsx`.

Modify:
- `src/splitsmith/db/models.py` (add `ExportPresetRow`), `src/splitsmith/db/__init__.py` (export the store).
- `src/splitsmith/ui/server.py` (`TenantContext.export_presets`, `AppState` field + property, `_build_tenant`, `include_router`).
- `src/splitsmith/ui_static/src/lib/api.ts` (types + three calls).
- `src/splitsmith/ui_static/src/pages/Export.tsx` (settings object, preset row, groups).
- `CLAUDE.md` (a short "Export presets" note).

---

### Task 1: The preset model, built-ins and the local JSON store

**Files:**
- Create: `src/splitsmith/export_presets.py`
- Test: `tests/test_export_presets.py`

**Interfaces:**
- Produces: `ExportPresetBody`, `ExportPreset`, `BUILTIN_PRESETS: tuple[ExportPreset, ...]`, `is_builtin_id(preset_id) -> bool`, `ExportPresetStore` (Protocol with async `list / put / delete`), `JsonExportPresetStore`, `load_presets_payload(raw) -> list[ExportPreset]`, `PRESETS_FILENAME = "export_presets.json"`, `MAX_NAME_LENGTH = 60`.

- [ ] **Step 1: Write the failing tests**

```python
"""The export preset model and its local JSON store (spec 2026-09-15 s1).

The body is the one shape three places share: the API, the on-disk file
and the SPA's last-used entry. Every field defaults and unknown fields
are ignored, which is the whole answer to "a preset saved before a new
effect shipped".
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith import export_presets as ep
from splitsmith.export_presets import (
    BUILTIN_PRESETS,
    ExportPreset,
    ExportPresetBody,
    JsonExportPresetStore,
    is_builtin_id,
    load_presets_payload,
)


def _preset(preset_id: str = "p1", name: str = "Club night", **body) -> ExportPreset:
    return ExportPreset(
        preset_id=preset_id,
        name=name,
        updated_at=datetime(2026, 9, 15, tzinfo=UTC),
        body=ExportPresetBody(**body),
    )


def test_body_defaults_match_the_page_defaults() -> None:
    body = ExportPresetBody()
    assert body.mode == "single"
    assert body.output_format == "fcpxml"
    assert body.padding_preset == "full"
    assert (body.head_pad_seconds, body.tail_pad_seconds) == (5.0, 5.0)
    assert body.transition_kind == "none"
    assert body.stage_card_style == "none"
    assert body.upload_privacy == "unlisted"
    assert body.upload_notify is True


def test_body_round_trips_through_json() -> None:
    body = ExportPresetBody(mode="compare", canvas="hd", grid_overlay=True, grid_hold_seconds=3)
    again = ExportPresetBody.model_validate(json.loads(body.model_dump_json()))
    assert again == body


def test_body_ignores_unknown_fields_and_defaults_missing_ones() -> None:
    body = ExportPresetBody.model_validate({"mode": "trims", "laser_wipe": True})
    assert body.mode == "trims"
    assert body.output_format == "fcpxml"
    assert not hasattr(body, "laser_wipe")


def test_load_skips_a_malformed_envelope_and_keeps_its_siblings(caplog: pytest.LogCaptureFixture) -> None:
    raw = {
        "schema_version": 1,
        "presets": [
            _preset("a", "A").model_dump(mode="json"),
            {"preset_id": "b"},  # no name, no body
            _preset("c", "C").model_dump(mode="json"),
        ],
    }
    with caplog.at_level("WARNING"):
        presets = load_presets_payload(raw)
    assert [p.preset_id for p in presets] == ["a", "c"]
    assert "b" in caplog.text or "skipping" in caplog.text.lower()


@pytest.mark.parametrize("raw", [None, [], "x", {"presets": "no"}])
def test_load_tolerates_a_non_dict_payload(raw) -> None:
    assert load_presets_payload(raw) == []


def test_builtins_have_stable_ids_and_are_flagged() -> None:
    ids = [p.preset_id for p in BUILTIN_PRESETS]
    assert ids == ["builtin:final-cut", "builtin:youtube", "builtin:trims", "builtin:compare"]
    assert all(p.builtin for p in BUILTIN_PRESETS)
    assert all(is_builtin_id(i) for i in ids)
    assert not is_builtin_id("p1")


def test_builtin_bodies_carry_their_intent() -> None:
    by_id = {p.preset_id: p.body for p in BUILTIN_PRESETS}
    assert by_id["builtin:final-cut"] == ExportPresetBody()
    yt = by_id["builtin:youtube"]
    assert (yt.output_format, yt.youtube_preset, yt.title_page, yt.stage_card_style) == ("mp4", True, True, "slate")
    assert by_id["builtin:trims"].mode == "trims"
    assert by_id["builtin:compare"].mode == "compare"


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JsonExportPresetStore:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    return JsonExportPresetStore()


def test_json_store_starts_empty_and_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    assert asyncio.run(store.list()) == []
    asyncio.run(store.put(_preset("p1", "Club night", mode="trims")))
    listed = asyncio.run(store.list())
    assert [p.name for p in listed] == ["Club night"]
    assert listed[0].body.mode == "trims"
    assert (tmp_path / ep.PRESETS_FILENAME).exists()


def test_json_store_put_replaces_by_id_and_sorts_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    asyncio.run(store.put(_preset("p2", "Zed")))
    asyncio.run(store.put(_preset("p1", "Alpha")))
    asyncio.run(store.put(_preset("p2", "Beta", mode="compare")))
    listed = asyncio.run(store.list())
    assert [(p.preset_id, p.name) for p in listed] == [("p1", "Alpha"), ("p2", "Beta")]
    assert listed[1].body.mode == "compare"


def test_json_store_delete_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    asyncio.run(store.put(_preset("p1")))
    asyncio.run(store.delete("p1"))
    asyncio.run(store.delete("p1"))
    assert asyncio.run(store.list()) == []


def test_json_store_never_persists_a_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(store.put(BUILTIN_PRESETS[0]))


def test_json_store_is_inert_when_user_config_is_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setenv("SPLITSMITH_DISABLE_USER_CONFIG", "1")
    asyncio.run(store.put(_preset("p1")))
    assert asyncio.run(store.list()) == []
    assert not (tmp_path / ep.PRESETS_FILENAME).exists()


def test_json_store_survives_a_corrupt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path, monkeypatch)
    (tmp_path / ep.PRESETS_FILENAME).write_text("{not json", encoding="utf-8")
    assert asyncio.run(store.list()) == []
    asyncio.run(store.put(_preset("p1")))
    assert [p.preset_id for p in asyncio.run(store.list())] == ["p1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_presets.py -n0 -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.export_presets'`

- [ ] **Step 3: Write the module**

```python
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
    _builtin("trims", "Quick trims", mode="trims", padding_preset="action", head_pad_seconds=0.5, tail_pad_seconds=1.0),
    _builtin("compare", "Compare grid", mode="compare", canvas="hd", grid_overlay=True, grid_hold_seconds=3.0),
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

    def _path(self):
        return user_config.user_config_dir() / PRESETS_FILENAME

    def _load(self) -> list[ExportPreset]:
        if user_config.is_disabled():
            return []
        return load_presets_payload(user_config._read_json(self._path()))

    def _save(self, presets: list[ExportPreset]) -> None:
        target = user_config._ensure_dir()
        if target is None:
            return
        payload = {"schema_version": SCHEMA_VERSION, "presets": [p.model_dump(mode="json") for p in presets]}
        try:
            user_config._atomic_write_text(target / PRESETS_FILENAME, json.dumps(payload, indent=2, sort_keys=True))
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_export_presets.py -n0 -q`
Expected: all PASS. Then `uv run ruff check src/splitsmith/export_presets.py tests/test_export_presets.py && uv run black --check src/splitsmith/export_presets.py tests/test_export_presets.py`.

- [ ] **Step 5: Confirm the module stays out of the hosted import chain**

Run: `uv run pytest tests/test_local_mode_no_hosted_imports.py -n0 -q`
Expected: PASS (the module imports only stdlib, pydantic and `user_config`).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/export_presets.py tests/test_export_presets.py
git commit -m "feat(export): preset model, built-ins and the local JSON store"
```

---

### Task 2: The hosted store, its row and the migration

**Files:**
- Modify: `src/splitsmith/db/models.py` (append after `DeviceAuthorizationRow`)
- Create: `src/splitsmith/db/export_presets.py`
- Modify: `src/splitsmith/db/__init__.py`
- Create: `alembic/versions/c3e8a1d47f92_create_export_presets_table.py`
- Test: `tests/test_export_presets_store.py`

**Interfaces:**
- Consumes: `ExportPreset`, `ExportPresetBody`, `ExportPresetStore`, `is_builtin_id` from Task 1.
- Produces: `ExportPresetRow`, `PostgresExportPresetStore(session_factory, *, user_id)` satisfying `ExportPresetStore`.

- [ ] **Step 1: Write the failing tests**

```python
"""``PostgresExportPresetStore``: per-user isolation and the round trip.

SQLite in-memory via aiosqlite, the same harness as
``test_scoreboard_identity_store``. Every method gets an isolation test:
a second user must see, overwrite and delete nothing of the first's.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from splitsmith.db import Base, PostgresExportPresetStore, User, create_engine, sessionmaker
from splitsmith.export_presets import BUILTIN_PRESETS, ExportPreset, ExportPresetBody, ExportPresetStore


def _preset(preset_id: str = "p1", name: str = "Club night", **body) -> ExportPreset:
    return ExportPreset(
        preset_id=preset_id, name=name, updated_at=datetime(2026, 9, 15, tzinfo=UTC), body=ExportPresetBody(**body)
    )


def _two_users() -> tuple[PostgresExportPresetStore, PostgresExportPresetStore]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            a, b = User(email="a@example.com"), User(email="b@example.com")
            s.add_all([a, b])
            await s.commit()
            await s.refresh(a)
            await s.refresh(b)
            return a.id, b.id

    a_id, b_id = asyncio.run(_setup())
    return PostgresExportPresetStore(sf, user_id=a_id), PostgresExportPresetStore(sf, user_id=b_id)


def test_satisfies_the_protocol() -> None:
    a, _ = _two_users()
    typed: ExportPresetStore = a
    assert typed is a


@pytest.mark.parametrize("bad", ["", None, 0])
def test_construction_rejects_an_empty_user_id(bad) -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    with pytest.raises(ValueError):
        PostgresExportPresetStore(sessionmaker(engine), user_id=bad)


def test_round_trip_and_name_order() -> None:
    a, _ = _two_users()
    asyncio.run(a.put(_preset("p2", "Zed", mode="compare")))
    asyncio.run(a.put(_preset("p1", "Alpha")))
    listed = asyncio.run(a.list())
    assert [(p.preset_id, p.name) for p in listed] == [("p1", "Alpha"), ("p2", "Zed")]
    assert listed[1].body.mode == "compare"
    assert listed[0].builtin is False


def test_put_replaces_by_id() -> None:
    a, _ = _two_users()
    asyncio.run(a.put(_preset("p1", "Old")))
    asyncio.run(a.put(_preset("p1", "New", mode="trims")))
    listed = asyncio.run(a.list())
    assert [(p.name, p.body.mode) for p in listed] == [("New", "trims")]


def test_delete_is_idempotent() -> None:
    a, _ = _two_users()
    asyncio.run(a.put(_preset("p1")))
    asyncio.run(a.delete("p1"))
    asyncio.run(a.delete("p1"))
    assert asyncio.run(a.list()) == []


def test_refuses_a_builtin() -> None:
    a, _ = _two_users()
    with pytest.raises(ValueError):
        asyncio.run(a.put(BUILTIN_PRESETS[0]))


def test_list_is_isolated_per_user() -> None:
    a, b = _two_users()
    asyncio.run(a.put(_preset("p1")))
    assert asyncio.run(b.list()) == []


def test_put_with_the_same_id_does_not_cross_users() -> None:
    a, b = _two_users()
    asyncio.run(a.put(_preset("p1", "A's")))
    asyncio.run(b.put(_preset("p1", "B's")))
    assert [p.name for p in asyncio.run(a.list())] == ["A's"]
    assert [p.name for p in asyncio.run(b.list())] == ["B's"]


def test_delete_does_not_cross_users() -> None:
    a, b = _two_users()
    asyncio.run(a.put(_preset("p1")))
    asyncio.run(b.delete("p1"))
    assert [p.preset_id for p in asyncio.run(a.list())] == ["p1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_presets_store.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'PostgresExportPresetStore'`

- [ ] **Step 3: Add the row to `models.py`**

Append after `DeviceAuthorizationRow` (end of file); `JSON`, `DateTime`, `ForeignKey`, `String`, `func` are already imported there.

```python
class ExportPresetRow(Base):
    """One saved export preset per (user, preset_id) (spec 2026-09-15 s1).

    Hosted-mode counterpart to the local ``export_presets.json``. A
    preset belongs to a user, not a match, which is why this is its own
    table and not a ``state_docs`` kind: a per-match kind would enter
    the sync manifest and its allowlists, and presets must not.

    ``body`` is the JSON dump of ``export_presets.ExportPresetBody``;
    the store re-validates on read so an older row loads with defaults
    for fields that did not exist when it was written.

    **Multi-tenant:** the primary key leads with ``user_id`` and the
    ``tenant_isolation`` RLS policy applies (migration c3e8a1d47f92);
    the store filters on ``user_id`` in every statement as well.
    """

    __tablename__ = "export_presets"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    preset_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ExportPresetRow user_id={self.user_id!r} preset_id={self.preset_id!r}>"
```

- [ ] **Step 4: Write the store**

`src/splitsmith/db/export_presets.py`:

```python
"""Postgres-backed :class:`ExportPresetStore` (spec 2026-09-15 s1).

Hosted-mode counterpart to :class:`splitsmith.export_presets.JsonExportPresetStore`.
One row per (user, preset_id) in ``export_presets``.

**Multi-tenant invariant:** every statement filters on
``ExportPresetRow.user_id == self._user_id``. The composite primary key
enforces the boundary at the DB layer; the per-method filter enforces it
at the query layer. ``tests/test_export_presets_store.py`` has one
isolation test per method; add one for any new method.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..export_presets import ExportPreset, ExportPresetBody, is_builtin_id
from .models import ExportPresetRow


class PostgresExportPresetStore:
    def __init__(self, session_factory: async_sessionmaker, *, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(
                "PostgresExportPresetStore requires a non-empty user_id; "
                f"got {user_id!r}. The auth layer must resolve a real "
                "user before constructing the per-request store."
            )
        self._session_factory = session_factory
        self._user_id = user_id

    async def list(self) -> list[ExportPreset]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(select(ExportPresetRow).where(ExportPresetRow.user_id == self._user_id))
            ).scalars().all()
        presets = [
            ExportPreset(
                preset_id=row.preset_id,
                name=row.name,
                updated_at=row.updated_at,
                body=ExportPresetBody.model_validate(row.body),
            )
            for row in rows
        ]
        return sorted(presets, key=lambda p: (p.name.casefold(), p.preset_id))

    async def put(self, preset: ExportPreset) -> None:
        if preset.builtin or is_builtin_id(preset.preset_id):
            raise ValueError(f"built-in preset {preset.preset_id!r} cannot be stored")
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(ExportPresetRow).where(
                        ExportPresetRow.user_id == self._user_id,
                        ExportPresetRow.preset_id == preset.preset_id,
                    )
                )
            ).scalar_one_or_none()
            body = preset.body.model_dump(mode="json")
            if existing is None:
                session.add(
                    ExportPresetRow(
                        user_id=self._user_id,
                        preset_id=preset.preset_id,
                        name=preset.name,
                        body=body,
                        updated_at=preset.updated_at,
                    )
                )
            else:
                existing.name = preset.name
                existing.body = body
                existing.updated_at = preset.updated_at
            await session.commit()

    async def delete(self, preset_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(ExportPresetRow).where(
                    ExportPresetRow.user_id == self._user_id,
                    ExportPresetRow.preset_id == preset_id,
                )
            )
            await session.commit()
```

In `src/splitsmith/db/__init__.py`, next to the `PostgresProfileStore` import and `__all__` entry:

```python
from .export_presets import PostgresExportPresetStore
```
and add `"PostgresExportPresetStore",` to `__all__`.

- [ ] **Step 5: Write the migration**

`alembic/versions/c3e8a1d47f92_create_export_presets_table.py`:

```python
"""create export_presets table

Per-user saved export presets (spec 2026-09-15 s1). Its own table, not
a ``state_docs`` kind: a preset belongs to a user, not a match, and
must stay out of the sync manifest. Composite primary key
``(user_id, preset_id)``; joins the ``tenant_isolation`` RLS policy
family like ``match_comments`` (b4d8f1a90c27).

Revision ID: c3e8a1d47f92
Revises: 58603835d0bd
Create Date: 2026-09-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e8a1d47f92"
down_revision: str | Sequence[str] | None = "58603835d0bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "tenant_isolation"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "export_presets",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("preset_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "preset_id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        # Each statement separately: asyncpg cannot run several commands
        # in one prepared statement.
        op.execute("ALTER TABLE export_presets ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE export_presets FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_POLICY} ON export_presets "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.user_id', true)) "
            f"WITH CHECK (user_id = current_setting('app.user_id', true))"
        )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON export_presets")
        op.execute("ALTER TABLE export_presets NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE export_presets DISABLE ROW LEVEL SECURITY")
    op.drop_table("export_presets")
```

Before writing, confirm the head has not moved: `cd alembic/versions && grep -h '^revision' *.py | awk -F'"' '{print $2}' | sort > /tmp/r; grep -h '^down_revision' *.py | awk -F'"' '{print $2}' | sort > /tmp/d; comm -23 /tmp/r /tmp/d` must print exactly `58603835d0bd`. If it prints something else, use that as `down_revision`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_export_presets_store.py tests/test_schema_diff.py -n0 -q`
Expected: all PASS.

- [ ] **Step 7: Check the migration against the model on Postgres if one is reachable**

Run (only if a scratch Postgres is available; it drops every table):
`SPLITSMITH_DATABASE_URL=postgresql+asyncpg://splitsmith:splitsmith@localhost:5432/splitsmith_ci uv run python scripts/ci/assert_migrations_match_models.py`
Expected: four assertions pass, "empty diff" included. If no Postgres is reachable locally, say so in the PR; CI runs the same script.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/db/models.py src/splitsmith/db/export_presets.py src/splitsmith/db/__init__.py alembic/versions/c3e8a1d47f92_create_export_presets_table.py tests/test_export_presets_store.py
git commit -m "feat(export): hosted export_presets table and store"
```

---

### Task 3: Server wiring and the settings routes

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`TenantContext`, `AppState`, `_build_tenant`, router registration)
- Create: `src/splitsmith/ui/export_presets_api.py`
- Test: `tests/test_export_presets_api.py`

**Interfaces:**
- Consumes: Task 1's module, Task 2's store.
- Produces: `GET /api/settings/export-presets` -> `{"presets": [ExportPreset...]}`; `PUT /api/settings/export-presets/{preset_id}` with `{"name": str, "body": ExportPresetBody}` -> `ExportPreset` (201 when `preset_id == "new"`, else 200); `DELETE /api/settings/export-presets/{preset_id}` -> 204. 403 for a `builtin:` id on PUT / DELETE, 404 on DELETE of an unknown id, 422 on a blank or over-long name. `AppState.export_presets` property.

- [ ] **Step 1: Write the failing tests**

```python
"""The export-preset settings routes, local and hosted.

Local mode: one operator, the JSON file under ``SPLITSMITH_HOME`` (the
autouse ``_isolate_user_config`` fixture points it at a tmp dir). Hosted
mode: per signed-in user, through ``tests.hosted_helpers``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.export_presets import BUILTIN_PRESETS
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_ui_server import _match_create_app, _MatchClient

BUILTIN_IDS = [p.preset_id for p in BUILTIN_PRESETS]


@pytest.fixture
def client(tmp_path: Path):
    app = _match_create_app(project_root=tmp_path / "match", project_name="Presets")
    return _MatchClient(app)


def _body(**over):
    return {"mode": "single", "output_format": "mp4", **over}


def test_list_leads_with_the_builtins(client) -> None:
    r = client.get("/api/settings/export-presets")
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert [p["preset_id"] for p in presets] == BUILTIN_IDS
    assert all(p["builtin"] for p in presets)
    assert presets[1]["body"]["output_format"] == "mp4"


def test_put_new_generates_an_id_and_lists_after_the_builtins(client) -> None:
    r = client.put("/api/settings/export-presets/new", json={"name": "Club night", "body": _body()})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["builtin"] is False and created["name"] == "Club night"
    assert not created["preset_id"].startswith("builtin:")
    listed = client.get("/api/settings/export-presets").json()["presets"]
    assert [p["preset_id"] for p in listed] == [*BUILTIN_IDS, created["preset_id"]]


def test_put_existing_replaces_name_and_body(client) -> None:
    pid = client.put("/api/settings/export-presets/new", json={"name": "A", "body": _body()}).json()["preset_id"]
    r = client.put(f"/api/settings/export-presets/{pid}", json={"name": "B", "body": _body(mode="trims")})
    assert r.status_code == 200
    own = [p for p in client.get("/api/settings/export-presets").json()["presets"] if not p["builtin"]]
    assert [(p["name"], p["body"]["mode"]) for p in own] == [("B", "trims")]


def test_put_ignores_unknown_body_fields(client) -> None:
    r = client.put("/api/settings/export-presets/new", json={"name": "X", "body": _body(laser_wipe=True)})
    assert r.status_code == 201
    assert "laser_wipe" not in r.json()["body"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_put_rejects_a_blank_or_long_name(client, name: str) -> None:
    r = client.put("/api/settings/export-presets/new", json={"name": name, "body": _body()})
    assert r.status_code == 422


def test_put_trims_the_name(client) -> None:
    r = client.put("/api/settings/export-presets/new", json={"name": "  Club  ", "body": _body()})
    assert r.json()["name"] == "Club"


def test_builtins_are_immutable(client) -> None:
    assert client.put(f"/api/settings/export-presets/{BUILTIN_IDS[0]}", json={"name": "X", "body": _body()}).status_code == 403
    assert client.delete(f"/api/settings/export-presets/{BUILTIN_IDS[0]}").status_code == 403
    assert [p["preset_id"] for p in client.get("/api/settings/export-presets").json()["presets"]] == BUILTIN_IDS


def test_delete_removes_and_unknown_is_404(client) -> None:
    pid = client.put("/api/settings/export-presets/new", json={"name": "A", "body": _body()}).json()["preset_id"]
    assert client.delete(f"/api/settings/export-presets/{pid}").status_code == 204
    assert client.delete(f"/api/settings/export-presets/{pid}").status_code == 404
    assert [p["preset_id"] for p in client.get("/api/settings/export-presets").json()["presets"]] == BUILTIN_IDS


def test_hosted_presets_are_per_user(hosted_app) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.put("/api/settings/export-presets/new", json={"name": "A's", "body": _body()})
    assert r.status_code == 201, r.text
    pid = r.json()["preset_id"]
    assert [p["name"] for p in client.get("/api/settings/export-presets").json()["presets"] if not p["builtin"]] == ["A's"]

    other = TestClient(client.app, follow_redirects=False)
    login(other, sender, "b@example.com")
    assert [p for p in other.get("/api/settings/export-presets").json()["presets"] if not p["builtin"]] == []
    assert other.delete(f"/api/settings/export-presets/{pid}").status_code == 404
    assert [p["name"] for p in client.get("/api/settings/export-presets").json()["presets"] if not p["builtin"]] == ["A's"]


def test_hosted_presets_need_a_session(hosted_app) -> None:
    client, _ = hosted_app
    assert client.get("/api/settings/export-presets").status_code in (401, 403)
```

`hosted_app` and `hosted_env` are registered for every test module by `tests/conftest.py:19`; only `login` is imported here.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_export_presets_api.py -n0 -q`
Expected: FAIL, the GET answers 404 (route missing).

- [ ] **Step 3: Wire `TenantContext` and `AppState`**

In `server.py`:

1. Near the top imports, after `from .. import ( ... user_config, ...)`, add `from .. import export_presets as export_presets_module`.
2. In `TenantContext`, after `profile: PostgresProfileStore | None = None`:

```python
    # Per-user saved export presets (spec 2026-09-15 s1). ``None`` in
    # local mode, where ``AppState.export_presets`` falls back to the
    # JSON file store.
    export_presets: export_presets_module.ExportPresetStore | None = None
```

3. In `AppState`, after `_scoreboard_identity`:

```python
    _export_presets: export_presets_module.ExportPresetStore = field(
        default_factory=export_presets_module.JsonExportPresetStore
    )
```

4. After the `scoreboard_identity` property pair:

```python
    @property
    def export_presets(self) -> export_presets_module.ExportPresetStore:
        tenant = current_tenant.get()
        if tenant is not None and tenant.export_presets is not None:
            return tenant.export_presets
        return self._export_presets
```

5. In `_build_tenant` (hosted wiring), add to the `from ..db import (...)` block `PostgresExportPresetStore,` and to the `TenantContext(...)` call:

```python
            export_presets=PostgresExportPresetStore(tenant_factory, user_id=user_id),
```

6. Next to `app.include_router(youtube_router)`:

```python
    # Export presets (spec 2026-09-15 s1): one router for both modes; the
    # store behind ``state.export_presets`` is what differs.
    from .export_presets_api import router as export_presets_router

    app.include_router(export_presets_router)
```

- [ ] **Step 4: Write the router**

`src/splitsmith/ui/export_presets_api.py`:

```python
"""Export preset settings routes (spec 2026-09-15 s1).

Built-ins come from :data:`export_presets.BUILTIN_PRESETS` and lead the
list; the user's own follow, by name. Writes to a ``builtin:`` id are
403: a built-in can be applied and saved *as* a new preset, never
overwritten or deleted.

Works in both modes: ``state.export_presets`` is the JSON file store
locally and the per-user Postgres store hosted (the auth gate pins the
tenant before this router runs). This module must not import
``server``; it reaches state through ``request.app.state``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from ..export_presets import (
    BUILTIN_PRESETS,
    MAX_NAME_LENGTH,
    ExportPreset,
    ExportPresetBody,
    ExportPresetStore,
    is_builtin_id,
)

router = APIRouter()

NEW_ID = "new"


class ExportPresetList(BaseModel):
    presets: list[ExportPreset]


class PutExportPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    body: ExportPresetBody

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def _store(request: Request) -> ExportPresetStore:
    return request.app.state.splitsmith_state.export_presets


@router.get("/api/settings/export-presets", response_model=ExportPresetList)
async def list_export_presets(request: Request) -> ExportPresetList:
    own = await _store(request).list()
    return ExportPresetList(presets=[*BUILTIN_PRESETS, *own])


@router.put("/api/settings/export-presets/{preset_id}", response_model=ExportPreset)
async def put_export_preset(preset_id: str, req: PutExportPresetRequest, request: Request, response: Response) -> ExportPreset:
    if is_builtin_id(preset_id):
        raise HTTPException(status_code=403, detail="built-in presets cannot be changed")
    created = preset_id == NEW_ID
    preset = ExportPreset(
        preset_id=uuid.uuid4().hex if created else preset_id,
        name=req.name,
        updated_at=datetime.now(UTC),
        body=req.body,
    )
    await _store(request).put(preset)
    if created:
        response.status_code = 201
    return preset


@router.delete("/api/settings/export-presets/{preset_id}", status_code=204)
async def delete_export_preset(preset_id: str, request: Request) -> Response:
    if is_builtin_id(preset_id):
        raise HTTPException(status_code=403, detail="built-in presets cannot be deleted")
    store = _store(request)
    if not any(p.preset_id == preset_id for p in await store.list()):
        raise HTTPException(status_code=404, detail="not found")
    await store.delete(preset_id)
    return Response(status_code=204)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_export_presets_api.py tests/test_local_mode_no_hosted_imports.py -n0 -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check src/splitsmith/ui/export_presets_api.py src/splitsmith/ui/server.py tests/test_export_presets_api.py && uv run black --check src/splitsmith/ui/export_presets_api.py tests/test_export_presets_api.py`

```bash
git add src/splitsmith/ui/server.py src/splitsmith/ui/export_presets_api.py tests/test_export_presets_api.py
git commit -m "feat(export): /api/settings/export-presets in both modes"
```

---

### Task 4: `lib/api.ts` and the pure `lib/exportPresets.ts`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (types near `YouTubeSettings`; calls next to `getYouTubeSettings`)
- Create: `src/splitsmith/ui_static/src/lib/exportPresets.ts`
- Test: `src/splitsmith/ui_static/src/lib/exportPresets.test.ts`

**Interfaces:**
- Produces (api.ts): `ExportPresetBody`, `ExportPreset`, `api.getExportPresets(): Promise<{ presets: ExportPreset[] }>`, `api.putExportPreset(id: string, name: string, body: ExportPresetBody): Promise<ExportPreset>`, `api.deleteExportPreset(id: string): Promise<void>`.
- Produces (exportPresets.ts): `ExportSettings`, `DEFAULT_EXPORT_SETTINGS`, `PaddingPreset`, `TransitionKind`, `PADDING_PRESETS`, `settingsToBody(s)`, `applyBody(s, body)`, `isDirty(s, body)`, `bodiesEqual(a, b)`, `groupSummary(s, group, ctx)`, `saveLastUsed(storage, s, presetId)`, `loadLastUsed(storage)`, `LAST_USED_KEY`, `NEW_PRESET_ID = "new"`, `CUSTOM = "custom"`.

- [ ] **Step 1: Add the API types and calls**

In `lib/api.ts`, after the `YouTubeSettings` interface:

```ts
/** The recurring half of the Export form (spec 2026-09-15 s1). Mirrors
 *  ``export_presets.ExportPresetBody``; every field has a server default
 *  and unknown fields are dropped there, so the SPA never needs to
 *  migrate a stored body. */
export interface ExportPresetBody {
  schema_version?: number;
  mode: "single" | "trims" | "compare";
  output_format: "fcpxml" | "fcp7xml" | "mp4";
  overlay_codec: OverlayCodec;
  canvas: "uhd" | "hd";
  include_secondaries: boolean;
  pip_layout: "stacked" | "pip-corners";
  youtube_preset: boolean;
  padding_preset: "full" | "action" | "highlight" | "custom";
  head_pad_seconds: number;
  tail_pad_seconds: number;
  transition_kind: "none" | "zoom" | "static";
  transition_seconds: number;
  title_page: boolean;
  title_page_seconds: number;
  closing_card: boolean;
  stage_card_style: "none" | "slate" | "lower-third";
  stage_card_seconds: number;
  summary_hold_seconds: number;
  overlay: boolean;
  grid_overlay: boolean;
  grid_hold_seconds: number;
  upload_after_render: boolean;
  upload_privacy: YouTubePrivacy;
  upload_playlist: string | null;
  upload_playlist_id: string | null;
  upload_notify: boolean;
}

export interface ExportPreset {
  preset_id: string;
  name: string;
  builtin: boolean;
  updated_at: string;
  body: ExportPresetBody;
}
```

(`OverlayCodec` and `YouTubePrivacy` are declared earlier in the file; if the interface lands above them, move it below `OverlayCodec` at line ~892.)

Next to `getYouTubeSettings`:

```ts
  // Export presets (spec 2026-09-15 s1): both modes, per user hosted.

  getExportPresets: () => request<{ presets: ExportPreset[] }>("/api/settings/export-presets"),

  /** ``id === "new"`` creates; any other id replaces. Built-in ids 403. */
  putExportPreset: (id: string, name: string, body: ExportPresetBody) =>
    request<ExportPreset>(`/api/settings/export-presets/${encodeURIComponent(id)}`, {
      method: "PUT",
      json: { name, body },
    }),

  deleteExportPreset: (id: string) =>
    request<void>(`/api/settings/export-presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
```

Check how `request<void>` handles a 204 (search for another `method: "DELETE"` call in `api.ts`, e.g. `disconnectYouTube`, and whether `request` parses JSON unconditionally). If it does, make `deleteExportPreset` follow whichever existing DELETE pattern returns no body.

- [ ] **Step 2: Write the failing tests for the pure module**

`lib/exportPresets.test.ts`:

```ts
/**
 * The preset half of the Export form (spec 2026-09-15 s1): what a
 * preset captures, what it never captures, and the last-used store.
 */
import { describe, expect, it } from "vitest";

import type { ExportPresetBody } from "@/lib/api";
import {
  applyBody,
  DEFAULT_EXPORT_SETTINGS,
  groupSummary,
  isDirty,
  loadLastUsed,
  saveLastUsed,
  settingsToBody,
  type ExportSettings,
} from "@/lib/exportPresets";

const YOUTUBE: ExportPresetBody = {
  ...settingsToBody(DEFAULT_EXPORT_SETTINGS),
  output_format: "mp4",
  youtube_preset: true,
  padding_preset: "action",
  head_pad_seconds: 0.5,
  tail_pad_seconds: 1,
  title_page: true,
  closing_card: true,
  stage_card_style: "slate",
  summary_hold_seconds: 3,
  overlay: true,
};

class MemoryStorage {
  map = new Map<string, string>();
  getItem(k: string) {
    return this.map.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.map.set(k, v);
  }
}

describe("settingsToBody / applyBody", () => {
  it("round-trips the defaults", () => {
    expect(settingsToBody(applyBody(DEFAULT_EXPORT_SETTINGS, settingsToBody(DEFAULT_EXPORT_SETTINGS)))).toEqual(
      settingsToBody(DEFAULT_EXPORT_SETTINGS),
    );
  });

  it("round-trips a full body", () => {
    expect(settingsToBody(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE))).toEqual(YOUTUBE);
  });

  it("keeps the match-specific fields the body does not carry", () => {
    const s: ExportSettings = {
      ...DEFAULT_EXPORT_SETTINGS,
      renderOptions: { ...DEFAULT_EXPORT_SETTINGS.renderOptions, titleInfo: "Production Optics" },
      uploadOptions: { ...DEFAULT_EXPORT_SETTINGS.uploadOptions, publishAt: "2026-10-01T18:00" },
    };
    const applied = applyBody(s, YOUTUBE);
    expect(applied.renderOptions.titleInfo).toBe("Production Optics");
    expect(applied.uploadOptions.publishAt).toBe("2026-10-01T18:00");
    expect(settingsToBody(s)).not.toHaveProperty("titleInfo");
    expect(settingsToBody(s)).not.toHaveProperty("publish_at");
  });
});

describe("isDirty", () => {
  it("is false right after apply", () => {
    expect(isDirty(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE), YOUTUBE)).toBe(false);
  });

  it("is true after a recurring field changes", () => {
    const s = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    expect(isDirty({ ...s, headPad: 2 }, YOUTUBE)).toBe(true);
    expect(isDirty({ ...s, includeOverlay: false }, YOUTUBE)).toBe(true);
    expect(isDirty({ ...s, uploadOptions: { ...s.uploadOptions, privacy: "public" } }, YOUTUBE)).toBe(true);
  });

  it("stays false after a match-specific field changes", () => {
    const s = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    expect(isDirty({ ...s, renderOptions: { ...s.renderOptions, titleInfo: "L3" } }, YOUTUBE)).toBe(false);
    expect(isDirty({ ...s, uploadOptions: { ...s.uploadOptions, publishAt: "2026-10-01T18:00" } }, YOUTUBE)).toBe(false);
  });

  it("ignores schema_version and unknown keys on the stored body", () => {
    const stored = { ...YOUTUBE, schema_version: 1, laser_wipe: true } as ExportPresetBody;
    expect(isDirty(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE), stored)).toBe(false);
  });
});

describe("groupSummary", () => {
  const ctx = { secondaryCount: 2, canvasLabel: "1080p (1920x1080) -- faster" };

  it("names the output", () => {
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "output", ctx)).toBe("FCPXML · 2 cams");
    const yt = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    expect(groupSummary(yt, "output", ctx)).toBe("MP4 · YouTube preset · auto codec · 2 cams");
    expect(groupSummary({ ...yt, mode: "trims" }, "output", ctx)).toBe("Lossless trims");
    expect(groupSummary({ ...yt, mode: "compare", canvas: "hd" }, "output", ctx)).toBe("1080p");
  });

  it("names the cut", () => {
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "cut", ctx)).toBe("Full 5.0 / 5.0 s · cut");
    expect(groupSummary({ ...DEFAULT_EXPORT_SETTINGS, transitionKind: "zoom", transitionSeconds: 0.5 }, "cut", ctx)).toBe(
      "Full 5.0 / 5.0 s · zoom 0.5 s",
    );
  });

  it("names the look", () => {
    expect(groupSummary(DEFAULT_EXPORT_SETTINGS, "look", ctx)).toBe("No cards");
    expect(groupSummary(applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE), "look", ctx)).toBe(
      "title page · slate · summary 3 s · closing · overlay",
    );
  });
});

describe("last-used", () => {
  it("round-trips the body and the active preset id", () => {
    const storage = new MemoryStorage();
    const s = applyBody(DEFAULT_EXPORT_SETTINGS, YOUTUBE);
    saveLastUsed(storage, s, "builtin:youtube");
    expect(loadLastUsed(storage)).toEqual({ body: YOUTUBE, presetId: "builtin:youtube" });
  });

  it("returns null on a missing, broken or throwing store", () => {
    expect(loadLastUsed(new MemoryStorage())).toBeNull();
    const broken = new MemoryStorage();
    broken.setItem("splitsmith.export.lastUsed", "{nope");
    expect(loadLastUsed(broken)).toBeNull();
    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(loadLastUsed(throwing)).toBeNull();
    expect(() => saveLastUsed(throwing, DEFAULT_EXPORT_SETTINGS, null)).not.toThrow();
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/exportPresets.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 4: Write the module**

`lib/exportPresets.ts`:

```ts
/**
 * The preset half of the Export form (spec 2026-09-15 s1). Pure.
 *
 * ``ExportSettings`` is every recurring field the page holds, in one
 * object, so a preset can be applied and compared without touching
 * sixteen setters. Two of its members carry a match-specific field the
 * body never stores: ``renderOptions.titleInfo`` and
 * ``uploadOptions.publishAt``. ``applyBody`` keeps them; ``settingsToBody``
 * drops them; ``isDirty`` therefore ignores them.
 */
import type { ExportPresetBody, OverlayCodec } from "@/lib/api";
import { DEFAULT_CAM_OPTIONS, type CamOptions } from "@/lib/camOptions";
import type { ExportMode } from "@/lib/exportPlan";
import { DEFAULT_RENDER_OPTIONS, describeRenderOptions, type OutputFormat, type RenderOptions } from "@/lib/renderOptions";
import { DEFAULT_UPLOAD_OPTIONS, type UploadFormOptions } from "@/lib/youtubeRows";

export type PaddingPreset = ExportPresetBody["padding_preset"];
export type TransitionKind = ExportPresetBody["transition_kind"];
export type CanvasId = ExportPresetBody["canvas"];

export const PADDING_PRESETS: Record<Exclude<PaddingPreset, "custom">, { label: string; head: number; tail: number }> = {
  full: { label: "Full", head: 5.0, tail: 5.0 },
  action: { label: "Action", head: 0.5, tail: 1.0 },
  highlight: { label: "Highlight", head: 1.5, tail: 2.0 },
};

export const TRANSITIONS: { value: TransitionKind; label: string }[] = [
  { value: "none", label: "Hard cut" },
  { value: "static", label: "Static frame" },
  { value: "zoom", label: "Zoom blur" },
];

export const FORMAT_LABELS: Record<OutputFormat, string> = { fcpxml: "FCPXML", fcp7xml: "FCP 7 XML", mp4: "MP4" };

/** The sentinel id the API creates under, and the row's "edited" state. */
export const NEW_PRESET_ID = "new";
export const CUSTOM = "custom";
export const LAST_USED_KEY = "splitsmith.export.lastUsed";

export interface ExportSettings {
  mode: ExportMode;
  outputFormat: OutputFormat;
  overlayCodec: OverlayCodec;
  canvas: CanvasId;
  camOptions: CamOptions;
  youtube: boolean;
  paddingPreset: PaddingPreset;
  headPad: number;
  tailPad: number;
  transitionKind: TransitionKind;
  transitionSeconds: number;
  /** ``titleInfo`` inside is match-specific and never stored. */
  renderOptions: RenderOptions;
  includeOverlay: boolean;
  gridOverlay: boolean;
  gridHoldSeconds: number;
  /** ``publishAt`` inside is match-specific and never stored. */
  uploadOptions: UploadFormOptions;
}

export const DEFAULT_EXPORT_SETTINGS: ExportSettings = {
  mode: "single",
  outputFormat: "fcpxml",
  overlayCodec: "auto",
  canvas: "uhd",
  camOptions: DEFAULT_CAM_OPTIONS,
  youtube: false,
  paddingPreset: "full",
  headPad: PADDING_PRESETS.full.head,
  tailPad: PADDING_PRESETS.full.tail,
  transitionKind: "none",
  transitionSeconds: 0.5,
  renderOptions: DEFAULT_RENDER_OPTIONS,
  includeOverlay: false,
  gridOverlay: false,
  gridHoldSeconds: 0,
  uploadOptions: DEFAULT_UPLOAD_OPTIONS,
};

export function settingsToBody(s: ExportSettings): ExportPresetBody {
  return {
    mode: s.mode,
    output_format: s.outputFormat,
    overlay_codec: s.overlayCodec,
    canvas: s.canvas,
    include_secondaries: s.camOptions.includeSecondaries,
    pip_layout: s.camOptions.pipLayout,
    youtube_preset: s.youtube,
    padding_preset: s.paddingPreset,
    head_pad_seconds: s.headPad,
    tail_pad_seconds: s.tailPad,
    transition_kind: s.transitionKind,
    transition_seconds: s.transitionSeconds,
    title_page: s.renderOptions.titlePage,
    title_page_seconds: s.renderOptions.titlePageDurationSeconds,
    closing_card: s.renderOptions.closingCard,
    stage_card_style: s.renderOptions.stageCardStyle,
    stage_card_seconds: s.renderOptions.stageCardDurationSeconds,
    summary_hold_seconds: s.renderOptions.summaryHoldSeconds,
    overlay: s.includeOverlay,
    grid_overlay: s.gridOverlay,
    grid_hold_seconds: s.gridHoldSeconds,
    upload_after_render: s.uploadOptions.enabled,
    upload_privacy: s.uploadOptions.privacy,
    upload_playlist: s.uploadOptions.playlist,
    upload_playlist_id: s.uploadOptions.playlistId,
    upload_notify: s.uploadOptions.notifySubscribers,
  };
}

/** Write a body into the settings, keeping the match-specific fields. */
export function applyBody(s: ExportSettings, body: ExportPresetBody): ExportSettings {
  return {
    mode: body.mode,
    outputFormat: body.output_format,
    overlayCodec: body.overlay_codec,
    canvas: body.canvas,
    camOptions: { includeSecondaries: body.include_secondaries, pipLayout: body.pip_layout },
    youtube: body.youtube_preset,
    paddingPreset: body.padding_preset,
    headPad: body.head_pad_seconds,
    tailPad: body.tail_pad_seconds,
    transitionKind: body.transition_kind,
    transitionSeconds: body.transition_seconds,
    renderOptions: {
      ...s.renderOptions,
      titlePage: body.title_page,
      titlePageDurationSeconds: body.title_page_seconds,
      closingCard: body.closing_card,
      stageCardStyle: body.stage_card_style,
      stageCardDurationSeconds: body.stage_card_seconds,
      summaryHoldSeconds: body.summary_hold_seconds,
    },
    includeOverlay: body.overlay,
    gridOverlay: body.grid_overlay,
    gridHoldSeconds: body.grid_hold_seconds,
    uploadOptions: {
      ...s.uploadOptions,
      enabled: body.upload_after_render,
      privacy: body.upload_privacy,
      playlist: body.upload_playlist,
      playlistId: body.upload_playlist_id,
      notifySubscribers: body.upload_notify,
    },
  };
}

/** Field-wise equality over the body's own keys; ``schema_version`` and
 *  anything the SPA does not know are ignored on both sides. */
export function bodiesEqual(a: ExportPresetBody, b: ExportPresetBody): boolean {
  const keys = Object.keys(settingsToBody(DEFAULT_EXPORT_SETTINGS)) as (keyof ExportPresetBody)[];
  return keys.every((k) => a[k] === b[k]);
}

export function isDirty(s: ExportSettings, body: ExportPresetBody): boolean {
  return !bodiesEqual(settingsToBody(s), body);
}

export type SettingsGroup = "output" | "cut" | "look";

export interface SummaryContext {
  /** Synced secondary cameras on the selection (the cams line shows only with some). */
  secondaryCount: number;
  canvasLabel: string;
}

const CODEC_LABELS: Record<OverlayCodec, string> = { auto: "auto codec", "hevc-alpha": "HEVC", "prores-4444": "ProRes 4444" };

/** The one line a closed group shows in its header. */
export function groupSummary(s: ExportSettings, group: SettingsGroup, ctx: SummaryContext): string {
  switch (group) {
    case "output": {
      if (s.mode === "trims") return "Lossless trims";
      if (s.mode === "compare") return s.canvas === "hd" ? "1080p" : "4K";
      const parts = [FORMAT_LABELS[s.outputFormat]];
      if (s.outputFormat === "mp4" && s.youtube) parts.push("YouTube preset");
      if (s.includeOverlay) parts.push(CODEC_LABELS[s.overlayCodec]);
      if (ctx.secondaryCount > 0) {
        parts.push(s.camOptions.includeSecondaries ? `${ctx.secondaryCount} cams` : "primary only");
      }
      return parts.join(" · ");
    }
    case "cut": {
      const pad = s.paddingPreset === "custom" ? "Custom" : PADDING_PRESETS[s.paddingPreset].label;
      const transition =
        s.transitionKind === "none" ? "cut" : `${s.transitionKind} ${s.transitionSeconds.toFixed(1)} s`;
      return `${pad} ${s.headPad.toFixed(1)} / ${s.tailPad.toFixed(1)} s · ${transition}`;
    }
    case "look": {
      const grid = s.mode === "compare";
      const parts: string[] = [];
      const cards = describeRenderOptions(s.renderOptions, grid ? "grid" : "single", grid ? "mp4" : s.outputFormat);
      if (cards) parts.push(cards);
      if (grid ? s.gridOverlay : s.includeOverlay) parts.push("overlay");
      return parts.length > 0 ? parts.join(" · ") : "No cards";
    }
  }
}

export interface LastUsed {
  body: ExportPresetBody;
  presetId: string | null;
}

/** The subset of ``Storage`` the page needs; tests pass a Map. */
export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function saveLastUsed(storage: KeyValueStorage, s: ExportSettings, presetId: string | null): void {
  try {
    storage.setItem(LAST_USED_KEY, JSON.stringify({ body: settingsToBody(s), presetId }));
  } catch {
    // Private mode or blocked storage: the page works without it.
  }
}

export function loadLastUsed(storage: KeyValueStorage): LastUsed | null {
  try {
    const raw = storage.getItem(LAST_USED_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LastUsed>;
    if (!parsed || typeof parsed !== "object" || !parsed.body || typeof parsed.body !== "object") return null;
    // Fill anything a newer field added since the entry was written.
    const body = { ...settingsToBody(DEFAULT_EXPORT_SETTINGS), ...parsed.body };
    return { body, presetId: typeof parsed.presetId === "string" ? parsed.presetId : null };
  } catch {
    return null;
  }
}
```

Check `describeRenderOptions` output for the YouTube body: the test expects `"title page · slate · summary 3 s · closing · overlay"`; the function pushes in the order title page, slate, summary, closing (verified at `lib/renderOptions.ts:150-166`). If `clampSeconds(3, 0)` prints `3` the string matches; if it prints `3.0`, change the test expectation to what the function prints, not the function.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/exportPresets.test.ts && pnpm typecheck && pnpm lint`
Expected: PASS, no type or lint errors.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/exportPresets.ts src/splitsmith/ui_static/src/lib/exportPresets.test.ts
git commit -m "feat(ui): export preset types, form settings model and last-used"
```

---

### Task 5: Collapsible `Section` and the `PresetRow`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/export/Section.tsx`
- Create: `src/splitsmith/ui_static/src/components/export/PresetRow.tsx`
- Test: `src/splitsmith/ui_static/src/components/export/PresetRow.test.tsx`

**Interfaces:**
- Produces: `Section({ label, aside, control, flush, summary, open, onToggle, children })`: when `onToggle` is given the header is a toggle button, `summary` shows in muted `text-sm` while closed and the children are not rendered. `PresetRow({ presets, activeId, dirty, busy, onApply, onSave, onSaveAs, onRename, onDelete })`.

- [ ] **Step 1: Move `Section` out of the page and add collapse**

Cut `Section` from `pages/Export.tsx` (lines ~1129-1151) into `components/export/Section.tsx`:

```tsx
/**
 * Section -- one bordered group on the Export page. With `onToggle` the
 * header is a disclosure: the label, the control and, while closed, the
 * group's one-line summary stay visible and the rows fold away
 * (spec 2026-09-15 s1). Without it the group is always open, as the
 * Stages and Details groups are.
 */
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { Label } from "@/components/ui/Label";
import { cn } from "@/lib/utils";

export interface SectionProps {
  label: string;
  aside?: ReactNode;
  control?: ReactNode;
  /** No inner padding: the child brings its own rows. */
  flush?: boolean;
  /** One line shown in the header while closed. */
  summary?: string;
  open?: boolean;
  onToggle?: () => void;
  children?: ReactNode;
}

export function Section({ label, aside, control, flush = false, summary, open = true, onToggle, children }: SectionProps) {
  const collapsible = onToggle !== undefined;
  const showChildren = children && (!collapsible || open);
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <section className="rounded-[10px] border border-rule bg-surface">
      <div className={cn("flex flex-wrap items-center gap-3 px-3.5 py-2", showChildren ? "border-b border-rule" : null)}>
        {collapsible ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={onToggle}
            className="inline-flex items-center gap-1.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            <Icon className="size-3.5 text-muted" aria-hidden />
            <Label>{label}</Label>
          </button>
        ) : (
          <Label>{label}</Label>
        )}
        {control}
        {collapsible && !open && summary ? <span className="text-sm text-muted">{summary}</span> : null}
        {aside ? <span className="ml-auto">{aside}</span> : null}
      </div>
      {showChildren ? <div className={cn(flush ? "[&>div]:rounded-none [&>div]:border-0" : null)}>{children}</div> : null}
    </section>
  );
}
```

Add `import { Section } from "@/components/export/Section";` to the page and delete the local definition. Run `pnpm vitest run src/pages` to confirm the existing Export tests still pass before going on.

- [ ] **Step 2: Write the failing PresetRow test**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PresetRow } from "@/components/export/PresetRow";
import type { ExportPreset } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, settingsToBody } from "@/lib/exportPresets";

function preset(id: string, name: string, builtin = false): ExportPreset {
  return { preset_id: id, name, builtin, updated_at: "2026-09-15T00:00:00Z", body: settingsToBody(DEFAULT_EXPORT_SETTINGS) };
}

const PRESETS = [preset("builtin:final-cut", "Final Cut bundle", true), preset("p1", "Club night")];

function setup(over: Partial<React.ComponentProps<typeof PresetRow>> = {}) {
  const props = {
    presets: PRESETS,
    activeId: "builtin:final-cut",
    dirty: false,
    busy: false,
    onApply: vi.fn(),
    onSave: vi.fn(),
    onSaveAs: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    ...over,
  };
  render(<PresetRow {...props} />);
  return { ...props, user: userEvent.setup() };
}

describe("PresetRow", () => {
  it("lists every preset and marks the active one", () => {
    setup();
    const group = screen.getByRole("group", { name: "Preset" });
    expect(within(group).getByRole("button", { name: "Final Cut bundle" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Club night" })).toHaveAttribute("aria-pressed", "false");
    expect(within(group).queryByRole("button", { name: /custom/i })).toBeNull();
  });

  it("applies on click", async () => {
    const { user, onApply } = setup();
    await user.click(screen.getByRole("button", { name: "Club night" }));
    expect(onApply).toHaveBeenCalledWith("p1");
  });

  it("shows Custom, pressed, when the form is dirty", () => {
    setup({ dirty: true });
    expect(screen.getByRole("button", { name: "Custom" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Final Cut bundle" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/from Final Cut bundle/)).toBeInTheDocument();
  });

  it("offers Save as on a built-in and Save, Rename, Delete on an own preset", async () => {
    const { user, onSaveAs } = setup({ dirty: true });
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    expect(screen.getByRole("menuitem", { name: "Save as..." })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: "Save as..." }));
    expect(onSaveAs).toHaveBeenCalled();
  });

  it("on an own preset every action is offered", async () => {
    const { user, onSave, onDelete } = setup({ activeId: "p1", dirty: true });
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith("p1");
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("p1");
  });

  it("Save is disabled while clean", async () => {
    const { user } = setup({ activeId: "p1", dirty: false });
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    expect(screen.getByRole("menuitem", { name: "Save" })).toBeDisabled();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/PresetRow.test.tsx`
Expected: FAIL, module not found.

- [ ] **Step 4: Write PresetRow**

```tsx
/**
 * PresetRow -- the Export page's preset picker (spec 2026-09-15 s1):
 * the built-ins, the user's own, and "Custom" once the form differs
 * from the active one. One `Segmented` (a closed choice) and a menu
 * for the actions. Red stays with the page's Export button.
 */
import { MoreHorizontal } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { Segmented } from "@/components/ui/Segmented";
import type { ExportPreset } from "@/lib/api";
import { CUSTOM } from "@/lib/exportPresets";

export interface PresetRowProps {
  presets: ExportPreset[];
  /** The preset the form was last set from; null before any was applied. */
  activeId: string | null;
  dirty: boolean;
  busy: boolean;
  onApply: (id: string) => void;
  onSave: (id: string) => void;
  onSaveAs: () => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
}

export function PresetRow({ presets, activeId, dirty, busy, onApply, onSave, onSaveAs, onRename, onDelete }: PresetRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const active = presets.find((p) => p.preset_id === activeId) ?? null;
  const own = active !== null && !active.builtin;
  const showCustom = dirty || active === null;
  const options = [
    ...presets.map((p) => ({ value: p.preset_id, label: p.name })),
    ...(showCustom ? [{ value: CUSTOM, label: "Custom" }] : []),
  ];
  const value = showCustom ? CUSTOM : active!.preset_id;

  return (
    <div className="flex flex-wrap items-center gap-3 px-1">
      <Label>Preset</Label>
      <Segmented
        label="Preset"
        value={value}
        onChange={(id) => {
          if (id !== CUSTOM) onApply(id);
        }}
        options={options}
        disabled={busy}
      />
      {showCustom && active ? <span className="text-sm text-muted">from {active.name}</span> : null}
      <div className="relative ml-auto">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Preset actions"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
          disabled={busy}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </Button>
        <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="right">
          {own ? (
            <button
              type="button"
              role="menuitem"
              className={menuItemClass}
              disabled={!dirty}
              onClick={() => {
                setMenuOpen(false);
                onSave(active.preset_id);
              }}
            >
              Save
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            className={menuItemClass}
            onClick={() => {
              setMenuOpen(false);
              onSaveAs();
            }}
          >
            Save as...
          </button>
          {own ? (
            <>
              <button
                type="button"
                role="menuitem"
                className={menuItemClass}
                onClick={() => {
                  setMenuOpen(false);
                  onRename(active.preset_id);
                }}
              >
                Rename
              </button>
              <button
                type="button"
                role="menuitem"
                className={menuItemClass}
                onClick={() => {
                  setMenuOpen(false);
                  onDelete(active.preset_id);
                }}
              >
                Delete
              </button>
            </>
          ) : null}
        </Menu>
      </div>
    </div>
  );
}
```

Check `Button` accepts `size="icon"` and `variant="ghost"` (it does in `CoverageMatrix.tsx:140-148`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/components/export/PresetRow.test.tsx src/pages && pnpm typecheck && pnpm lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/components/export/Section.tsx src/splitsmith/ui_static/src/components/export/PresetRow.tsx src/splitsmith/ui_static/src/components/export/PresetRow.test.tsx src/splitsmith/ui_static/src/pages/Export.tsx
git commit -m "feat(ui): collapsible export Section and the PresetRow"
```

---

### Task 6: The page on `ExportSettings`, the preset row, last-used and the four groups

This is the large task. It is one task because the settings-object refactor, the preset row and the regrouping all touch the same JSX and cannot be reviewed apart: a preset row over sixteen hooks is not testable, and groups without the row have no summary to fold to.

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx`
- Create: `src/splitsmith/ui_static/src/components/export/OutputGroup.tsx`, `CutGroup.tsx`, `LookGroup.tsx`, `DetailsGroup.tsx`, `SavePresetSheet.tsx`
- Test: `src/splitsmith/ui_static/src/pages/Export.presets.test.tsx`

**Interfaces:**
- Consumes: Task 4's `ExportSettings` and functions, Task 5's `Section` and `PresetRow`, `api.getExportPresets / putExportPreset / deleteExportPreset`.
- Every group component takes `settings: ExportSettings`, `patch: (p: Partial<ExportSettings>) => void`, `busy: boolean`, plus only what its rows need (listed per component below).

- [ ] **Step 1: Write the failing page test**

`pages/Export.presets.test.tsx`. Copy the harness (the `vi.mock`, `shooter`, `video`, `stage`, `PROJECT`, `ready`, `OVERVIEW`, `job`, `Shell`, `renderPage`, `choice` helpers) verbatim from `pages/Export.renderOptions.test.tsx` lines 7-200, then add `getExportPresets`, `putExportPreset` and `deleteExportPreset` to the mocked `api` object and these tests:

```tsx
import { BUILTIN_PRESETS_FOR_TESTS } from "./Export.presets.fixtures";
// (define the fixture inline instead if you prefer: four ExportPreset
// objects whose bodies match the Python built-ins in Task 1; the page
// treats what the API returns as truth, so the fixture only needs the
// ids, names and the bodies the assertions below read.)

beforeEach(() => {
  vi.mocked(api.getProject).mockResolvedValue(PROJECT);
  vi.mocked(api.getExportOverview).mockResolvedValue(OVERVIEW);
  vi.mocked(api.getExportPresets).mockResolvedValue({ presets: BUILTIN_PRESETS_FOR_TESTS });
  vi.mocked(api.putExportPreset).mockImplementation(async (id, name, body) => ({
    preset_id: id === "new" ? "p-new" : id,
    name,
    builtin: false,
    updated_at: "2026-09-15T00:00:00Z",
    body,
  }));
  vi.mocked(api.deleteExportPreset).mockResolvedValue(undefined);
  window.localStorage.clear();
});
afterEach(() => vi.clearAllMocks());

describe("Export presets", () => {
  it("opens on the built-ins with Final Cut bundle active and the groups closed", async () => {
    await renderPage();
    expect(choice("Preset", "Final Cut bundle")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Output/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /^Cut/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /^Look/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Full 5.0 / 5.0 s · cut")).toBeInTheDocument();
  });

  it("applying the YouTube preset changes the form and the request body", async () => {
    const { user } = await renderPage();
    vi.mocked(api.exportMatch).mockResolvedValue(job({ status: "running" }));
    vi.mocked(api.pollJob).mockResolvedValue(job({ status: "succeeded" }));
    await user.click(choice("Preset", "YouTube match video"));
    expect(screen.getByText(/MP4 · YouTube preset/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /export bundle/i }));
    await waitFor(() => expect(api.exportMatch).toHaveBeenCalled());
    const body = vi.mocked(api.exportMatch).mock.calls[0][1];
    expect(body.output_format).toBe("mp4");
    expect(body.youtube_preset).toBe(true);
    expect(body.head_pad_seconds).toBe(0.5);
    expect(body.title_page).toBe(true);
    expect(body.include_overlay).toBe(true);
  });

  it("editing a field shows Custom, from the preset it started on", async () => {
    const { user } = await renderPage();
    await user.click(screen.getByRole("button", { name: /^Cut/ }));
    await user.click(choice("Trim padding", "Action"));
    expect(choice("Preset", "Custom")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("from Final Cut bundle")).toBeInTheDocument();
  });

  it("editing a match-specific field does not dirty the preset", async () => {
    const { user } = await renderPage();
    await user.clear(screen.getByLabelText("Bundle name"));
    await user.type(screen.getByLabelText("Bundle name"), "cut-1");
    expect(screen.queryByRole("button", { name: "Custom" })).toBeNull();
  });

  it("Save as... stores the current body under a new name and selects it", async () => {
    const { user } = await renderPage();
    await user.click(screen.getByRole("button", { name: /^Cut/ }));
    await user.click(choice("Trim padding", "Highlight"));
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Save as..." }));
    const dialog = screen.getByRole("dialog", { name: "Save preset" });
    await user.type(within(dialog).getByLabelText("Name"), "Club night");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putExportPreset).toHaveBeenCalledWith("new", "Club night", expect.objectContaining({ padding_preset: "highlight" })));
    await waitFor(() => expect(choice("Preset", "Club night")).toHaveAttribute("aria-pressed", "true"));
    expect(screen.queryByRole("button", { name: "Custom" })).toBeNull();
  });

  it("restores last-used on reload", async () => {
    const { user } = await renderPage();
    await user.click(choice("Preset", "YouTube match video"));
    await user.click(screen.getByRole("button", { name: /^Cut/ }));
    await user.click(choice("Trim padding", "Highlight"));
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem("splitsmith.export.lastUsed")!).body.padding_preset).toBe("highlight"));
    cleanup();
    await renderPage();
    expect(choice("Preset", "Custom")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("from YouTube match video")).toBeInTheDocument();
    expect(screen.getByText(/Highlight 1\.5 \/ 2\.0 s/)).toBeInTheDocument();
  });

  it("a failed preset load still renders the page with Custom only", async () => {
    vi.mocked(api.getExportPresets).mockRejectedValue(new Error("offline"));
    await renderPage();
    expect(choice("Preset", "Custom")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /export bundle/i })).toBeEnabled();
  });
});
```

Import `cleanup` from `@testing-library/react`. `choice(group, label)` is the helper from the copied harness.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/pages/Export.presets.test.tsx`
Expected: FAIL (no "Preset" group).

- [ ] **Step 3: Replace the recurring hooks with one `settings` object**

In `pages/Export.tsx`, delete these hooks (lines ~183-211) and the local `PaddingPreset`, `PADDING_PRESETS`, `TransitionKind`, `TRANSITIONS` declarations (lines ~82-98): `mode, preset, headPad, tailPad, transitionKind, transitionDurationSeconds, includeOverlay, overlayCodec, outputFormat, renderOptions, camOptions, youtube, uploadOptions, canvas, gridOverlay, gridHoldSeconds`. Keep `selection`, `projectName`, `descriptionLead`, `audioFrom` (match-specific).

Add:

```tsx
  // The recurring half of the form is one object so a preset applies and
  // compares as a unit (lib/exportPresets). Match-specific fields stay
  // separate below and are never stored.
  const [settings, setSettings] = useState<ExportSettings>(() => {
    const last = loadLastUsed(window.localStorage);
    return last ? applyBody(DEFAULT_EXPORT_SETTINGS, last.body) : DEFAULT_EXPORT_SETTINGS;
  });
  const patch = useCallback((p: Partial<ExportSettings>) => setSettings((s) => ({ ...s, ...p })), []);
  const {
    mode,
    outputFormat,
    overlayCodec,
    camOptions,
    youtube,
    paddingPreset,
    headPad,
    tailPad,
    transitionKind,
    transitionSeconds,
    renderOptions,
    includeOverlay,
    gridOverlay,
    gridHoldSeconds,
    uploadOptions,
  } = settings;
  const canvas = CANVAS_CHOICES.find((c) => c.id === settings.canvas) ?? CANVAS_CHOICES[0];

  // Presets. Built-ins come from the server too; a failed load leaves the
  // row with Custom alone and the page fully usable.
  const [presets, setPresets] = useState<ExportPreset[]>([]);
  const [activePresetId, setActivePresetId] = useState<string | null>(
    () => loadLastUsed(window.localStorage)?.presetId ?? null,
  );
  const [saveSheet, setSaveSheet] = useState<{ mode: "saveAs" | "rename"; id?: string } | null>(null);
  const [openGroups, setOpenGroups] = useState<Record<SettingsGroup, boolean>>({ output: false, cut: false, look: false });
  const toggleGroup = (g: SettingsGroup) => setOpenGroups((o) => ({ ...o, [g]: !o[g] }));

  useEffect(() => {
    let cancelled = false;
    api
      .getExportPresets()
      .then((r) => {
        if (!cancelled) setPresets(r.presets);
      })
      .catch(() => {
        if (!cancelled) setPresets([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // First visit, nothing remembered: start on the first built-in.
  useEffect(() => {
    if (activePresetId === null && presets.length > 0 && !loadLastUsed(window.localStorage)) {
      setSettings((s) => applyBody(s, presets[0].body));
      setActivePresetId(presets[0].preset_id);
    }
  }, [presets, activePresetId]);

  const activePreset = presets.find((p) => p.preset_id === activePresetId) ?? null;
  const dirty = activePreset ? isDirty(settings, activePreset.body) : true;

  // Last-used follows every change, debounced; the id it names may be a
  // preset the form has since diverged from, which the row shows as Custom.
  useEffect(() => {
    const t = window.setTimeout(() => saveLastUsed(window.localStorage, settings, activePresetId), 300);
    return () => window.clearTimeout(t);
  }, [settings, activePresetId]);

  function applyPreset(id: string) {
    const p = presets.find((x) => x.preset_id === id);
    if (!p) return;
    setSettings((s) => applyBody(s, p.body));
    setActivePresetId(id);
    setOpenGroups({ output: false, cut: false, look: false });
    if (p.body.mode === "compare" && !multiShooter) return;
    if (p.body.mode !== mode) setSelection(new Set());
  }

  async function savePreset(id: string, name: string) {
    try {
      const saved = await api.putExportPreset(id, name, settingsToBody(settings));
      setPresets((list) => {
        const rest = list.filter((p) => p.preset_id !== saved.preset_id);
        const own = [...rest.filter((p) => !p.builtin), saved].sort((a, b) => a.name.localeCompare(b.name));
        return [...rest.filter((p) => p.builtin), ...own];
      });
      setActivePresetId(saved.preset_id);
      setSaveSheet(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function deletePreset(id: string) {
    const p = presets.find((x) => x.preset_id === id);
    if (!p) return;
    if (!(await confirm({ title: `Delete preset "${p.name}"?`, confirmLabel: "Delete", destructive: true }))) return;
    try {
      await api.deleteExportPreset(id);
      setPresets((list) => list.filter((x) => x.preset_id !== id));
      if (activePresetId === id) setActivePresetId(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
```

Check `useConfirm`'s option names against `components/useConfirm.tsx` and use its real signature. `multiShooter` is declared a few lines below in the current file; move its declaration above `applyPreset` (it only depends on `shooters`).

Then rename every setter use in the page to a `patch` call. The mapping:

| old | new |
|---|---|
| `setMode(v)` / `selectMode` body | `patch({ mode: v })` (keep `selectMode`'s selection reset) |
| `setPreset` / `selectPreset(next)` | `patch(next === "custom" ? { paddingPreset: next } : { paddingPreset: next, headPad: PADDING_PRESETS[next].head, tailPad: PADDING_PRESETS[next].tail })` |
| `setHeadPad(v)` / `setTailPad(v)` | `patch({ headPad: v })` / `patch({ tailPad: v })` |
| `setTransitionKind(v)` | `patch({ transitionKind: v })` |
| `setTransitionDurationSeconds(v)` | `patch({ transitionSeconds: v })` |
| `setIncludeOverlay(v)` | `patch({ includeOverlay: v })` |
| `setOverlayCodec(v)` | `patch({ overlayCodec: v })` |
| `setOutputFormat(v)` | `patch({ outputFormat: v })` |
| `setRenderOptions(v)` | `patch({ renderOptions: v })` |
| `setCamOptions(v)` | `patch({ camOptions: v })` |
| `setYoutube(v)` | `patch({ youtube: v })` |
| `setUploadOptions(v)` | `patch({ uploadOptions: v })` |
| `setCanvas(choice)` | `patch({ canvas: choice.id })` |
| `setGridOverlay(v)` | `patch({ gridOverlay: v })` |
| `setGridHoldSeconds(v)` | `patch({ gridHoldSeconds: v })` |
| `transitionDurationSeconds` (reads) | `transitionSeconds` |
| `preset` (padding, reads) | `paddingPreset` |

Import from `@/lib/exportPresets`: `applyBody, DEFAULT_EXPORT_SETTINGS, groupSummary, isDirty, loadLastUsed, PADDING_PRESETS, saveLastUsed, settingsToBody, TRANSITIONS, type ExportSettings, type PaddingPreset, type SettingsGroup, type TransitionKind`; and `type ExportPreset` from `@/lib/api`. The `submitBundle` body keeps its field names; only `transition_duration_seconds: transitionSeconds` changes.

Run `pnpm typecheck` until clean, then `pnpm vitest run src/pages` to confirm the six existing Export tests still pass (their `api` mock lacks `getExportPresets`; `vi.fn()` returns `undefined`, `.then` on it throws inside the effect: guard with `Promise.resolve(api.getExportPresets()).then(...)` so a non-promise mock is tolerated, and the `.catch` leaves `presets` empty).

- [ ] **Step 4: Write the four group components and the save sheet**

Each group is the JSX it takes over, moved verbatim from the page, with the page's locals replaced by props. Signatures:

`OutputGroup.tsx` (takes the current Output section's `trimsOnly` grid-camera Field, the compare `Reference` and `Canvas` Fields, the single-mode `Format` Field, the overlay codec `SelectField` (shown when `settings.includeOverlay`), the `YouTube` on/off `Segmented` (shown when `outputFormat === "mp4"`), and `CamOptionsPanel`):

```tsx
export interface OutputGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  editDenied: boolean;
  shooters: ShooterListEntry[];
  audioFrom: string;
  onAudioFrom: (slug: string) => void;
  cameraOptions: { value: string; label: string }[];
  compareCamera: string;
  onChangeCamera: (v: string) => void;
  secondaryCount: number;
  bareSelected: number;
}
```

`CutGroup.tsx` (the `Padding` and `Transition` Fields plus `NumInput`, which moves here from the page):

```tsx
export interface CutGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
}
```

`LookGroup.tsx` (`RenderOptionsPanel` for the mode's surface, the `Overlay` on/off `Segmented` for single, the grid `Overlay` + hold for compare):

```tsx
export interface LookGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareSelected: number;
}
```

`DetailsGroup.tsx` (the `Bundle name` Field, and when `outputFormat === "mp4" && youtube`: the `Description lead` textarea and `YouTubeConnect`):

```tsx
export interface DetailsGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  hosted: boolean;
  projectName: string;
  onProjectName: (v: string) => void;
  exportsDir: string | null;
  descriptionLead: string;
  onDescriptionLead: (v: string) => void;
  youtubeSettings: YouTubeSettings | null;
  onYouTubeSettingsChange: () => void;
  matchName: string;
  bareSelected: number;
}
```

`SavePresetSheet.tsx`:

```tsx
/** Name a preset (Save as...) or rename one. A `Sheet` with one field. */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Sheet } from "@/components/ui/Sheet";
import { cn } from "@/lib/utils";

export interface SavePresetSheetProps {
  open: boolean;
  title: string;
  initialName: string;
  onClose: () => void;
  onSubmit: (name: string) => void;
}

export function SavePresetSheet({ open, title, initialName, onClose, onSubmit }: SavePresetSheetProps) {
  const [name, setName] = useState(initialName);
  const trimmed = name.trim();
  return (
    <Sheet open={open} onClose={onClose} label="Save preset">
      <form
        className="flex flex-col gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed) onSubmit(trimmed);
        }}
      >
        <h2 className="text-base text-ink">{title}</h2>
        <Field label="Name" htmlFor="preset-name">
          <input
            id="preset-name"
            autoFocus
            maxLength={60}
            className={cn(inputClass, "w-full")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!trimmed}>
            Save
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
```

Mount it with `key={saveSheet?.mode + (saveSheet?.id ?? "")}` so `initialName` resets per opening. The Save button here is `default`, not `primary`: the page's one primary is Export.

- [ ] **Step 5: Lay the page out**

In the form column, in this order:

```tsx
          <PresetRow
            presets={presets}
            activeId={activePresetId}
            dirty={dirty}
            busy={busy}
            onApply={applyPreset}
            onSave={(id) => void savePreset(id, activePreset?.name ?? "")}
            onSaveAs={() => setSaveSheet({ mode: "saveAs" })}
            onRename={(id) => setSaveSheet({ mode: "rename", id })}
            onDelete={(id) => void deletePreset(id)}
          />

          {/* Stages: match-specific, always open. */}
          <Section label="Stages" aside={...unchanged...} flush>...unchanged...</Section>

          <Section
            label="Output"
            control={<Segmented<ExportMode> ...the existing mode control... />}
            summary={groupSummary(settings, "output", summaryCtx)}
            open={openGroups.output}
            onToggle={() => toggleGroup("output")}
          >
            <OutputGroup ... />
          </Section>

          {mode === "single" ? (
            <Section label="Cut" summary={groupSummary(settings, "cut", summaryCtx)} open={openGroups.cut} onToggle={() => toggleGroup("cut")}>
              <CutGroup settings={settings} patch={patch} busy={busy} />
            </Section>
          ) : null}

          {mode !== "trims" ? (
            <Section label="Look" summary={groupSummary(settings, "look", summaryCtx)} open={openGroups.look} onToggle={() => toggleGroup("look")}>
              <LookGroup settings={settings} patch={patch} busy={busy} bareSelected={bareSelected} />
            </Section>
          ) : null}

          {mode === "single" ? (
            <Section label="Details">
              <DetailsGroup ... />
            </Section>
          ) : null}

          <ExportHistory ... unchanged ... />
```

with `const summaryCtx = { secondaryCount, canvasLabel: canvas.label };` declared next to `lines`. The mode `Segmented` stays in the Output header so switching mode is one click while the group is folded. The page's `aside` on the Output section (the "FCPXML + CSV + report" line) is dropped: the summary replaces it.

`SavePresetSheet` mounts at the end of the page:

```tsx
      {saveSheet ? (
        <SavePresetSheet
          key={saveSheet.mode + (saveSheet.id ?? "")}
          open
          title={saveSheet.mode === "rename" ? "Rename preset" : "Save preset"}
          initialName={saveSheet.mode === "rename" ? (presets.find((p) => p.preset_id === saveSheet.id)?.name ?? "") : ""}
          onClose={() => setSaveSheet(null)}
          onSubmit={(name) => void savePreset(saveSheet.mode === "rename" ? saveSheet.id! : NEW_PRESET_ID, name)}
        />
      ) : null}
```

(Rename saves the current body under the same id with the new name; that is what the API's PUT does and it is fine because the row's Save is disabled while clean and enabled while dirty, so a rename on a dirty form also saves the edits, which the sheet title should say: use "Rename and save preset" as the title when `dirty`.)

The title line (`renderOptions.titleInfo`) stays inside `RenderOptionsPanel` under Look for this PR; part 2 rebuilds Look and moves it to Details then. Note this in the PR description.

- [ ] **Step 6: Run every Export test, typecheck and lint**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/pages src/components/export src/lib/exportPresets.test.ts && pnpm typecheck && pnpm lint`
Expected: all PASS, including the six pre-existing `Export.*.test.tsx` files. If one of them asserts on the old "Options" section label or the Output aside copy, update that assertion to the new label; do not weaken what it checks.

- [ ] **Step 7: Look at it**

```bash
uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media
uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5174
```

Wait for `/api/health`, open `/match/<match_id>/export/<slug>` (match id from `~/.claude-tmp/demo-match/match.json`, slug from its `shooters`), screenshot with Playwright at 1440 wide and at 400 wide. Check: the preset row reads left to right with the four built-ins; the three groups are closed with a summary each; clicking Cut opens it; picking "Action" flips the row to Custom "from Final Cut bundle"; Save as... opens the sheet; after saving, the new chip is selected and Custom is gone; a reload lands on the same state. Show the screenshots before calling the task done.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Export.tsx src/splitsmith/ui_static/src/pages/Export.presets.test.tsx src/splitsmith/ui_static/src/components/export/
git commit -m "feat(ui): export presets row, last-used and the four option groups"
```

---

### Task 7: Docs, review pass and PR

**Files:**
- Modify: `CLAUDE.md` (a short section after "Hosted playback streams the web rendition")

- [ ] **Step 1: Add the CLAUDE.md note**

```markdown
## Export presets (spec 2026-09-15)

``export_presets.ExportPresetBody`` is the one shape the API, the local
``export_presets.json`` and the SPA's ``localStorage`` last-used entry
share. Every field defaults and ``extra="ignore"`` is set, so a body
saved before a new option shipped loads with that option at its default:
adding an option means adding a defaulted field here, a column in
``lib/exportPresets.settingsToBody`` / ``applyBody``, and nothing else.
Presets are per user (``export_presets`` table hosted, the JSON file
locally) and never a ``state_docs`` kind: a per-match kind would enter
the sync manifest. Match-specific fields (stage selection, the title
line, the description lead, the bundle name, the reference shooter, a
publish date) are never stored; ``isDirty`` ignores them by
construction because ``settingsToBody`` does not emit them. The page's
recurring state is one ``ExportSettings`` object; a new recurring
field goes on it, not on a fresh ``useState``.
```

- [ ] **Step 2: Review pass**

Per the project's review practice, run one review over the branch with these named claims to verify, and treat the implementation as unverified:

- `isDirty` is false after every built-in is applied, on the real built-in bodies the API returns (not the test fixture): run the page against the local server and check the row.
- `applyBody` then `settingsToBody` is the identity on every `ExportPresetBody` field; list any field it drops.
- The existing `Export.*.test.tsx` files still exercise what they claim: for each assertion touched in Task 6 step 6, confirm the new assertion fails against the pre-change page.
- A hosted user B cannot read, overwrite or delete user A's preset through the routes (the test exists; run it and read the assertions).
- The migration's table matches `ExportPresetRow` column for column, including `server_default` and nullability, so the CI gate's "empty diff" passes.

- [ ] **Step 3: Run the targeted Python suites once more and the full SPA suite**

```bash
uv run pytest tests/test_export_presets.py tests/test_export_presets_store.py tests/test_export_presets_api.py tests/test_schema_diff.py tests/test_local_mode_no_hosted_imports.py tests/test_youtube_api.py -n0 -q
cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint
```

- [ ] **Step 4: Commit and open the PR**

```bash
git add CLAUDE.md
git commit -m "docs: export presets note"
git push -u origin HEAD
gh pr create --title "feat(export): presets, last-used and option groups" --body-file - <<'EOF'
Part 1 of the export redesign (spec docs/superpowers/specs/2026-09-15-export-presets-and-look-gallery-design.md).

- `export_presets.ExportPresetBody`: the recurring settings, every field defaulted, unknown fields ignored. Four built-ins in code.
- Local store: `~/.splitsmith/export_presets.json`. Hosted: `export_presets` table (RLS, per user), migration c3e8a1d47f92.
- `GET/PUT/DELETE /api/settings/export-presets`; built-ins 403 on write.
- SPA: the recurring form state is one `ExportSettings` object; preset row (built-ins, own, Custom); Save / Save as / Rename / Delete; last-used restored from localStorage.
- Output / Cut / Look fold with a one-line summary; Stages and Details stay open.

Not in this PR: the Look gallery (part 2) and the real-match preview (part 3). The title line stays inside the cards panel until part 2 rebuilds Look.

Screenshots: (attach the two from Task 6 step 7)
EOF
```

---

## Self-review

**Spec coverage (section 1):** what a preset captures / never captures (Task 4 `settingsToBody`, Task 1 body fields); model with defaults and `extra="ignore"` (Task 1); envelope-skip loader (Task 1 `load_presets_payload`); built-ins immutable, ids fixed (Tasks 1, 3); local JSON store and hosted table with migration, not `state_docs` (Tasks 1, 2); routes GET/PUT/DELETE with `new` id generation and 403 (Task 3); last-used in `localStorage` with a stale id falling to Custom and a broken store tolerated (Tasks 4, 6); preset row on `Segmented` with the actions menu and the sheet (Tasks 5, 6); groups collapsible with summaries, Stages and Details open, mode control in the Output header (Tasks 5, 6); presets never bypass eligibility (nothing in the plan touches `exportRows` or `stageBlock`); modules `lib/exportPresets.ts` and the group components (Tasks 4, 6). Gap accepted and stated: the title line remains in the cards panel until part 2.

**Placeholder scan:** the group components in Task 6 step 4 are described by the JSX they take over and their prop interfaces rather than reproduced, because the JSX exists at the quoted lines of `Export.tsx` and is moved, not written. Everything else is inline.

**Type consistency:** `ExportSettings` field names (`paddingPreset`, `transitionSeconds`, `canvas: CanvasId`) match between Task 4, the Task 6 mapping table and the tests; `groupSummary(settings, group, { secondaryCount, canvasLabel })` matches its call in Task 6; `putExportPreset(id, name, body)` matches Tasks 3, 4 and 6; `NEW_PRESET_ID = "new"` matches the router's `NEW_ID`.
