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

from splitsmith.cleanup import CleanupCategory, apply_cleanup, plan_cleanup
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


def test_a_local_mirror_is_deduplicated_against_its_storage_object(tmp_path: Path) -> None:
    """A hosted container can hold a mirrored copy of a storage object --
    right after an export job, this is the normal state, not a corner
    case. The two used to become separate ``CleanupItem``s at the same
    ``path``, doubling ``total_bytes`` / ``bytes_freed`` and every
    per-category total the dialog renders against a user's delete
    decision (I2 whole-branch finding).

    ``apply_cleanup``'s storage branch already unlinks the local mirror as
    a side effect of deleting the storage object (see
    ``test_apply_deletes_the_storage_object_and_the_local_mirror``), so
    "apply has to remove each" -- this test's own rationale before the
    fix -- was factually wrong: a single storage-backed item is enough to
    remove both copies. Keep only the storage item, since it is the
    durable byte.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")
    local = root / "exports" / "stage1_a_trimmed.mp4"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"01234")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.storage_key == f"{SCOPE}/exports/stage1_a_trimmed.mp4"
    assert item.size_bytes == 10  # the storage object's size, not the 5-byte local mirror
    assert plan.total_bytes == 10
    assert plan.total_file_count == 1


def _stage_with_primary(project: MatchProject, root: Path, rel: str) -> None:
    """Give the project one stage whose primary points at ``rel``.

    ``StageEntry`` / ``StageVideo`` live in ``splitsmith.match_project``,
    not ``match_model``. ``time_seconds`` is required. ``StageVideo`` has
    no ``video_id`` field -- ``role="primary"`` is what ``primary()``
    looks for.
    """
    from splitsmith.match_project import StageEntry, StageVideo

    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="alpha",
            time_seconds=12.5,
            videos=[StageVideo(path=Path(rel), role="primary")],
        )
    )
    project.save(root)


def test_a_trim_is_reconstructable_while_its_source_survives(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    # NOT scope-prefixed: ``source_present`` calls
    # ``storage.exists(str(video_path))`` with the user-prefix-relative
    # path. Scope prefixes derived caches (exports/, trimmed/, audio/),
    # never raw sources -- see ``bind_storage``'s docstring.
    _put(backing, "raw/clip.mp4", b"source")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert [i.reconstructable for i in plan.items] == [True]


def test_a_trim_is_not_reconstructable_once_the_source_is_gone(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    # Source absent from storage; present only in the ephemeral local cache.
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "clip.mp4").write_bytes(b"cached")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert [i.reconstructable for i in plan.items] == [False]


def test_exports_light_uses_the_callers_audit_stages_on_hosted(tmp_path: Path) -> None:
    """Hosted audit docs are in ``state_docs``, not on this disk.

    Without the ``audit_stages`` hand-off the planner reads an empty
    container directory and calls every CSV and FCPXML unrebuildable,
    which would push the cheapest category out of "select all" on exactly
    the deployment this change exists for.
    """
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_alpha.fcpxml")
    # Nothing on local disk; the caller knows stage 1 has an audit doc.

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT}, audit_stages={1})

    assert [i.reconstructable for i in plan.items] == [True]


def test_exports_light_keys_on_the_audit_doc_not_the_source(tmp_path: Path) -> None:
    """The row that regresses desktop select-all if keyed on the source.

    A CSV/FCPXML is re-derived from the audit doc, which is durable and
    only removable through the separately-gated AUDIT_DATA category. If
    this were keyed on the source video, EXPORTS_LIGHT would drop out of
    "select all" the moment a source went missing -- for the cheapest,
    most re-derivable category in the table.
    """
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    # No source anywhere; audit doc present.
    (root / "audit").mkdir(parents=True, exist_ok=True)
    (root / "audit" / "stage1.json").write_text("{}", encoding="utf-8")
    _put(backing, f"{SCOPE}/exports/stage1_alpha.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})

    assert [i.reconstructable for i in plan.items] == [True]


def test_exports_light_flips_when_the_audit_doc_is_gone(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_alpha.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})

    assert [i.reconstructable for i in plan.items] == [False]


def test_an_audit_trim_is_reconstructable_while_its_source_survives(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, "raw/clip.mp4", b"source")
    _put(backing, f"{SCOPE}/trimmed/stage1_alpha.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIT_TRIMS})

    assert [i.reconstructable for i in plan.items] == [True]


def test_an_audit_trim_is_not_reconstructable_once_the_source_is_gone(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "clip.mp4").write_bytes(b"cached")
    _put(backing, f"{SCOPE}/trimmed/stage1_alpha.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIT_TRIMS})

    assert [i.reconstructable for i in plan.items] == [False]


def test_an_overlay_is_reconstructable_while_its_source_survives(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, "raw/clip.mp4", b"source")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_overlay.mov")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_OVERLAYS})

    assert [i.reconstructable for i in plan.items] == [True]


def test_an_overlay_is_not_reconstructable_once_the_source_is_gone(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "clip.mp4").write_bytes(b"cached")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_overlay.mov")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_OVERLAYS})

    assert [i.reconstructable for i in plan.items] == [False]


def test_a_secondarys_trim_is_keyed_on_its_own_source_not_the_primarys(tmp_path: Path) -> None:
    """C1 whole-branch finding: a secondary's export trim and audit trim
    are per-camera artefacts (``..._cam_<video_id>_trimmed.mp4``), but
    ``_reconstructable`` used to resolve every ``EXPORTS_TRIMS`` /
    ``AUDIT_TRIMS`` item through ``stage.primary()`` regardless. So a
    secondary's trim was judged reconstructable whenever the *primary's*
    source survived, even with the secondary's own source gone -- putting
    an item that cannot be rebuilt inside "select all" with no consent
    tick. Confirming a plan like that destroys the secondary's trims with
    nothing checked.

    The primary's source is present; the secondary's is not. The
    primary's trim must stay reconstructable, and the secondary's export
    trim and audit trim must both flip to False.
    """
    from splitsmith.match_project import StageEntry, StageVideo

    project, root, backing = _project(tmp_path)
    primary = StageVideo(path=Path("raw/primary.mp4"), role="primary", stage_number=1)
    secondary = StageVideo(path=Path("raw/secondary.mp4"), role="secondary", stage_number=1)
    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="alpha",
            time_seconds=12.5,
            videos=[primary, secondary],
        )
    )
    project.save(root)

    _put(backing, "raw/primary.mp4", b"source")
    # secondary's own source is never written -- it is the one that's gone.

    base = "stage1_alpha"
    _put(backing, f"{SCOPE}/exports/{base}_trimmed.mp4")
    _put(backing, f"{SCOPE}/exports/{base}_cam_{secondary.video_id}_trimmed.mp4")
    _put(backing, f"{SCOPE}/trimmed/stage1_cam_{primary.video_id}_trimmed.mp4")
    _put(backing, f"{SCOPE}/trimmed/stage1_cam_{secondary.video_id}_trimmed.mp4")

    trims = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    audit_trims = plan_cleanup(project, root, {CleanupCategory.AUDIT_TRIMS})

    trims_by_name = {i.path.name: i.reconstructable for i in trims.items}
    assert trims_by_name[f"{base}_trimmed.mp4"] is True
    assert trims_by_name[f"{base}_cam_{secondary.video_id}_trimmed.mp4"] is False

    audit_trims_by_name = {i.path.name: i.reconstructable for i in audit_trims.items}
    assert audit_trims_by_name[f"stage1_cam_{primary.video_id}_trimmed.mp4"] is True
    assert audit_trims_by_name[f"stage1_cam_{secondary.video_id}_trimmed.mp4"] is False


def test_a_cam_id_that_does_not_resolve_to_a_registered_video_fails_closed(tmp_path: Path) -> None:
    """A ``_cam_<id>_`` segment that cannot be resolved to a video on the
    stage (stale artefact from a removed camera, corrupted name, etc.)
    must not be silently treated as reconstructable -- there is no source
    to check, so the honest answer is "cannot prove it", not "assume yes".
    """
    from splitsmith.match_project import StageEntry, StageVideo

    project, root, backing = _project(tmp_path)
    primary = StageVideo(path=Path("raw/primary.mp4"), role="primary", stage_number=1)
    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="alpha",
            time_seconds=12.5,
            videos=[primary],
        )
    )
    project.save(root)
    _put(backing, "raw/primary.mp4", b"source")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_cam_deadbeef0000_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert [i.reconstructable for i in plan.items] == [False]


def test_audio_is_reconstructable_when_every_registered_source_survives(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, "raw/clip.mp4", b"source")
    _put(backing, f"{SCOPE}/audio/stage1_cam_v1.wav")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})

    assert [i.reconstructable for i in plan.items] == [True]


def test_audio_requires_every_registered_source_not_just_the_named_stage(tmp_path: Path) -> None:
    """AUDIO wavs are stage-prefixed, but a wav can derive from any
    registered video. Keying ``reconstructable`` on just the wav's own
    stage (stage 1, whose source is present) would wrongly say True while
    stage 2's source is gone -- this is the case that distinguishes the
    fixed whole-project rule from the broken per-stage-primary one.
    """
    from splitsmith.match_project import StageEntry, StageVideo

    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip1.mp4")  # stage 1, source present
    project.stages.append(
        StageEntry(
            stage_number=2,
            stage_name="bravo",
            time_seconds=9.0,
            videos=[StageVideo(path=Path("raw/clip2.mp4"), role="primary")],
        )
    )
    project.save(root)
    _put(backing, "raw/clip1.mp4", b"source")
    # stage 2's source (clip2.mp4) is never written to storage.
    _put(backing, f"{SCOPE}/audio/stage1_cam_v1.wav")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIO})

    assert [i.reconstructable for i in plan.items] == [False]


def test_apply_deletes_the_storage_object_and_the_local_mirror(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")
    local = root / "exports" / "stage1_a_trimmed.mp4"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"01234")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    result = apply_cleanup(plan, root=root, project=project)

    assert not (backing / SCOPE / "exports" / "stage1_a_trimmed.mp4").exists()
    assert not local.exists()
    assert result.failed == []


def test_apply_deletes_no_key_outside_the_plan(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_a.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    apply_cleanup(plan, root=root, project=project)

    assert (backing / SCOPE / "exports" / "stage1_a.fcpxml").exists()


def test_apply_writes_the_log_to_storage_on_hosted(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    apply_cleanup(plan, root=root, project=project)

    log = (backing / SCOPE / ".cleanup.log").read_text(encoding="utf-8")
    assert log.count("\n") == 1
    assert "bytes_freed" in log


def test_apply_appends_rather_than_overwrites_the_storage_log(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    for name in ("stage1_a_trimmed.mp4", "stage2_b_trimmed.mp4"):
        _put(backing, f"{SCOPE}/exports/{name}")
        plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
        apply_cleanup(plan, root=root, project=project)

    log = (backing / SCOPE / ".cleanup.log").read_text(encoding="utf-8")
    assert log.count("\n") == 2


def test_apply_does_not_count_a_failed_storage_delete_as_freed(tmp_path: Path) -> None:
    """``bytes_freed`` must be honest: a storage delete that raises must not
    add its bytes to the tally, and the item must not be recorded deleted --
    even though the local mirror (if any) could still be removed. This is
    the sharp version of the "no double counting" rule: it is not enough for
    a passing item to count once, a *failing* item must count zero times.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    def boom(key: str) -> None:
        raise OSError("simulated storage outage")

    project._storage.delete = boom  # type: ignore[method-assign]

    result = apply_cleanup(plan, root=root, project=project)

    assert result.bytes_freed == 0
    assert result.deleted == []
    assert len(result.failed) == 1
    # The object is still there -- nothing was actually freed.
    assert (backing / SCOPE / "exports" / "stage1_a_trimmed.mp4").exists()


