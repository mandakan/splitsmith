#!/usr/bin/env python3
"""Drive the one-off consolidation of the match corpus onto X9.

The rules live in ``consolidate_lib`` as pure functions. This module is
the only code that mutates the filesystem, and every mutation is
preceded by a plan the caller can print.

Subcommands:
    inventory  Snapshot projects to build/consolidation/<label>.json
    reconcile  Plan (and with --apply, execute) a source -> destination merge
    verify     Compare two inventories and report every finding

Every reconcile records its outcome -- source, destination, action count,
violations, whether ``--apply`` ran, and the ``deletable`` verdict -- in
``build/consolidation/reconcile-log.json``, keyed by (source,
destination) so a re-run supersedes its own earlier verdict instead of
stacking a stale one beside it. ``verify`` turns a recorded
``deletable: false``, a verdict that was never applied, and a missing or
empty log alike into blocking findings. That log is the artifact
encoding the migration's central rule ("never delete a source that still
holds content the destination lacks") for the human who reads the report
before phase 8 deletes the originals.

``verify`` requires ``--rename-map``: which after-project a
before-project became is declared, never inferred. See
``scripts/consolidation_rename_map.json``.

Usage:
    uv run python scripts/consolidate_matches.py inventory --label phase0 \
        --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
    uv run python scripts/consolidate_matches.py reconcile \
        --source /Volumes/X9/matches/blacksmith-2026 \
        --destination /Volumes/X9/matches/blacksmith-handgun-open-2026/shooters/s_ce10fa76 \
        --reconcile-log build/consolidation/reconcile-log.json
    uv run python scripts/consolidate_matches.py verify --before phase0 --after phase7 \
        --rename-map scripts/consolidation_rename_map.json \
        --reconcile-log build/consolidation/reconcile-log.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from consolidate_lib import (  # noqa: E402
    ProjectInventory,
    ReconcilePlan,
    ReconcileRecord,
    ShooterInventory,
    inventory_project,
    load_rename_map,
    plan_reconcile,
    record_reconcile,
    resolve_projects,
    supersede_records,
    verify_before_inventory_nonempty,
    verify_documents_replaced,
    verify_documents_survived,
    verify_media_not_shrunk,
    verify_no_broken_links,
    verify_project_identity,
    verify_raw_files_survived,
    verify_reconcile_coverage,
    verify_reconcile_records,
    verify_tokens_preserved,
)

REPORT_DIR = Path(__file__).parent.parent / "build" / "consolidation"
RECONCILE_LOG_NAME = "reconcile-log.json"


class UnsafePlanError(RuntimeError):
    """A plan with safety violations must never be applied."""


class AmbiguousShooterError(RuntimeError):
    """A reconcile target resolved to more than one shooter."""


def single_shooter(inventory: ProjectInventory) -> ShooterInventory:
    """The one shooter in ``inventory``, or raise.

    Reconciliation is always shooter-to-shooter. Handing this a match
    root would otherwise silently pick ``shooters[0]`` and merge one
    competitor's data into another's.
    """
    if len(inventory.shooters) != 1:
        slugs = ", ".join(str(s.slug) for s in inventory.shooters)
        raise AmbiguousShooterError(
            f"{inventory.root} holds {len(inventory.shooters)} shooters ({slugs}); "
            f"pass a specific shooters/<slug> directory instead"
        )
    return inventory.shooters[0]


def apply_reconcile(plan: ReconcilePlan, *, dry_run: bool) -> list[str]:
    """Execute ``plan``. Returns one human-readable line per action.

    ``shutil.copy2`` preserves nanosecond mtimes on APFS, which is load-
    bearing: ``sync/plan.py`` skips an upload only when size AND
    mtime_ns match what sync_state recorded, with no content-hash
    fallback, so a copy that loses precision re-uploads every trimmed
    mp4 in the match.
    """
    if plan.violations:
        raise UnsafePlanError(
            "refusing to apply a plan with "
            f"{len(plan.violations)} safety violation(s): "
            + "; ".join(f"{v.document} ({v.reason})" for v in plan.violations)
        )

    performed: list[str] = []
    for action in plan.actions:
        if action.kind == "set_shooter_token":
            performed.append(f"set shooter_token={action.detail} on {action.destination}")
            if not dry_run:
                doc = json.loads(action.destination.read_text())
                doc["shooter_token"] = action.detail
                action.destination.write_text(json.dumps(doc, indent=2) + "\n")
            continue

        performed.append(f"copy {action.source} -> {action.destination}")
        if not dry_run:
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.source, action.destination)
    return performed


def _iter_projects(root: Path) -> list[Path]:
    return [child for child in sorted(root.expanduser().iterdir()) if child.is_dir()]


def write_reconcile_record(record: ReconcileRecord, *, log_path: Path) -> Path:
    """Record one reconcile outcome in ``log_path``, returning its path.

    Every pair a phase reconciles stays in the log -- the human reading it
    before phase 8 needs all of them -- but a pair appears once. Re-running
    a reconcile after fixing a violation supersedes the earlier verdict
    rather than stacking a stale ``deletable: false`` on top of it.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = supersede_records(load_reconcile_records(log_path), record)
    log_path.write_text(json.dumps([json.loads(r.model_dump_json()) for r in records], indent=2) + "\n")
    return log_path


