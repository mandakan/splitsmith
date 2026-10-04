"""``StoragePresence``: existence answered from one listing per prefix, not a HEAD per key (#1180).

``GET /api/match/shooters`` asked storage twice per video angle (source
present? trim cached?), in sequence, through ``_audit_trim_targets``. On
R2 that was two HEAD round trips per angle and 11 s for a 20-stage match.
The index lists a prefix once and answers by set membership, with the
same local-disk-first semantics as ``MatchProject.source_present`` and
``audio.trim_available``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from splitsmith.match_project import MatchProject
from splitsmith.storage import FilesystemStorage, StorageObject
from splitsmith.ui.presence import StoragePresence


class CountingStorage(FilesystemStorage):
    """FilesystemStorage that counts the metadata calls the index is meant to replace."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.exists_calls: list[str] = []
        self.list_calls: list[str] = []
        self.fail_prefixes: set[str] = set()

    def exists(self, path: str) -> bool:
        self.exists_calls.append(path)
        return super().exists(path)

    def list(self, prefix: str) -> Iterator[StorageObject]:
        self.list_calls.append(prefix)
        if prefix in self.fail_prefixes:
            raise OSError(f"listing {prefix!r} failed")
        return super().list(prefix)


SCOPE = "matches/m1/shooters/me"


def _project(tmp_path: Path, storage: FilesystemStorage | None) -> tuple[MatchProject, Path]:
    root = tmp_path / "shooter"
    project = MatchProject.init(root, name="me")
    project.bind_storage(storage, scope=SCOPE if storage is not None else None)
    return project, root


def test_source_present_answers_from_one_listing_of_the_raw_prefix(tmp_path: Path) -> None:
    storage = CountingStorage(tmp_path / "backing")
    storage.write_bytes("raw/A.mp4", b"a")
    storage.write_bytes("raw/B.mp4", b"b")
    project, root = _project(tmp_path, storage)
    presence = StoragePresence(storage)

    assert presence.source_present(project, root, Path("raw/A.mp4")) is True
    assert presence.source_present(project, root, Path("raw/B.mp4")) is True
    assert presence.source_present(project, root, Path("raw/C.mp4")) is False

    assert storage.list_calls == ["raw/"]
    assert storage.exists_calls == []


def test_local_copy_wins_without_any_storage_call(tmp_path: Path) -> None:
    storage = CountingStorage(tmp_path / "backing")
    project, root = _project(tmp_path, storage)
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "A.mp4").write_bytes(b"a")
    presence = StoragePresence(storage)

    assert presence.source_present(project, root, Path("raw/A.mp4")) is True
    assert storage.list_calls == []
    assert storage.exists_calls == []


def test_trim_available_answers_from_one_listing_of_the_trimmed_prefix(tmp_path: Path) -> None:
    storage = CountingStorage(tmp_path / "backing")
    storage.write_bytes(f"{SCOPE}/trimmed/stage1_cam_v1_trimmed.mp4", b"MP4")
    project, root = _project(tmp_path, storage)
    presence = StoragePresence(storage)
    trimmed = root / "trimmed"

    assert presence.trim_available(project, trimmed / "stage1_cam_v1_trimmed.mp4") is True
    assert presence.trim_available(project, trimmed / "stage2_cam_v2_trimmed.mp4") is False

    assert storage.list_calls == [f"{SCOPE}/trimmed/"]
    assert storage.exists_calls == []


def test_a_key_outside_the_indexed_prefixes_falls_back_to_a_head(tmp_path: Path) -> None:
    storage = CountingStorage(tmp_path / "backing")
    storage.write_bytes("elsewhere/A.mp4", b"a")
    project, root = _project(tmp_path, storage)
    presence = StoragePresence(storage)

    assert presence.source_present(project, root, Path("elsewhere/A.mp4")) is True
    assert storage.exists_calls == ["elsewhere/A.mp4"]
    assert storage.list_calls == []


def test_a_failed_listing_raises_so_the_caller_records_unreachable(tmp_path: Path) -> None:
    storage = CountingStorage(tmp_path / "backing")
    storage.fail_prefixes.add("raw/")
    project, root = _project(tmp_path, storage)
    presence = StoragePresence(storage)

    with pytest.raises(OSError):
        presence.source_present(project, root, Path("raw/A.mp4"))
    with pytest.raises(OSError):
        presence.source_present(project, root, Path("raw/B.mp4"))
    # The failure is remembered: the prefix is not listed again per key.
    assert storage.list_calls == ["raw/"]


def test_trim_lookup_swallows_a_failed_listing_like_trim_available(tmp_path: Path) -> None:
    storage = CountingStorage(tmp_path / "backing")
    storage.fail_prefixes.add(f"{SCOPE}/trimmed/")
    project, root = _project(tmp_path, storage)
    presence = StoragePresence(storage)

    assert presence.trim_available(project, root / "trimmed" / "stage1_cam_v1_trimmed.mp4") is False