def test_apply_reports_failed_not_freed_when_no_storage_is_bound(tmp_path: Path) -> None:
    """The exact silent-success failure this task exists to prevent: a
    storage-backed item reaches ``apply_cleanup`` with no storage bound
    (e.g. a caller that forgets to pass ``project``, or a project whose
    storage was never bound). The old code unlinked ``item.path`` -- which
    usually doesn't exist for a storage-backed item -- via
    ``missing_ok=True``, silently no-opped, and still recorded the item as
    deleted with its bytes freed. It must instead land in ``failed`` and
    contribute nothing to ``deleted`` or ``bytes_freed``.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    # No project at all.
    result = apply_cleanup(plan, root=root)

    assert result.bytes_freed == 0
    assert result.deleted == []
    assert len(result.failed) == 1
    assert result.failed[0][0] == plan.items[0].path
    # The object was never touched.
    assert (backing / SCOPE / "exports" / "stage1_a_trimmed.mp4").exists()

    # A project whose storage is unbound behaves the same way.
    project.bind_storage(None)
    result2 = apply_cleanup(plan, root=root, project=project)

    assert result2.bytes_freed == 0
    assert result2.deleted == []
    assert len(result2.failed) == 1
    assert (backing / SCOPE / "exports" / "stage1_a_trimmed.mp4").exists()


def test_append_storage_log_does_not_clobber_the_log_on_a_transient_read_error(tmp_path: Path) -> None:
    """A bare ``except Exception`` around the read used to treat *any*
    read failure -- permission denied, throttling, a network blip -- the
    same as "no log yet", so ``apply_cleanup`` would overwrite the whole
    accumulated audit trail with a single new record. Only a genuine
    missing key (``FileNotFoundError``) may be treated that way; any other
    exception must skip the append entirely and leave the existing log
    untouched.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")
    log_key = f"{SCOPE}/.cleanup.log"
    _put(backing, log_key, b'{"ts": "pre-existing", "deleted_count": 1}\n')

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    real_read_bytes = project._storage.read_bytes

    def flaky_read(path: str) -> bytes:
        if path == log_key:
            raise PermissionError("simulated transient read failure")
        return real_read_bytes(path)

    project._storage.read_bytes = flaky_read  # type: ignore[method-assign]

    apply_cleanup(plan, root=root, project=project)

    log = (backing / log_key).read_text(encoding="utf-8")
    assert "pre-existing" in log
    # The append was skipped entirely -- no second line got appended either,
    # since we never learned what was already there.
    assert log.count("\n") == 1