def load_reconcile_records(log_path: Path) -> list[ReconcileRecord]:
    """Every reconcile outcome recorded in ``log_path``, or none.

    A missing log reads as no records, which ``verify_reconcile_records``
    turns into a blocking finding. It must never read as a pass.
    """
    if not log_path.exists():
        return []
    return [ReconcileRecord(**doc) for doc in json.loads(log_path.read_text())]


def reconcile_log_path(args: argparse.Namespace) -> Path:
    """The log this invocation reads or writes."""
    explicit: Path | None = getattr(args, "reconcile_log", None)
    return explicit if explicit is not None else args.report_dir / RECONCILE_LOG_NAME


def cmd_inventory(args: argparse.Namespace) -> None:
    out = args.report_dir / f"{args.label}.json"
    if out.exists() and not args.force:
        print(f"error: {out} already exists; pass --force to overwrite an existing label", file=sys.stderr)
        raise SystemExit(1)

    projects: list[ProjectInventory] = []
    skipped: list[Path] = []
    for root in args.root:
        for project_root in _iter_projects(root):
            if not (project_root / "project.json").exists() and not (project_root / "match.json").exists():
                skipped.append(project_root)
                continue
            projects.append(inventory_project(project_root))

    args.report_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([json.loads(p.model_dump_json()) for p in projects], indent=2) + "\n")
    print(f"inventoried {len(projects)} project(s) -> {out}")
    for project in projects:
        broken = sum(len(s.broken_links) for s in project.shooters)
        docs = sum(len(s.audit_docs) for s in project.shooters)
        flag = f"  BROKEN LINKS: {broken}" if broken else ""
        print(
            f"  {project.root.name:38s} {project.kind:6s} shooters={len(project.shooters)} docs={docs}{flag}"
        )
    if skipped:
        print(f"skipped {len(skipped)} director(ies) lacking project.json/match.json:")
        for path in skipped:
            print(f"  {path}")


def cmd_reconcile(args: argparse.Namespace) -> None:
    source = single_shooter(inventory_project(args.source))
    destination = single_shooter(inventory_project(args.destination))
    plan = plan_reconcile(source, destination)

    for action in plan.actions:
        print(f"  {action.kind}: {action.source} -> {action.destination}")
    for violation in plan.violations:
        print(f"  VIOLATION: {violation.document} -- {violation.reason}")

    applied = False
    try:
        if args.apply:
            for line in apply_reconcile(plan, dry_run=False):
                print(f"  applied: {line}")
            applied = True
    finally:
        # The record is written even when apply refuses the plan: a
        # refused plan is a safety violation, and that is precisely what
        # the report a human reads before deleting has to carry.
        #
        # Without --apply nothing has been copied yet, so `deletable` is a
        # statement about a future state, not about the disk as it stands.
        verdict = "deletable" if applied else "deletable_after_apply"
        print(f"actions={len(plan.actions)} violations={len(plan.violations)} {verdict}={plan.deletable}")
        record = record_reconcile(source, destination, plan, applied=applied)
        print(f"recorded -> {write_reconcile_record(record, log_path=reconcile_log_path(args))}")


