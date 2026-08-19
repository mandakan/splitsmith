#!/usr/bin/env python3
"""Pure helpers for the one-off X9 corpus consolidation.

Inventory, reconciliation and verification live here as pure functions
over Pydantic models so they can be tested against ``tmp_path`` trees.
``scripts/consolidate_matches.py`` is the only caller that touches the
real filesystem.

See docs/superpowers/specs/2026-08-19-corpus-consolidation-design.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MEDIA_DIRS = ("trimmed", "audio", "probes", "thumbs", "exports")


class MalformedProjectError(ValueError):
    """A ``project.json`` or ``match.json`` that cannot be read as a document.

    The inventory walk aborts on one, deliberately: an inventory that
    quietly omits a project is the failure this migration exists to
    prevent. What it must never do is abort without naming the file.
    """


def _load_document(path: Path) -> dict:
    """Read one project/match document, naming ``path`` when it is unreadable."""
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise MalformedProjectError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise MalformedProjectError(f"{path}: expected a JSON object, got {type(doc).__name__}")
    return doc


class ShooterInventory(BaseModel):
    """Everything about one shooter's data that must survive the migration."""

    slug: str | None
    root: Path
    shooter_token: str | None
    scoreboard_match_id: str | None = None
    audit_docs: dict[str, str] = Field(default_factory=dict)
    media_counts: dict[str, int] = Field(default_factory=dict)
    media_bytes: dict[str, int] = Field(default_factory=dict)
    link_targets: dict[str, str] = Field(default_factory=dict)
    broken_links: list[str] = Field(default_factory=list)
    raw_files: dict[str, int] = Field(default_factory=dict)


class ProjectInventory(BaseModel):
    """A legacy project or a merged match, described identically."""

    root: Path
    kind: Literal["legacy", "match"]
    match_id: str | None = None
    shooters: list[ShooterInventory] = Field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _inventory_shooter(root: Path, slug: str | None) -> ShooterInventory:
    project_file = root / "project.json"
    token: str | None = None
    scoreboard_match_id: str | None = None
    if project_file.exists():
        doc = _load_document(project_file)
        token = doc.get("shooter_token")
        scoreboard_match_id = doc.get("scoreboard_match_id")

    audit_dir = root / "audit"
    audit_docs = (
        {path.name: _sha256(path) for path in sorted(audit_dir.glob("*.json"))} if audit_dir.is_dir() else {}
    )

    media_counts: dict[str, int] = {}
    media_bytes: dict[str, int] = {}
    for name in MEDIA_DIRS:
        directory = root / name
        if not directory.is_dir():
            media_counts[name] = 0
            media_bytes[name] = 0
            continue
        files = [p for p in directory.rglob("*") if p.is_file()]
        media_counts[name] = len(files)
        media_bytes[name] = sum(p.stat().st_size for p in files)

    link_targets: dict[str, str] = {}
    broken_links: list[str] = []
    raw_files: dict[str, int] = {}
    raw_dir = root / "raw"
    if raw_dir.is_dir():
        for entry in sorted(raw_dir.iterdir()):
            if entry.name == ".DS_Store":
                continue
            if entry.is_symlink():
                link_targets[entry.name] = str(Path(entry.readlink()))
            if not entry.exists():
                broken_links.append(entry.name)
            elif entry.is_file() and not entry.is_symlink():
                raw_files[entry.name] = entry.stat().st_size

    return ShooterInventory(
        slug=slug,
        root=root,
        shooter_token=token,
        scoreboard_match_id=scoreboard_match_id,
        audit_docs=audit_docs,
        media_counts=media_counts,
        media_bytes=media_bytes,
        link_targets=link_targets,
        broken_links=broken_links,
        raw_files=raw_files,
    )


def inventory_project(root: Path) -> ProjectInventory:
    """Describe a project at ``root``, legacy or merged, in one shape.

    ``.bak`` files are deliberately not audit docs: the migration must
    never promote a stale backup into the position of a real document.
    """
    match_file = root / "match.json"
    if match_file.exists():
        doc = _load_document(match_file)
        shooters_dir = root / "shooters"
        shooters = (
            [
                _inventory_shooter(child, child.name)
                for child in sorted(shooters_dir.iterdir())
                if child.is_dir()
            ]
            if shooters_dir.is_dir()
            else []
        )
        return ProjectInventory(root=root, kind="match", match_id=doc.get("match_id"), shooters=shooters)

    return ProjectInventory(root=root, kind="legacy", shooters=[_inventory_shooter(root, None)])