# ---------------------------------------------------------------------------
# Through the API: one listing per prefix per request, shared by every shooter
# and by the bulk rebuild; never a HEAD per angle.
# ---------------------------------------------------------------------------


def _hosted_sources_in_storage(
    tmp_path: Path, *, stage_count: int
) -> tuple[object, Path, CountingStorage, str]:
    """A ``stage_count``-stage match whose sources live only in storage.

    Returns the client, the match root, the counting storage and the
    per-shooter storage scope the trim keys live under.
    """
    import json

    from .test_ui_server import _seed_match_export_project

    client, project_root = _seed_match_export_project(tmp_path, stage_count=stage_count)
    storage = CountingStorage(tmp_path / "backing")
    shooter_root = project_root / "shooters" / "me"
    for n in range(1, stage_count + 1):
        storage.write_bytes(f"raw/VID{n}.mp4", b"SOURCEBYTES")
        (shooter_root / "raw" / f"VID{n}.mp4").unlink()
    client.app.state.splitsmith_state.storage = storage
    client.app.state.splitsmith_state.job_bodies.register("trim", lambda handle, **args: None)
    match_id = json.loads((project_root / "match.json").read_text())["match_id"]
    return client, project_root, storage, f"matches/{match_id}/shooters/me"


def _primary_video_id(project_root: Path, stage_number: int) -> str:
    project = MatchProject.load(project_root / "shooters" / "me")
    primary = project.stage(stage_number).primary()
    assert primary is not None
    return primary.video_id


def test_shooters_list_lists_each_prefix_once_and_never_heads(tmp_path: Path) -> None:
    client, _root, storage, scope = _hosted_sources_in_storage(tmp_path, stage_count=3)

    resp = client.get("/api/match/shooters")

    assert resp.status_code == 200, resp.text
    assert resp.json()["shooters"][0]["stages_missing_trim"] == 3
    assert storage.exists_calls == []
    assert sorted(storage.list_calls) == sorted(["raw/", f"{scope}/trimmed/"])


def test_shooters_list_counts_a_trim_found_in_storage_as_cached(tmp_path: Path) -> None:
    client, root, storage, scope = _hosted_sources_in_storage(tmp_path, stage_count=2)
    vid = _primary_video_id(root, 1)
    storage.write_bytes(f"{scope}/trimmed/stage1_cam_{vid}_trimmed.mp4", b"MP4")

    resp = client.get("/api/match/shooters")

    assert resp.json()["shooters"][0]["stages_missing_trim"] == 1
    assert storage.exists_calls == []


def test_shooters_list_reports_a_missing_source_without_a_head(tmp_path: Path) -> None:
    client, _root, storage, _scope = _hosted_sources_in_storage(tmp_path, stage_count=2)
    storage.delete("raw/VID2.mp4")

    resp = client.get("/api/match/shooters")

    # A stage whose source is gone is not "missing a trim" -- it cannot be
    # rebuilt -- exactly as the per-angle HEAD path answered.
    assert resp.json()["shooters"][0]["stages_missing_trim"] == 1
    assert storage.exists_calls == []


def test_bulk_rebuild_shares_one_index_and_keeps_the_skip_reasons(tmp_path: Path) -> None:
    client, root, storage, scope = _hosted_sources_in_storage(tmp_path, stage_count=3)
    cached = _primary_video_id(root, 1)
    storage.write_bytes(f"{scope}/trimmed/stage1_cam_{cached}_trimmed.mp4", b"MP4")
    storage.delete("raw/VID3.mp4")
    gone = _primary_video_id(root, 3)

    resp = client.post("/api/match/shooters/me/build-trim-caches")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    reasons = {e["video_id"]: e["reason"] for e in body["skipped"] if "video_id" in e}
    assert reasons[cached] == "already_cached"
    assert reasons[gone] == "source_missing"
    assert {j["video_id"] for j in body["jobs_submitted"]} == {_primary_video_id(root, 2)}
    assert storage.exists_calls == []
    assert sorted(storage.list_calls) == sorted(["raw/", f"{scope}/trimmed/"])


def test_a_failed_raw_listing_reports_every_source_unreachable(tmp_path: Path) -> None:
    client, _root, storage, _scope = _hosted_sources_in_storage(tmp_path, stage_count=2)
    storage.fail_prefixes.add("raw/")

    resp = client.post("/api/match/shooters/me/build-trim-caches")

    body = resp.json()
    assert {e["reason"] for e in body["skipped"] if "video_id" in e} == {"source_unreachable"}
    assert body["jobs_submitted"] == []


def test_without_a_bound_storage_the_index_mirrors_the_local_answers(tmp_path: Path) -> None:
    project, root = _project(tmp_path, None)
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "A.mp4").write_bytes(b"a")
    presence = StoragePresence(None)

    assert presence.source_present(project, root, Path("raw/A.mp4")) is True
    assert presence.source_present(project, root, Path("raw/B.mp4")) is False
    assert presence.trim_available(project, root / "trimmed" / "x_trimmed.mp4") is False
