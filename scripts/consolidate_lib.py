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
    if project_file.exists():
        token = _load_document(project_file).get("shooter_token")

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


def pair_projects(
    before: list[ProjectInventory], after: list[ProjectInventory]
) -> list[tuple[ProjectInventory, ProjectInventory | None]]:
    """Pair each before-project with the after-project that holds its data.

    The migration renames and relocates: a legacy directory becomes a
    shooter inside a merged match, two whole roots disappear, and the
    surviving directory basenames are new. Pairing on the basename
    therefore matches nothing for precisely the projects the migration
    reshaped -- the ones whose data is most at risk.

    Identity, strongest first:

    1. ``match_id`` where both sides carry one. It is the durable id of a
       merged match and survives a move between volumes.
    2. Any ``shooter_token`` the before-project contains. A legacy project
       becomes a shooter inside a merged match and its token comes with
       it, so the token is the identity that spans the reshaping. This is
       the project-level analogue of :func:`_pair_shooters`.
    3. The directory basename, for a project carrying neither a
       ``match_id`` nor any token, which simply stayed where it was.

    Several before-projects may legitimately pair to the same
    after-project: that is what a merge is. A ``None`` counterpart is
    never a pass -- see :func:`unpaired_project_finding`.
    """
    by_match_id: dict[str, ProjectInventory] = {}
    by_token: dict[str, ProjectInventory] = {}
    by_name: dict[str, ProjectInventory] = {}
    for project in after:
        if project.match_id:
            by_match_id.setdefault(project.match_id, project)
        by_name.setdefault(project.root.name, project)
        for shooter in project.shooters:
            if shooter.shooter_token:
                by_token.setdefault(shooter.shooter_token, project)

    pairs: list[tuple[ProjectInventory, ProjectInventory | None]] = []
    for project in before:
        counterpart = by_match_id.get(project.match_id) if project.match_id else None
        if counterpart is None:
            for shooter in project.shooters:
                if shooter.shooter_token and shooter.shooter_token in by_token:
                    counterpart = by_token[shooter.shooter_token]
                    break
        if counterpart is None:
            counterpart = by_name.get(project.root.name)
        pairs.append((project, counterpart))
    return pairs


def unpaired_project_finding(project: ProjectInventory) -> VerifyFinding:
    """A before-project with no counterpart after the migration is blocking.

    Skipping it instead lets every document, byte and unlinked raw file a
    project holds disappear under a report that says nothing is wrong --
    and that report is what the deletion phase is gated on.
    """
    tokens = sorted({s.shooter_token for s in project.shooters if s.shooter_token})
    docs = sum(len(s.audit_docs) for s in project.shooters)
    identity = f"tokens {', '.join(tokens)}" if tokens else "no shooter_token"
    return VerifyFinding(
        check="project_paired",
        subject=str(project.root),
        detail=(
            f"no counterpart project in the after inventory "
            f"(match_id {project.match_id or 'none'}, {identity}); "
            f"{docs} audit doc(s) unaccounted for"
        ),
    )


def verify_reconcile_records(records: list[ReconcileRecord]) -> list[VerifyFinding]:
    """Any reconcile that left its source undeletable blocks the migration."""
    findings: list[VerifyFinding] = []
    for record in records:
        if record.deletable:
            continue
        reasons = "; ".join(f"{v.document} ({v.reason})" for v in record.violations) or "no reason recorded"
        findings.append(
            VerifyFinding(
                check="reconcile_deletable",
                subject=str(record.source),
                detail=f"source is not deletable after reconcile into {record.destination}: {reasons}",
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