class ReconcileAction(BaseModel):
    """One filesystem change the reconciler wants to make."""

    kind: Literal["copy_audit_doc", "copy_media", "set_shooter_token"]
    source: Path
    destination: Path
    detail: str = ""


class SafetyViolation(BaseModel):
    """A document that would be lost if the source were deleted."""

    source: Path
    document: str
    reason: str


class ReconcilePlan(BaseModel):
    """Actions to run, plus whether the source may then be deleted."""

    actions: list[ReconcileAction] = Field(default_factory=list)
    violations: list[SafetyViolation] = Field(default_factory=list)
    deletable: bool = False


def plan_reconcile(source: ShooterInventory, destination: ShooterInventory) -> ReconcilePlan:
    """Plan the merge of one source shooter into its destination.

    Rules, in order:

    1. ``shooter_token`` is carried over when the source has one and the
       destination does not. An existing destination token is never
       overwritten.
    2. Audit docs: the destination wins where both sides have one -- the
       merged copies were measured to carry strictly more ``audit_events``
       with later timestamps. Where only the source has one, copy it in.
       ``.bak`` files are never counterparts and never promoted.
    3. Media: union, destination wins on name collision.
    4. The source may be deleted only if every document it holds has a
       counterpart in the destination once these actions are applied.
       ``raw_files`` (real files sitting in ``raw/``, not symlinks) are
       never copied by this plan, but a source-only raw file is still
       content the destination lacks -- it always blocks deletion.
    """
    plan = ReconcilePlan()

    if source.shooter_token and not destination.shooter_token:
        plan.actions.append(
            ReconcileAction(
                kind="set_shooter_token",
                source=source.root / "project.json",
                destination=destination.root / "project.json",
                detail=source.shooter_token,
            )
        )

    for name in sorted(source.audit_docs):
        if name in destination.audit_docs:
            continue
        plan.actions.append(
            ReconcileAction(
                kind="copy_audit_doc",
                source=source.root / "audit" / name,
                destination=destination.root / "audit" / name,
                detail="source-only document",
            )
        )

    for media_dir in MEDIA_DIRS:
        source_dir = source.root / media_dir
        if not source_dir.is_dir():
            continue
        destination_dir = destination.root / media_dir
        existing = {p.name for p in destination_dir.iterdir()} if destination_dir.is_dir() else set()
        for entry in sorted(source_dir.iterdir()):
            if not entry.is_file() or entry.name in existing:
                continue
            plan.actions.append(
                ReconcileAction(
                    kind="copy_media",
                    source=entry,
                    destination=destination_dir / entry.name,
                    detail=media_dir,
                )
            )

    planned_docs = {a.destination.name for a in plan.actions if a.kind == "copy_audit_doc"}
    for name in sorted(source.audit_docs):
        if name not in destination.audit_docs and name not in planned_docs:
            plan.violations.append(
                SafetyViolation(
                    source=source.root,
                    document=name,
                    reason="present in source, absent from destination, not scheduled for copy",
                )
            )

    for name in sorted(source.raw_files):
        if name not in destination.raw_files:
            plan.violations.append(
                SafetyViolation(
                    source=source.root,
                    document=name,
                    reason="unlinked raw footage present in source, absent from destination",
                )
            )

    plan.deletable = not plan.violations
    return plan


class ReconcileRecord(BaseModel):
    """What one reconcile decided, in the form a human reads before deleting.

    ``deletable`` is the migration's central rule -- never delete a source
    that still holds content the destination lacks -- and it has to
    survive the run that computed it, or nothing a reviewer opens encodes
    the safety property.
    """

    source: Path
    destination: Path
    applied: bool
    action_count: int
    violations: list[SafetyViolation] = Field(default_factory=list)
    deletable: bool


def record_reconcile(
    source: ShooterInventory,
    destination: ShooterInventory,
    plan: ReconcilePlan,
    *,
    applied: bool,
) -> ReconcileRecord:
    """Describe one reconcile outcome for the phase's report file."""
    return ReconcileRecord(
        source=source.root,
        destination=destination.root,
        applied=applied,
        action_count=len(plan.actions),
        violations=list(plan.violations),
        deletable=plan.deletable,
    )


