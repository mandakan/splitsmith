"""SyncState v2 (doc_versions) + sync_base/ snapshot store."""

from pathlib import Path

from splitsmith.sync.base import load_base_doc, save_base_doc
from splitsmith.sync.state import SyncState, load_sync_state, save_sync_state


def test_sync_state_v2_roundtrip(tmp_path: Path):
    state = SyncState()
    assert state.schema_version == 2
    state.doc_versions["project/anna"] = 4
    save_sync_state(tmp_path, state)
    loaded = load_sync_state(tmp_path)
    assert loaded.doc_versions == {"project/anna": 4}


def test_sync_state_v1_file_loads_with_empty_versions(tmp_path: Path):
    (tmp_path / "sync_state.json").write_text(
        '{"schema_version": 1, "items": {}, "doc_hashes": {"match": "ab"}}',
        encoding="utf-8",
    )
    loaded = load_sync_state(tmp_path)
    assert loaded.doc_versions == {}
    assert loaded.doc_hashes == {"match": "ab"}


def test_base_doc_roundtrip_and_missing(tmp_path: Path):
    assert load_base_doc(tmp_path, "audit/anna/3") is None
    save_base_doc(tmp_path, "audit/anna/3", {"shots": [1]})
    assert load_base_doc(tmp_path, "audit/anna/3") == {"shots": [1]}
    assert (tmp_path / "sync_base" / "audit" / "anna" / "3.json").exists()
    save_base_doc(tmp_path, "match", {"name": "x"})
    assert (tmp_path / "sync_base" / "match.json").exists()


def test_base_doc_corrupt_reads_as_missing(tmp_path: Path):
    p = tmp_path / "sync_base" / "match.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert load_base_doc(tmp_path, "match") is None


def test_base_doc_key_segment_with_dot_is_not_truncated(tmp_path: Path):
    save_base_doc(tmp_path, "project/j.doe", {"a": 1})
    save_base_doc(tmp_path, "project/j", {"b": 2})
    assert (tmp_path / "sync_base" / "project" / "j.doe.json").exists()
    assert load_base_doc(tmp_path, "project/j.doe") == {"a": 1}
    assert load_base_doc(tmp_path, "project/j") == {"b": 2}
