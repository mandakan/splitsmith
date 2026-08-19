"""Verification is what stands between the migration and rm -rf.

Each predicate returns findings, never a bool: the report has to say
which document, which shooter, which byte count, or it cannot be read by
a human deciding whether to delete 3 GB of originals.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _match(root: Path, shooters: dict[str, dict]) -> Path:
    (root / "shooters").mkdir(parents=True)
    (root / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    for slug, spec in shooters.items():
        shooter = root / "shooters" / slug
        shooter.mkdir()
        project = {"schema_version": 2, "name": "M", "stages": []}
        if spec.get("token"):
            project["shooter_token"] = spec["token"]
        (shooter / "project.json").write_text(json.dumps(project))
        (shooter / "audit").mkdir()
        audit_content = spec.get("audit_content", {})
        for name in spec.get("audits", []):
            content = audit_content.get(name, json.dumps({"doc": name}))
            (shooter / "audit" / name).write_text(content)
        (shooter / "trimmed").mkdir()
        sizes = spec.get("trimmed_sizes") or [64] * spec.get("trimmed", 0)
        for index, size in enumerate(sizes):
            (shooter / "trimmed" / f"stage{index + 1}_trimmed.mp4").write_bytes(b"0" * size)
        (shooter / "raw").mkdir()
    return root


def _legacy(root: Path, *, token: str | None = None, audits: list[str] | None = None) -> Path:
    """A single-shooter legacy project: no match.json, no shooters/ dir."""
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    for name in audits or []:
        (root / "audit" / name).write_text(json.dumps({"doc": name}))
    (root / "trimmed").mkdir()
    (root / "raw").mkdir()
    return root


def test_a_lost_document_is_reported_by_name(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json", "stage2.json"]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json"]}})
    )

    findings = mod.verify_documents_survived(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "stage2.json" in findings[0].detail


def test_documents_that_all_survived_report_nothing(tmp_path: Path) -> None:
    mod = _mod()
    spec = {"s_a": {"token": "s97dcec94", "audits": ["stage1.json", "stage2.json"]}}
    before = mod.inventory_project(_match(tmp_path / "before", spec))
    after = mod.inventory_project(_match(tmp_path / "after", spec))

    assert mod.verify_documents_survived(before, after) == []


def test_shrunk_media_is_reported_with_both_byte_counts(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 3}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed": 1}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert "192" in findings[0].detail and "64" in findings[0].detail


def test_a_broken_link_after_migration_is_a_finding(tmp_path: Path) -> None:
    mod = _mod()
    root = _match(tmp_path / "after", {"s_a": {"token": "t", "audits": []}})
    (root / "shooters" / "s_a" / "raw" / "IMG_1.MOV").symlink_to(tmp_path / "gone" / "IMG_1.MOV")

    findings = mod.verify_no_broken_links(mod.inventory_project(root))

    assert len(findings) == 1
    assert "IMG_1.MOV" in findings[0].detail


def test_a_dropped_shooter_token_is_a_finding(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(_match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": []}}))
    after = mod.inventory_project(_match(tmp_path / "after", {"s_a": {"audits": []}}))

    findings = mod.verify_tokens_preserved(before, after)

    assert len(findings) == 1
    assert "s97dcec94" in findings[0].detail


def test_a_same_name_different_hash_document_is_replaced_not_lost(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(
            tmp_path / "before",
            {
                "s_a": {
                    "token": "t",
                    "audits": ["stage1.json"],
                    "audit_content": {"stage1.json": '{"v": 1}'},
                }
            },
        )
    )
    after = mod.inventory_project(
        _match(
            tmp_path / "after",
            {
                "s_a": {
                    "token": "t",
                    "audits": ["stage1.json"],
                    "audit_content": {"stage1.json": '{"v": 2}'},
                }
            },
        )
    )

    replaced = mod.verify_documents_replaced(before, after)
    survived = mod.verify_documents_survived(before, after)

    assert survived == []
    assert len(replaced) == 1
    assert replaced[0].subject == "t"
    assert "stage1.json" in replaced[0].detail


def test_a_same_name_same_hash_document_reports_nothing(tmp_path: Path) -> None:
    mod = _mod()
    spec = {
        "s_a": {
            "token": "t",
            "audits": ["stage1.json"],
            "audit_content": {"stage1.json": '{"v": 1}'},
        }
    }
    before = mod.inventory_project(_match(tmp_path / "before", spec))
    after = mod.inventory_project(_match(tmp_path / "after", spec))

    assert mod.verify_documents_replaced(before, after) == []
    assert mod.verify_documents_survived(before, after) == []


def test_a_vanished_shooter_is_a_media_finding_with_byte_counts(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 2}})
    )
    after = mod.inventory_project(_match(tmp_path / "after", {}))

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "t"
    assert "128" in findings[0].detail


def _map(mapping: dict[str, str]):
    return _mod().RenameMap(destinations=mapping)


def test_a_renamed_relocated_project_resolves_through_the_declared_map(tmp_path: Path) -> None:
    """The migration's whole point is that projects move and get renamed.

    A legacy directory becomes a shooter inside a merged match under a new
    name, on a different volume. Which one it becomes is declared, not
    guessed: nothing in the two inventories carries that fact.
    """
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "blacksmith-2026", token="s97dcec94"))]
    after = [
        mod.inventory_project(
            _match(
                tmp_path / "x9" / "blacksmith-handgun-open-2026",
                {
                    "s_ce10fa76": {"token": "s97dcec94", "audits": []},
                    "s_9540b345": {"token": "s9540b345", "audits": []},
                },
            )
        )
    ]

    pairs, findings = mod.resolve_projects(
        before, after, _map({"blacksmith-2026": "blacksmith-handgun-open-2026"})
    )

    assert findings == []
    assert len(pairs) == 1
    assert pairs[0][1].root.name == "blacksmith-handgun-open-2026"


def test_several_before_projects_may_declare_one_destination(tmp_path: Path) -> None:
    """Three legacy blacksmith projects become three shooters in one match."""
    mod = _mod()
    before = [
        mod.inventory_project(_legacy(tmp_path / "home" / "blacksmith-2026", token="s97dcec94")),
        mod.inventory_project(_legacy(tmp_path / "home" / "blacksmith-handgun-2026-anton", token="s3")),
    ]
    after = [
        mod.inventory_project(
            _match(
                tmp_path / "x9" / "blacksmith-handgun-open-2026",
                {"s_ce10fa76": {"token": "s97dcec94", "audits": []}, "s_46039db3": {"token": "s3"}},
            )
        )
    ]

    pairs, findings = mod.resolve_projects(
        before,
        after,
        _map(
            {
                "blacksmith-2026": "blacksmith-handgun-open-2026",
                "blacksmith-handgun-2026-anton": "blacksmith-handgun-open-2026",
            }
        ),
    )

    assert findings == []
    assert [pair[1].root.name for pair in pairs] == [
        "blacksmith-handgun-open-2026",
        "blacksmith-handgun-open-2026",
    ]


def test_a_before_project_absent_from_the_map_is_a_blocking_finding(tmp_path: Path) -> None:
    """Never a silent skip: an unmapped project is a hole in the declaration."""
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "gone-2026", token="s0fe3d797"))]
    after = [
        mod.inventory_project(_match(tmp_path / "x9" / "other-2026", {"s_a": {"token": "t", "audits": []}}))
    ]

    pairs, findings = mod.resolve_projects(before, after, _map({"other-2026": "other-2026"}))

    assert pairs == []
    checks = {f.check for f in findings}
    assert checks == {"project_mapped", "rename_map_unmatched"}
    mapped = next(f for f in findings if f.check == "project_mapped")
    assert "gone-2026" in mapped.subject
    assert "s0fe3d797" in mapped.detail


def test_a_mapped_destination_missing_from_the_after_inventory_names_both(tmp_path: Path) -> None:
    mod = _mod()
    before = [
        mod.inventory_project(_legacy(tmp_path / "home" / "bofors-bombardment-2026", token="s97dcec94"))
    ]
    after = [
        mod.inventory_project(
            _match(
                tmp_path / "x9" / "blacksmith-handgun-open-2026",
                {"s_ce10fa76": {"token": "s97dcec94", "audits": []}},
            )
        )
    ]

    pairs, findings = mod.resolve_projects(
        before, after, _map({"bofors-bombardment-2026": "bofors-bombardment-2026"})
    )

    assert pairs == []
    assert len(findings) == 1
    assert findings[0].check == "project_destination_present"
    assert "bofors-bombardment-2026" in findings[0].subject
    assert "bofors-bombardment-2026" in findings[0].detail


def test_a_shared_shooter_token_does_not_resolve_a_project(tmp_path: Path) -> None:
    """A token identifies a shooter, not a match. Four of them span ten matches.

    The map here deliberately declares nothing for the bofors project, so
    the only thing that could pair it with blacksmith is the token both
    carry. It must not.
    """
    mod = _mod()
    before = [
        mod.inventory_project(_legacy(tmp_path / "home" / "bofors-bombardment-2026", token="s97dcec94"))
    ]
    after = [
        mod.inventory_project(
            _match(
                tmp_path / "x9" / "blacksmith-handgun-open-2026",
                {"s_ce10fa76": {"token": "s97dcec94", "audits": []}},
            )
        )
    ]

    pairs, findings = mod.resolve_projects(before, after, _map({}))

    assert pairs == []
    assert [f.check for f in findings] == ["project_mapped"]


def test_two_after_projects_sharing_a_directory_name_are_ambiguous(tmp_path: Path) -> None:
    """A destination name that resolves to two projects is not a resolution."""
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "tallmilan-2025"))]
    after = [
        mod.inventory_project(_legacy(tmp_path / "x9" / "tallmilan-2025")),
        mod.inventory_project(_legacy(tmp_path / "backup" / "tallmilan-2025")),
    ]

    pairs, findings = mod.resolve_projects(before, after, _map({"tallmilan-2025": "tallmilan-2025"}))

    assert pairs == []
    assert findings[0].check == "project_destination_ambiguous"


def test_a_project_that_stayed_put_still_has_to_declare_itself(tmp_path: Path) -> None:
    """Same name on both sides is still an entry in the map, not a fallback."""
    mod = _mod()
    before = [mod.inventory_project(_legacy(tmp_path / "home" / "tallmilan-2025"))]
    after = [mod.inventory_project(_legacy(tmp_path / "x9" / "tallmilan-2025"))]

    pairs, findings = mod.resolve_projects(before, after, _map({"tallmilan-2025": "tallmilan-2025"}))

    assert findings == []
    assert pairs[0][1].root == tmp_path / "x9" / "tallmilan-2025"


def test_a_rename_map_file_is_read_as_a_flat_object_of_names(tmp_path: Path) -> None:
    mod = _mod()
    path = tmp_path / "rename-map.json"
    path.write_text(json.dumps({"blacksmith-2026": "blacksmith-handgun-open-2026"}))

    rename_map = mod.load_rename_map(path)

    assert rename_map.destinations == {"blacksmith-2026": "blacksmith-handgun-open-2026"}


def test_a_rename_map_that_is_not_an_object_of_strings_names_the_file(tmp_path: Path) -> None:
    mod = _mod()
    path = tmp_path / "rename-map.json"
    path.write_text(json.dumps({"blacksmith-2026": ["a", "b"]}))

    with pytest.raises(mod.MalformedRenameMapError) as excinfo:
        mod.load_rename_map(path)

    assert str(path) in str(excinfo.value)


def test_a_shrunk_media_count_at_equal_bytes_is_a_finding(tmp_path: Path) -> None:
    """Bytes alone cannot see one clip replacing two of half the size."""
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed_sizes": [64, 64]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed_sizes": [128]}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert "2 file" in findings[0].detail and "1 file" in findings[0].detail


def test_shrunk_media_reports_counts_and_bytes_together(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 3}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed": 1}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    detail = findings[0].detail
    assert "3 file" in detail and "192" in detail
    assert "1 file" in detail and "64" in detail


def test_a_shooter_that_vanished_entirely_loses_its_documents(tmp_path: Path) -> None:
    """The within-project twin of an unpaired project.

    Every other test reuses the same slug on both sides, so the slug
    fallback always pairs and this branch never runs.
    """
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json"]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_b": {"token": "s36ed6e4e", "audits": ["stage1.json"]}})
    )

    findings = mod.verify_documents_survived(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "no counterpart shooter" in findings[0].detail


def test_a_shooter_that_vanished_entirely_loses_its_token(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(_match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": []}}))
    after = mod.inventory_project(_match(tmp_path / "after", {"s_b": {"token": "s36ed6e4e", "audits": []}}))

    findings = mod.verify_tokens_preserved(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "s97dcec94" in findings[0].detail


def test_a_recorded_undeletable_reconcile_is_a_finding(tmp_path: Path) -> None:
    """The central safety rule has to reach the report a human reads."""
    mod = _mod()
    record = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged" / "shooters" / "s_a",
        applied=False,
        action_count=0,
        violations=[
            mod.SafetyViolation(source=tmp_path / "legacy", document="IMG_1.MOV", reason="unlinked raw")
        ],
        deletable=False,
    )

    findings = mod.verify_reconcile_records([record], log_path=tmp_path / "reconcile-log.json")

    assert len(findings) == 1
    assert findings[0].check == "reconcile_deletable"
    assert "legacy" in findings[0].subject
    assert "IMG_1.MOV" in findings[0].detail


def test_a_recorded_deletable_reconcile_reports_nothing(tmp_path: Path) -> None:
    mod = _mod()
    record = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged",
        applied=True,
        action_count=2,
        violations=[],
        deletable=True,
    )

    assert mod.verify_reconcile_records([record], log_path=tmp_path / "reconcile-log.json") == []


def test_a_deletable_verdict_that_was_never_applied_is_a_finding(tmp_path: Path) -> None:
    """A plan-only run copies nothing; its verdict is about a future state.

    Running ``reconcile`` without ``--apply`` over a clean source records
    ``deletable: true`` while not one document has moved. Treating that as
    a satisfied gate hands phase 8 a source whose destination never
    received anything.
    """
    mod = _mod()
    record = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged",
        applied=False,
        action_count=3,
        violations=[],
        deletable=True,
    )

    findings = mod.verify_reconcile_records([record], log_path=tmp_path / "reconcile-log.json")

    assert len(findings) == 1
    assert findings[0].check == "reconcile_applied"
    assert "legacy" in findings[0].subject
    assert "never applied" in findings[0].detail


def test_no_recorded_reconciles_at_all_is_a_finding_naming_the_log(tmp_path: Path) -> None:
    """ "No records" must never read as "all clear"."""
    mod = _mod()
    log_path = tmp_path / "reports" / "reconcile-log.json"

    findings = mod.verify_reconcile_records([], log_path=log_path)

    assert len(findings) == 1
    assert findings[0].check == "reconcile_log_present"
    assert str(log_path) in findings[0].subject


def test_a_later_record_for_the_same_pair_replaces_the_earlier_one(tmp_path: Path) -> None:
    """Re-running after fixing a violation must not leave the stale verdict."""
    mod = _mod()

    def record(*, deletable: bool, applied: bool) -> object:
        return mod.ReconcileRecord(
            source=tmp_path / "legacy",
            destination=tmp_path / "merged",
            applied=applied,
            action_count=0,
            violations=[],
            deletable=deletable,
        )

    records = mod.supersede_records(
        [record(deletable=False, applied=False)], record(deletable=True, applied=True)
    )

    assert len(records) == 1
    assert records[0].deletable is True
    assert records[0].applied is True


def test_a_record_for_a_different_pair_is_kept_alongside(tmp_path: Path) -> None:
    """A phase runs one reconcile per shooter and the reviewer needs all of them."""
    mod = _mod()
    first = mod.ReconcileRecord(
        source=tmp_path / "legacy1",
        destination=tmp_path / "merged1",
        applied=True,
        action_count=0,
        deletable=True,
    )
    second = mod.ReconcileRecord(
        source=tmp_path / "legacy2",
        destination=tmp_path / "merged2",
        applied=True,
        action_count=0,
        deletable=True,
    )

    records = mod.supersede_records([first], second)

    assert [r.source.name for r in records] == ["legacy1", "legacy2"]


def test_the_same_source_reconciled_into_two_destinations_keeps_both(tmp_path: Path) -> None:
    """The key is the pair, not the source: one source can feed two shooters."""
    mod = _mod()
    first = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged" / "s_a",
        applied=True,
        action_count=0,
        deletable=True,
    )
    second = mod.ReconcileRecord(
        source=tmp_path / "legacy",
        destination=tmp_path / "merged" / "s_b",
        applied=True,
        action_count=0,
        deletable=True,
    )

    records = mod.supersede_records([first], second)

    assert len(records) == 2


def test_a_malformed_project_json_names_the_offending_file(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy(tmp_path / "broken-2026")
    (root / "project.json").write_text("{not json")

    with pytest.raises(mod.MalformedProjectError) as excinfo:
        mod.inventory_project(root)

    assert str(root / "project.json") in str(excinfo.value)


def test_a_malformed_match_json_names_the_offending_file(tmp_path: Path) -> None:
    mod = _mod()
    root = _match(tmp_path / "broken-match", {"s_a": {"token": "t", "audits": []}})
    (root / "match.json").write_text("{not json")

    with pytest.raises(mod.MalformedProjectError) as excinfo:
        mod.inventory_project(root)

    assert str(root / "match.json") in str(excinfo.value)


FINAL_MATCH_SLUGS = {
    "blacksmith-handgun-open-2026",
    "bofors-bombardment-2026",
    "ess-black-handgun-2026",
    "hfo-masters-2026",
    "jinglebell-challenge-2026",
    "oden-cup-2026",
    "stockholm-ipsc-open-2026",
    "tallmilan-2025",
    "tallmilan-2026",
    "vads-easter-shoot-2026",
}


def test_the_checked_in_rename_map_lands_on_the_ten_final_slugs() -> None:
    """Every destination is one of the ten matches the migration ends with.

    A typo in a destination name is indistinguishable from a project that
    vanished -- both produce ``project_destination_present`` at verify
    time, during phase 7, with the migration already done. This catches
    it in the repo instead. ``jinglebells-challenge-2026-anton`` maps to
    the singular ``jinglebell-challenge-2026``: the misspelled directory
    name does not survive.
    """
    mod = _mod()
    path = Path(__file__).resolve().parents[1] / "scripts" / "consolidation_rename_map.json"

    rename_map = mod.load_rename_map(path)

    assert set(rename_map.destinations.values()) == FINAL_MATCH_SLUGS
    assert "jinglebells-challenge-2026-anton" in rename_map.destinations
    assert rename_map.destinations["jinglebells-challenge-2026-anton"] == "jinglebell-challenge-2026"