class VerifyFinding(BaseModel):
    """One thing that is wrong. An empty list of these means a pass."""

    check: str
    subject: str
    detail: str


def _pair_shooters(
    before: ProjectInventory, after: ProjectInventory
) -> list[tuple[ShooterInventory, ShooterInventory | None]]:
    """Pair by shooter_token where available, else by slug.

    Tokens are stable across the migration and slugs are not (a legacy
    project has no slug until it becomes a shooter), so the token is the
    stronger key when both sides carry one.
    """
    by_token = {s.shooter_token: s for s in after.shooters if s.shooter_token}
    by_slug = {s.slug: s for s in after.shooters if s.slug}
    pairs: list[tuple[ShooterInventory, ShooterInventory | None]] = []
    for shooter in before.shooters:
        counterpart = None
        if shooter.shooter_token:
            counterpart = by_token.get(shooter.shooter_token)
        if counterpart is None and shooter.slug:
            counterpart = by_slug.get(shooter.slug)
        pairs.append((shooter, counterpart))
    return pairs


def _subject(shooter: ShooterInventory) -> str:
    return shooter.shooter_token or shooter.slug or str(shooter.root)


def verify_documents_survived(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]:
    """Every audit doc that existed before must exist after."""
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if counterpart is None:
            findings.append(
                VerifyFinding(
                    check="documents_survived",
                    subject=_subject(shooter),
                    detail=f"no counterpart shooter found in {after.root}",
                )
            )
            continue
        lost = sorted(set(shooter.audit_docs) - set(counterpart.audit_docs))
        if lost:
            findings.append(
                VerifyFinding(
                    check="documents_survived",
                    subject=_subject(shooter),
                    detail=f"missing after migration: {', '.join(lost)}",
                )
            )
    return findings


def verify_documents_replaced(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]:
    """Audit docs present on both sides whose content hash differs.

    Not blocking: ``plan_reconcile`` deliberately lets the destination's
    copy win when both sides carry a document, because merged copies were
    measured to carry strictly more audit history. A hash mismatch here is
    the "documented newer replacement" half of the spec sentence -- it
    exists so a human can confirm the replacement is that, not silent
    corruption of a same-named file during the migration.
    """
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if counterpart is None:
            continue
        for name in sorted(shooter.audit_docs):
            if name not in counterpart.audit_docs:
                continue
            before_hash = shooter.audit_docs[name]
            after_hash = counterpart.audit_docs[name]
            if before_hash != after_hash:
                findings.append(
                    VerifyFinding(
                        check="documents_replaced",
                        subject=_subject(shooter),
                        detail=f"{name}: {before_hash[:12]} before, {after_hash[:12]} after",
                    )
                )
    return findings


def verify_media_not_shrunk(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]:
    """Per media directory, the destination must hold at least as many files and bytes.

    Both halves are load-bearing, which is why the spec's verification
    list names both: bytes alone cannot see two clips replaced by one of
    their combined size, and counts alone cannot see a truncated copy.
    """
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if counterpart is None:
            for media_dir in MEDIA_DIRS:
                was_files = shooter.media_counts.get(media_dir, 0)
                was_bytes = shooter.media_bytes.get(media_dir, 0)
                if was_files or was_bytes:
                    findings.append(
                        VerifyFinding(
                            check="media_not_shrunk",
                            subject=_subject(shooter),
                            detail=(
                                f"{media_dir}: {was_files} file(s)/{was_bytes} bytes before, "
                                f"no counterpart shooter after migration"
                            ),
                        )
                    )
            continue
        for media_dir in MEDIA_DIRS:
            was_files = shooter.media_counts.get(media_dir, 0)
            now_files = counterpart.media_counts.get(media_dir, 0)
            was_bytes = shooter.media_bytes.get(media_dir, 0)
            now_bytes = counterpart.media_bytes.get(media_dir, 0)
            if now_files < was_files or now_bytes < was_bytes:
                findings.append(
                    VerifyFinding(
                        check="media_not_shrunk",
                        subject=_subject(shooter),
                        detail=(
                            f"{media_dir}: {was_files} file(s)/{was_bytes} bytes before, "
                            f"{now_files} file(s)/{now_bytes} bytes after"
                        ),
                    )
                )
    return findings


