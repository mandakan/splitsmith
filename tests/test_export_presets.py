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
    assert "skipping" in caplog.text.lower()


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
    assert (yt.output_format, yt.youtube_preset, yt.title_page, yt.stage_card_style) == (
        "mp4",
        True,
        True,
        "slate",
    )
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


def test_json_store_put_replaces_by_id_and_sorts_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_json_store_is_inert_when_user_config_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
