"""``splitsmith.look_store``: the stored Look body, the local folder store and
materializing stored Looks into a Looks folder (issue #1263)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith import look_store, looks
from splitsmith.look_store import FolderLookStore, LookStore, LookStoreError, StoredLookBody


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    return tmp_path / "looks"


def _body(**changes) -> StoredLookBody:
    base = looks.load_look("splitsmith").manifest
    fields = {
        "label": "Club red",
        "base": "splitsmith",
        "colors": dict(base.colors),
        "accent_series": ["#ff0000"],
        "styles": {},
    }
    fields.update(changes)
    return StoredLookBody.model_validate(fields)


# --- the body ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"colors": {"ink": [1, 2, 3]}}, "colors"),
        ({"accent_series": ["red"]}, "accent_series"),
        ({"styles": {"slate": "Rise!"}}, "styles"),
        ({"base": "Not A Name"}, "base"),
        ({"label": "x" * 61}, "label"),
    ],
)
def test_a_bad_body_names_its_field(changes: dict, field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _body(**changes)
    assert caught.value.errors()[0]["loc"][0] == field


def test_the_body_round_trips_through_a_manifest() -> None:
    body = _body(styles={"slate": "rise"})
    manifest = looks.LookManifest.model_validate(look_store.manifest_for("club", body))
    assert manifest.name == "club" and manifest.styles == {"slate": "rise"} and manifest.slots == {}
    assert look_store.body_from_manifest(manifest) == body


# --- the folder store ------------------------------------------------------------------


def test_the_folder_store_satisfies_the_protocol() -> None:
    typed: LookStore = FolderLookStore()
    assert isinstance(typed, FolderLookStore)


def test_put_makes_a_look_the_renderers_load(user_dir: Path) -> None:
    store = FolderLookStore()
    saved = asyncio.run(store.put("club", _body(styles={"slate": "rise"})))
    assert saved.name == "club"
    look = looks.load_look("club")
    assert look.source == "user" and look.label == "Club red"
    assert looks.template_for(look, "slate") == looks.load_look("splitsmith").own_template("slate", "rise")
    assert [s.name for s in asyncio.run(store.list())] == ["club"]
    got = asyncio.run(store.get("club"))
    assert got is not None and got.body == _body(styles={"slate": "rise"})


def test_put_on_a_hand_made_look_keeps_its_templates_and_unknown_fields(user_dir: Path) -> None:
    from splitsmith import look_tools

    root = look_tools.new_look("mine", starter="still")
    raw = json.loads((root / "look.json").read_text(encoding="utf-8"))
    raw["fonts"] = {"display": "Antonio"}
    raw["made_with"] = "an editor"
    (root / "look.json").write_text(json.dumps(raw), encoding="utf-8")
    asyncio.run(FolderLookStore().put("mine", _body(label="Mine, recoloured")))
    after = json.loads((root / "look.json").read_text(encoding="utf-8"))
    assert after["slots"] == raw["slots"] and after["made_with"] == "an editor"
    assert after["label"] == "Mine, recoloured" and (root / "still.html").is_file()


def test_a_local_look_may_shadow_a_shipped_name(user_dir: Path) -> None:
    asyncio.run(FolderLookStore().put("clean", _body(label="My clean")))
    assert looks.load_look("clean").label == "My clean"


def test_put_refuses_a_bad_name_and_leaves_nothing(user_dir: Path) -> None:
    with pytest.raises(LookStoreError, match="Look name"):
        asyncio.run(FolderLookStore().put("Bad Name", _body()))
    assert not user_dir.exists() or list(user_dir.iterdir()) == []


def test_a_broken_hand_made_look_is_listed_by_neither_get_nor_list(user_dir: Path) -> None:
    (user_dir / "broken").mkdir(parents=True)
    (user_dir / "broken" / "look.json").write_text("{not json", encoding="utf-8")
    store = FolderLookStore()
    assert asyncio.run(store.list()) == [] and asyncio.run(store.get("broken")) is None


def test_put_over_a_broken_look_fails_and_restores_its_file(user_dir: Path) -> None:
    root = user_dir / "broken"
    root.mkdir(parents=True)
    raw = {"name": "broken", "colors": {}, "slots": {"slate": "missing.html"}}
    (root / "look.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(LookStoreError, match="missing.html"):
        asyncio.run(FolderLookStore().put("broken", _body()))
    assert json.loads((root / "look.json").read_text(encoding="utf-8")) == raw


def test_delete_removes_the_folder_and_is_idempotent(user_dir: Path) -> None:
    store = FolderLookStore()
    asyncio.run(store.put("club", _body()))
    asyncio.run(store.delete("club"))
    asyncio.run(store.delete("club"))
    assert not (user_dir / "club").exists()
    with pytest.raises(LookStoreError):
        asyncio.run(store.delete("../escape"))


# --- materialize -------------------------------------------------------------------------


def test_materialize_writes_each_look_once_per_content(tmp_path: Path) -> None:
    stored = [
        look_store.StoredLook(name="club", body=_body(styles={"slate": "rise"})),
        look_store.StoredLook(name="team", body=_body(label="Team")),
    ]
    first = look_store.materialize(stored, tmp_path / "cache")
    assert sorted(p.name for p in first.iterdir()) == ["club", "team"]
    assert look_store.materialize(list(reversed(stored)), tmp_path / "cache") == first
    changed = look_store.materialize(stored[:1], tmp_path / "cache")
    assert changed != first and [p.name for p in changed.iterdir()] == ["club"]
    token = looks.set_user_looks_provider(lambda: first)
    try:
        assert looks.load_look("club").manifest.styles == {"slate": "rise"}
    finally:
        looks.reset_user_looks_provider(token)


def test_materialize_never_writes_a_shipped_name(tmp_path: Path) -> None:
    out = look_store.materialize([look_store.StoredLook(name="clean", body=_body())], tmp_path)
    assert list(out.iterdir()) == []