def verify_raw_files_survived(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]:
    """Every real file under ``raw/`` that existed before must exist after.

    ``plan_reconcile`` already refuses to call a source deletable while it
    holds a raw file the destination lacks. This is the independent check
    that runs after the fact, over the inventories alone -- without it, a
    source that never went through reconcile at all (or was reconciled
    against the wrong destination) could still pass verify clean.
    """
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if counterpart is None:
            if shooter.raw_files:
                findings.append(
                    VerifyFinding(
                        check="raw_files_survived",
                        subject=_subject(shooter),
                        detail=(
                            f"unlinked raw footage present before ({', '.join(sorted(shooter.raw_files))}), "
                            f"no counterpart shooter after migration"
                        ),
                    )
                )
            continue
        lost = sorted(set(shooter.raw_files) - set(counterpart.raw_files))
        if lost:
            findings.append(
                VerifyFinding(
                    check="raw_files_survived",
                    subject=_subject(shooter),
                    detail=f"unlinked raw footage missing after migration: {', '.join(lost)}",
                )
            )
    return findings


def verify_no_broken_links(after: ProjectInventory) -> list[VerifyFinding]:
    """No shooter may hold a raw/ entry that does not resolve."""
    return [
        VerifyFinding(
            check="no_broken_links",
            subject=_subject(shooter),
            detail=f"broken: {', '.join(shooter.broken_links)}",
        )
        for shooter in after.shooters
        if shooter.broken_links
    ]


class MalformedRenameMapError(ValueError):
    """A rename map that cannot be read as an object of directory names."""


class RenameMap(BaseModel):
    """Declared identity: which after-project each before-project lands in.

    Keys and values are directory *names*, not paths: the before side
    spans three roots and the after side is one, so a name is the only
    thing both sides express. Several before-names may share a
    destination -- three legacy blacksmith projects become three shooters
    in one merged match -- but a name never resolves to two destinations.
    """

    destinations: dict[str, str] = Field(default_factory=dict)


def load_rename_map(path: Path) -> RenameMap:
    """Read a rename map from ``path``, naming it when it is unreadable."""
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise MalformedRenameMapError(f"{path}: no such rename map") from exc
    except json.JSONDecodeError as exc:
        raise MalformedRenameMapError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(doc, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in doc.items()
    ):
        raise MalformedRenameMapError(
            f"{path}: expected a JSON object mapping before-project name to after-project name"
        )
    return RenameMap(destinations=doc)


def resolve_projects(
    before: list[ProjectInventory], after: list[ProjectInventory], rename_map: RenameMap
) -> tuple[list[tuple[ProjectInventory, ProjectInventory]], list[VerifyFinding]]:
    """Resolve each before-project against the after-project it declares.

    Identity is declared, never inferred. Two inferences were tried and
    both were unsound:

    - The directory basename skipped every project the migration renames
      or relocates, which is exactly the set phase 8 deletes.
    - Any shared ``shooter_token`` pairs a *shooter* to a *match*. Four
      tokens span ten matches, so legacy ``bofors-bombardment-2026``
      resolved to ``blacksmith-handgun-open-2026`` purely because the
      same competitor shot both; audit docs are named ``stageN.json`` in
      every match, so every document looked present and a lost project
      verified clean.

    Nothing in an inventory records which match a directory became. That
    fact lives in the migration plan, so it is supplied as data.

    Every unresolved before-project produces a blocking finding. The
    caller compares only the pairs returned here, so a project that fails
    to resolve is never silently skipped.
    """
    by_name: dict[str, list[ProjectInventory]] = {}
    for project in after:
        by_name.setdefault(project.root.name, []).append(project)

    pairs: list[tuple[ProjectInventory, ProjectInventory]] = []
    findings: list[VerifyFinding] = []
    for project in before:
        name = project.root.name
        destination = rename_map.destinations.get(name)
        if destination is None:
            findings.append(
                VerifyFinding(
                    check="project_mapped",
                    subject=str(project.root),
                    detail=(
                        f"no entry for {name!r} in the rename map "
                        f"({_project_identity(project)}); "
                        f"{_project_doc_count(project)} audit doc(s) unaccounted for"
                    ),
                )
            )
            continue

        candidates = by_name.get(destination, [])
        if not candidates:
            findings.append(
                VerifyFinding(
                    check="project_destination_present",
                    subject=str(project.root),
                    detail=(
                        f"the rename map sends {name!r} to {destination!r}, "
                        f"which is absent from the after inventory "
                        f"({_project_identity(project)}); "
                        f"{_project_doc_count(project)} audit doc(s) unaccounted for"
                    ),
                )
            )
            continue
        if len(candidates) > 1:
            roots = ", ".join(str(candidate.root) for candidate in candidates)
            findings.append(
                VerifyFinding(
                    check="project_destination_ambiguous",
                    subject=str(project.root),
                    detail=f"the rename map sends {name!r} to {destination!r}, which names {roots}",
                )
            )
            continue

        pairs.append((project, candidates[0]))

    before_names = {project.root.name for project in before}
    for key in sorted(rename_map.destinations):
        if key not in before_names:
            findings.append(
                VerifyFinding(
                    check="rename_map_unmatched",
                    subject=key,
                    detail=(
                        f"the rename map declares {key!r} -> {rename_map.destinations[key]!r}, but no "
                        f"before-project named {key!r} exists in the before inventory -- an omitted "
                        f"--root at inventory time makes a project invisible, not absent"
                    ),
                )
            )
    return pairs, findings