def test_plan_serialises_the_fields_the_spa_reads(tmp_path: Path) -> None:
    """``storage_key`` and ``reconstructable`` must survive model_dump.

    Both cleanup routes are pass-throughs of ``plan.model_dump(mode="json")``
    (``server.py:12466``), so this is the whole wire contract. A field the
    SPA's CleanupDialog reads that never reaches JSON would be a silent
    ``undefined`` in the browser and a green Python suite.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")

    payload = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS}).model_dump(mode="json")

    assert payload["items"], "expected one planned item"
    item = payload["items"][0]
    assert item["storage_key"] == f"{SCOPE}/exports/stage1_a_trimmed.mp4"
    assert item["reconstructable"] is False  # no stage registered -> no source
    assert isinstance(item["path"], str)  # Path must serialise for the SPA


class _CountingStorage(FilesystemStorage):
    """Wraps ``FilesystemStorage`` to count ``exists``/``list`` calls.

    The I3 whole-branch finding is about call *volume*, not correctness --
    every existing hosted test already exercises correct results through
    plain ``FilesystemStorage``. This subclass exists only to make the
    call count an assertable fact instead of a claim.
    """

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.exists_calls = 0
        self.list_calls = 0

    def exists(self, path: str) -> bool:
        self.exists_calls += 1
        return super().exists(path)

    def list(self, prefix: str):  # type: ignore[override]
        self.list_calls += 1
        return super().list(prefix)


def test_plan_memoises_source_present_across_items(tmp_path: Path) -> None:
    """I3 whole-branch finding: nothing memoised ``source_present``, and the
    ``AUDIO`` row rebuilt the full video list and re-checked every source
    for each wav, all over again, per wav. Measured on this exact shape --
    20 stages x 2 cameras (40 registered videos), all three storage-backed
    categories that call ``source_present`` requested together -- pre-fix
    code made 1680 ``storage.exists()`` calls against one ``list()``; this
    pins the fixed count, not an estimate: at most one HEAD per distinct
    video path used across the whole plan (40 here), regardless of how
    many items derive from it.
    """
    from splitsmith.export_naming import stage_file_base
    from splitsmith.match_project import StageEntry, StageVideo

    root = tmp_path / "p"
    project = MatchProject.init(root, name="probe")
    backing = tmp_path / "tenant"
    backing.mkdir(exist_ok=True)
    storage = _CountingStorage(backing)
    project.bind_storage(storage, scope=SCOPE)

    n_stages = 20
    for s in range(1, n_stages + 1):
        primary = StageVideo(path=Path(f"raw/stage{s}_primary.mp4"), role="primary", stage_number=s)
        secondary = StageVideo(path=Path(f"raw/stage{s}_secondary.mp4"), role="secondary", stage_number=s)
        project.stages.append(
            StageEntry(
                stage_number=s,
                stage_name=f"stage-{s}",
                time_seconds=10.0,
                videos=[primary, secondary],
            )
        )
    project.save(root)

    for stage in project.stages:
        _put(backing, f"raw/stage{stage.stage_number}_primary.mp4")
        _put(backing, f"raw/stage{stage.stage_number}_secondary.mp4")
        primary = stage.primary()
        secondary = next(v for v in stage.videos if v.role == "secondary")
        base = stage_file_base(stage.stage_number, stage.stage_name)
        _put(backing, f"{SCOPE}/exports/{base}_trimmed.mp4")
        _put(backing, f"{SCOPE}/exports/{base}_cam_{secondary.video_id}_trimmed.mp4")
        _put(backing, f"{SCOPE}/trimmed/stage{stage.stage_number}_cam_{primary.video_id}_trimmed.mp4")
        _put(backing, f"{SCOPE}/trimmed/stage{stage.stage_number}_cam_{secondary.video_id}_trimmed.mp4")
        _put(backing, f"{SCOPE}/audio/stage{stage.stage_number}_cam_{primary.video_id}.wav")
        _put(backing, f"{SCOPE}/audio/stage{stage.stage_number}_cam_{secondary.video_id}.wav")

    plan = plan_cleanup(
        project,
        root,
        {CleanupCategory.EXPORTS_TRIMS, CleanupCategory.AUDIT_TRIMS, CleanupCategory.AUDIO},
    )

    assert len(plan.items) == 120  # 6 artefacts x 20 stages
    assert storage.list_calls == 1  # one scope listing regardless of category count
    # 40 distinct registered video paths; every item that needs an answer
    # about one of them reuses the memoised result. Pre-fix this was 1680.
    assert storage.exists_calls == 40
