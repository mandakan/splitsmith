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
        doc = json.loads(project_file.read_text())
        token = doc.get("shooter_token")

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
    raw_dir = root / "raw"
    if raw_dir.is_dir():
        for entry in sorted(raw_dir.iterdir()):
            if entry.name == ".DS_Store":
                continue
            if entry.is_symlink():
                link_targets[entry.name] = str(Path(entry.readlink()))
            if not entry.exists():
                broken_links.append(entry.name)

    return ShooterInventory(
        slug=slug,
        root=root,
        shooter_token=token,
        audit_docs=audit_docs,
        media_counts=media_counts,
        media_bytes=media_bytes,
        link_targets=link_targets,
        broken_links=broken_links,
    )


def inventory_project(root: Path) -> ProjectInventory:
    """Describe a project at ``root``, legacy or merged, in one shape.

    ``.bak`` files are deliberately not audit docs: the migration must
    never promote a stale backup into the position of a real document.
    """
    match_file = root / "match.json"
    if match_file.exists():
        doc = json.loads(match_file.read_text())
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