def verify_before_inventory_nonempty(before: list[ProjectInventory]) -> list[VerifyFinding]:
    """An empty before-inventory is never a clean migration -- it's an unread file.

    A truncated or accidentally-overwritten ``before.json`` decodes to
    ``[]`` exactly like a real zero-project corpus would. Every other
    check here compares before against after; with nothing on the before
    side there is nothing to compare, and the gate passes vacuously.
    """
    if before:
        return []
    return [
        VerifyFinding(
            check="before_inventory_nonempty",
            subject="<before inventory>",
            detail="the before inventory holds 0 projects; refusing to verify a migration against nothing",
        )
    ]


def _project_match_identity(project: ProjectInventory) -> str | None:
    """The one match id this project claims, or None if it cannot say.

    A merged match's own ``match_id`` wins. A legacy project has no
    match.json, so its single shooter's ``scoreboard_match_id`` stands in.
    """
    if project.match_id:
        return project.match_id
    ids = {s.scoreboard_match_id for s in project.shooters if s.scoreboard_match_id}
    if len(ids) == 1:
        return next(iter(ids))
    return None


def verify_project_identity(
    pairs: list[tuple[ProjectInventory, ProjectInventory]],
) -> tuple[list[tuple[ProjectInventory, ProjectInventory]], list[VerifyFinding], list[VerifyFinding]]:
    """Cross-check each resolved pair against ``scoreboard_match_id``/``match_id``.

    The rename map declares identity, but nothing stops the map itself
    from naming the wrong destination -- a mistyped entry that happens to
    resolve to a real, different match compares cleanly otherwise: audit
    docs are named identically in every match, and a shared shooter_token
    survives the swap. The match id is the one thing the map cannot forge.

    A disagreement is blocking: the map is provably pointing somewhere
    wrong. An identity that cannot be checked because the field is unset
    on either side is reported as a separate, non-blocking note rather
    than blocking outright -- ``jinglebell-challenge-2026`` has no
    ``scoreboard_match_id`` in this corpus at all (its merge already needs
    an explicit ``--name`` for the same reason), and a project unique on
    disk carries little of the risk this check exists for.
    """
    verified: list[tuple[ProjectInventory, ProjectInventory]] = []
    blocking: list[VerifyFinding] = []
    notes: list[VerifyFinding] = []
    for project, destination in pairs:
        before_id = _project_match_identity(project)
        after_id = _project_match_identity(destination)
        if before_id is not None and after_id is not None and before_id != after_id:
            blocking.append(
                VerifyFinding(
                    check="project_identity_mismatch",
                    subject=str(project.root),
                    detail=(
                        f"{project.root.name!r} resolves to {destination.root.name!r}, but their match "
                        f"ids disagree: {before_id!r} (before) vs {after_id!r} (after) -- unforgeable "
                        f"by the rename map"
                    ),
                )
            )
            continue
        if before_id is None or after_id is None:
            notes.append(
                VerifyFinding(
                    check="project_identity_unverifiable",
                    subject=str(project.root),
                    detail=(
                        f"{project.root.name!r} resolves to {destination.root.name!r}, but "
                        f"scoreboard_match_id/match_id is unset on the "
                        f"{'before' if before_id is None else 'after'} side; identity could not be "
                        f"cross-checked"
                    ),
                )
            )
        verified.append((project, destination))
    return verified, blocking, notes


