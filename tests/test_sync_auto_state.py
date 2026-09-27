"""Local auto-sync state (spec 2026-09-27 s6)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from splitsmith.sync.auto_state import (
    AUTO_SYNC_FILE,
    AutoSyncPrefs,
    auto_sync_effective,
    load_auto_prefs,
    update_auto_prefs,
)
from splitsmith.sync.state import SyncState, local_fingerprint, save_sync_state


def test_missing_or_corrupt_file_loads_defaults(tmp_path: Path) -> None:
    assert load_auto_prefs(tmp_path) == AutoSyncPrefs()
    (tmp_path / AUTO_SYNC_FILE).write_text("{not json", encoding="utf-8")
    assert load_auto_prefs(tmp_path) == AutoSyncPrefs()


def test_update_round_trips(tmp_path: Path) -> None:
    update_auto_prefs(tmp_path, lambda p: setattr(p, "enabled", False))
    update_auto_prefs(tmp_path, lambda p: p.reconcile_failures.update({"trim/me/1/v1": "12.3400"}))
    loaded = load_auto_prefs(tmp_path)
    assert loaded.enabled is False
    assert loaded.reconcile_failures == {"trim/me/1/v1": "12.3400"}


def test_default_is_on_once_synced_and_global_switch_wins() -> None:
    never = SyncState()
    synced = SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC))
    assert auto_sync_effective(AutoSyncPrefs(), never, global_enabled=True) is False
    assert auto_sync_effective(AutoSyncPrefs(), synced, global_enabled=True) is True
    assert auto_sync_effective(AutoSyncPrefs(enabled=False), synced, global_enabled=True) is False
    assert auto_sync_effective(AutoSyncPrefs(), synced, global_enabled=False) is False
    # An explicit on still needs a first manual sync: there is no mirror yet.
    assert auto_sync_effective(AutoSyncPrefs(enabled=True), never, global_enabled=True) is False


def test_toggle_is_not_clobbered_by_sync_state_saves(tmp_path: Path) -> None:
    """run_sync saves sync_state.json from its in-memory copy many times per
    run. The auto flag lives in its own file so those saves cannot undo it."""
    update_auto_prefs(tmp_path, lambda p: setattr(p, "enabled", False))
    save_sync_state(tmp_path, SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC)))
    assert load_auto_prefs(tmp_path).enabled is False


def test_local_fingerprint_counts_and_sums_doc_versions() -> None:
    state = SyncState(doc_versions={"match": 3, "project/me": 2, "audit/me/1": 5})
    assert local_fingerprint(state) == (3, 10)
    assert local_fingerprint(SyncState()) == (0, 0)


def test_full_media_defaults_off_and_round_trips(tmp_path: Path) -> None:
    assert load_auto_prefs(tmp_path).full_media is False
    update_auto_prefs(tmp_path, lambda p: setattr(p, "full_media", True))
    assert load_auto_prefs(tmp_path).full_media is True


def test_a_match_deleted_on_hosted_is_not_watched() -> None:
    synced = SyncState(last_synced_at=datetime(2026, 9, 27, tzinfo=UTC))
    gone = AutoSyncPrefs(hosted_deleted_at=datetime(2026, 9, 27, tzinfo=UTC))
    assert auto_sync_effective(gone, synced, global_enabled=True) is False
