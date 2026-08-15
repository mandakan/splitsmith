"""Hosted-mode cleanup: the bytes are in object storage, not on this disk.

``plan_cleanup`` used to glob ``project.exports_path(root)`` only. In
hosted mode that directory is an ephemeral container cache and the durable
bytes live under ``<scope>/``, so a hosted plan reported zero items and
reclaimed nothing -- the same shape as the #565 source-cache LRU, shipped
and inert in the deployment that needs it.

``FilesystemStorage`` against ``tmp_path`` is the established fake here
(Protocol-equivalent to ``S3Storage`` per ``test_s3_storage.py``).
"""

from __future__ import annotations

from pathlib import Path

from splitsmith.cleanup import CleanupCategory, plan_cleanup
from splitsmith.match_project import MatchProject
from splitsmith.storage import FilesystemStorage

SCOPE = "matches/m1/shooters/me"


def _project(tmp_path: Path) -> tuple[MatchProject, Path, Path]:
    root = tmp_path / "p"
    project = MatchProject.init(root, name="cleanup-test")
    backing = tmp_path / "tenant"
    backing.mkdir(exist_ok=True)
    project.bind_storage(FilesystemStorage(backing), scope=SCOPE)
    return project, root, backing


def _put(backing: Path, key: str, data: bytes = b"xxxx") -> None:
    dest = backing / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def test_plan_finds_export_trims_in_storage(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_alpha_trimmed.mp4", b"0123456789")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert plan.total_file_count == 1
    item = plan.items[0]
    assert item.storage_key == f"{SCOPE}/exports/stage1_alpha_trimmed.mp4"
    assert item.size_bytes == 10
    assert item.path.name == "stage1_alpha_trimmed.mp4"
    assert plan.total_bytes == 10


def test_plan_keeps_export_buckets_distinct_in_storage(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_a_overlay.mov")
    _put(backing, f"{SCOPE}/exports/stage1_a.fcpxml")

    trims = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    overlays = plan_cleanup(project, root, {CleanupCategory.EXPORTS_OVERLAYS})
    light = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})

    assert [i.path.name for i in trims.items] == ["stage1_a_trimmed.mp4"]
    assert [i.path.name for i in overlays.items] == ["stage1_a_overlay.mov"]
    assert [i.path.name for i in light.items] == ["stage1_a.fcpxml"]


def test_plan_reads_audio_and_peaks_from_the_same_prefix(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/audio/clip.wav")
    _put(backing, f"{SCOPE}/audio/clip.peaks-3000.json")

    audio = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    caches = plan_cleanup(project, root, {CleanupCategory.CACHES})

    assert [i.path.name for i in audio.items] == ["clip.wav"]
    assert [i.path.name for i in caches.items] == ["clip.peaks-3000.json"]


def test_plan_ignores_keys_outside_the_scope(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, "matches/m1/shooters/someone-else/exports/stage1_x_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert plan.items == []


def test_plan_never_offers_raw_sources(tmp_path: Path) -> None:
    """Raw uploads are keyed ``raw/<name>`` at the storage root, *outside*
    the per-project scope -- ``bind_storage``'s docstring is explicit that
    ``scope`` prefixes derived-artefact caches only, and the raw resolver
    keys off the user-prefix-relative ``StageVideo.path`` directly.

    So the real protection is that the scope listing never sees them. The
    scoped variant is asserted too as defence-in-depth, in case a future
    change moves raw under the scope.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, "raw/original.mp4", b"irreplaceable")
    _put(backing, f"{SCOPE}/raw/scoped-someday.mp4", b"irreplaceable")

    plan = plan_cleanup(project, root, set(CleanupCategory))

    assert all("raw/" not in (i.storage_key or "") for i in plan.items)
    assert (backing / "raw" / "original.mp4").exists()


def test_hosted_audit_data_plans_nothing(tmp_path: Path) -> None:
    """Hosted audit docs live in ``state_docs``, not object storage.

    Deleting them is a database operation this module does not do. The
    empty plan is correct; this test exists so a future reader does not
    mistake it for "audit-data cleanup works on hosted".
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIT_DATA})

    assert plan.items == []


def test_local_and_storage_items_both_appear(tmp_path: Path) -> None:
    """A hosted container can hold a mirrored copy of a storage object.

    Both are reported, and they are not deduplicated into one item -- the
    storage object is the durable byte and the local file is a cache, and
    apply has to remove each.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")
    local = root / "exports" / "stage1_a_trimmed.mp4"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"01234")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    keys = sorted((i.storage_key or "<local>") for i in plan.items)
    assert keys == ["<local>", f"{SCOPE}/exports/stage1_a_trimmed.mp4"]