def verify_reconcile_coverage(
    before: list[ProjectInventory], rename_map: RenameMap, records: list[ReconcileRecord]
) -> list[VerifyFinding]:
    """Every before-project the map relocates must have its own reconcile record.

    ``verify_reconcile_records`` only judges the verdicts of whatever
    records exist in the log -- nothing ties a record to the project it
    claims to cover, so one unrelated ``applied: true, deletable: true``
    record satisfied the gate for every project in the corpus. A
    self-mapping project (its declared destination is its own name) never
    runs through ``reconcile`` and needs none; every other project must
    have a record whose ``source`` is its own root.
    """
    covered = {record.source for record in records}
    findings: list[VerifyFinding] = []
    for project in before:
        name = project.root.name
        destination = rename_map.destinations.get(name)
        if destination is None or destination == name:
            continue
        if project.root not in covered:
            findings.append(
                VerifyFinding(
                    check="reconcile_covers_project",
                    subject=str(project.root),
                    detail=(
                        f"the rename map sends {name!r} to {destination!r}, but no reconcile record "
                        f"has source={project.root}; the deletion gate cannot vouch for it"
                    ),
                )
            )
    return findings


def _project_identity(project: ProjectInventory) -> str:
    tokens = sorted({s.shooter_token for s in project.shooters if s.shooter_token})
    identity = f"tokens {', '.join(tokens)}" if tokens else "no shooter_token"
    return f"match_id {project.match_id or 'none'}, {identity}"


def _project_doc_count(project: ProjectInventory) -> int:
    return sum(len(s.audit_docs) for s in project.shooters)


def supersede_records(records: list[ReconcileRecord], record: ReconcileRecord) -> list[ReconcileRecord]:
    """Add ``record`` to ``records``, replacing any earlier one for its pair.

    A reconcile is keyed by ``(source, destination)``. Re-running one
    after fixing a violation used to append a second record beside the
    stale ``deletable: false``, which blocked the gate forever; the only
    escape was deleting the log, and a deleted log used to pass. The
    later run is the current truth about that pair, and the position is
    kept so a phase still reads in the order it was run.
    """
    key = (record.source, record.destination)
    if any((r.source, r.destination) == key for r in records):
        return [record if (r.source, r.destination) == key else r for r in records]
    return [*records, record]


def verify_reconcile_records(records: list[ReconcileRecord], *, log_path: Path) -> list[VerifyFinding]:
    """The reconcile log's own verdicts, as blocking findings.

    Three ways this gate fails open, all closed here:

    - No records at all. A missing or emptied log is not a clean run, it
      is a check that never ran, and this is the only place that can say
      so -- the before/after inventories look identical either way.
    - A recorded ``deletable: false``. The migration's central rule.
    - A ``deletable: true`` that was only ever planned. Without
      ``--apply`` nothing has been copied, so the verdict describes a
      state the disk never reached.
    """
    if not records:
        return [
            VerifyFinding(
                check="reconcile_log_present",
                subject=str(log_path),
                detail=(
                    "no reconcile outcomes recorded; the log is missing or empty, "
                    "which is a check that never ran, not a clean run"
                ),
            )
        ]

    findings: list[VerifyFinding] = []
    for record in records:
        if not record.deletable:
            reasons = (
                "; ".join(f"{v.document} ({v.reason})" for v in record.violations) or "no reason recorded"
            )
            findings.append(
                VerifyFinding(
                    check="reconcile_deletable",
                    subject=str(record.source),
                    detail=f"source is not deletable after reconcile into {record.destination}: {reasons}",
                )
            )
            continue
        if not record.applied:
            findings.append(
                VerifyFinding(
                    check="reconcile_applied",
                    subject=str(record.source),
                    detail=(
                        f"reconcile into {record.destination} was planned but never applied: "
                        f"{record.action_count} action(s) outstanding, so the destination "
                        f"never received them"
                    ),
                )
            )
    return findings


def verify_tokens_preserved(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]:
    """A shooter that had a token must still have it."""
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if not shooter.shooter_token:
            continue
        if counterpart is None or counterpart.shooter_token != shooter.shooter_token:
            findings.append(
                VerifyFinding(
                    check="tokens_preserved",
                    subject=_subject(shooter),
                    detail=f"shooter_token {shooter.shooter_token} not present after migration",
                )
            )
    return findings