def cmd_verify(args: argparse.Namespace) -> None:
    report_dir: Path = args.report_dir
    before = [ProjectInventory(**doc) for doc in json.loads((report_dir / f"{args.before}.json").read_text())]
    after = [ProjectInventory(**doc) for doc in json.loads((report_dir / f"{args.after}.json").read_text())]

    # Blocking findings gate the migration's exit code. `documents_replaced`
    # is deliberately excluded: plan_reconcile lets the destination win where
    # both sides hold a document (the merged copies carry strictly more audit
    # history), so a hash mismatch there is an expected, documented
    # replacement, not a defect. It is reported separately, below, and never
    # folded into `blocking`.
    #
    # Which after-project a before-project became is DECLARED, in the
    # rename map, and never inferred. Nothing in an inventory records it:
    # the basename changes for every project the migration reshapes, and
    # a shooter_token identifies a competitor, not a match. Both were
    # tried and both verified a lost project clean. Every before-project
    # that fails to resolve is itself blocking.
    rename_map = load_rename_map(args.rename_map)
    blocking = verify_before_inventory_nonempty(before)
    pairs, resolve_findings = resolve_projects(before, after, rename_map)
    blocking.extend(resolve_findings)
    # scoreboard_match_id/match_id is the one thing the rename map cannot
    # forge -- a mistyped destination that happens to name a real, different
    # match would otherwise resolve and compare cleanly. Pairs whose
    # identity cannot be verified are still compared (see the docstring on
    # verify_project_identity); pairs whose identity provably disagrees are
    # not.
    pairs, identity_blocking, identity_notes = verify_project_identity(pairs)
    blocking.extend(identity_blocking)

    replaced = []
    for project, counterpart in pairs:
        blocking.extend(verify_documents_survived(project, counterpart))
        blocking.extend(verify_media_not_shrunk(project, counterpart))
        blocking.extend(verify_tokens_preserved(project, counterpart))
        blocking.extend(verify_raw_files_survived(project, counterpart))
        replaced.extend(verify_documents_replaced(project, counterpart))
    for project in after:
        blocking.extend(verify_no_broken_links(project))

    # Whatever the reconciles recorded about deletability outranks a
    # clean before/after diff: a source that held content its destination
    # never received is not deletable, however tidy the inventories look.
    # An absent log is not a clean run either -- it is a check that never
    # ran, and verify_reconcile_records says so. verify_reconcile_coverage
    # closes the other half: a record has to name the project it covers,
    # or one unrelated applied+deletable record satisfies the gate for
    # every project in the corpus.
    log_path = reconcile_log_path(args)
    records = load_reconcile_records(log_path)
    blocking.extend(verify_reconcile_records(records, log_path=log_path))
    blocking.extend(verify_reconcile_coverage(before, rename_map, records))

    out = report_dir / f"verify-{args.before}-vs-{args.after}.json"
    out.write_text(
        json.dumps(
            {
                "blocking": [json.loads(f.model_dump_json()) for f in blocking],
                "replaced": [json.loads(f.model_dump_json()) for f in replaced],
                "identity_notes": [json.loads(f.model_dump_json()) for f in identity_notes],
            },
            indent=2,
        )
        + "\n"
    )

    for finding in blocking:
        print(f"  {finding.check}: {finding.subject} -- {finding.detail}")
    print(f"{len(blocking)} blocking finding(s)")
    print(f"{len(pairs)} project pair(s) resolved through {args.rename_map}")
    print(f"{len(records)} reconcile outcome(s) consulted from {log_path}")

    if replaced:
        print("replaced (not blocking):")
        for finding in replaced:
            print(f"  {finding.check}: {finding.subject} -- {finding.detail}")
    print(f"{len(replaced)} replaced document(s) (not blocking) -> {out}")

    if identity_notes:
        print("identity unverifiable (not blocking):")
        for finding in identity_notes:
            print(f"  {finding.check}: {finding.subject} -- {finding.detail}")
    print(f"{len(identity_notes)} identity note(s) (not blocking) -> {out}")

    if blocking:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_report_dir(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--report-dir",
            type=Path,
            default=REPORT_DIR,
            help=f"Where reports and the reconcile log live (default: {REPORT_DIR}).",
        )

    p_inv = sub.add_parser("inventory")
    p_inv.add_argument("--label", required=True)
    p_inv.add_argument("--root", type=Path, action="append", required=True)
    p_inv.add_argument("--force", action="store_true", help="Overwrite an existing label's inventory file.")
    add_report_dir(p_inv)
    p_inv.set_defaults(func=cmd_inventory)

    def add_reconcile_log(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--reconcile-log",
            type=Path,
            default=None,
            help=f"The reconcile log (default: <report-dir>/{RECONCILE_LOG_NAME}).",
        )

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--source", type=Path, required=True)
    p_rec.add_argument("--destination", type=Path, required=True)
    p_rec.add_argument("--apply", action="store_true", help="Execute the plan. Off by default.")
    add_report_dir(p_rec)
    add_reconcile_log(p_rec)
    p_rec.set_defaults(func=cmd_reconcile)

    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--before", required=True)
    p_ver.add_argument("--after", required=True)
    p_ver.add_argument(
        "--rename-map",
        type=Path,
        required=True,
        help=(
            "JSON object mapping each before-project directory name to the "
            "after-project directory name it is expected to land in. Required: "
            "identity is declared, never inferred."
        ),
    )
    add_report_dir(p_ver)
    add_reconcile_log(p_ver)
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
