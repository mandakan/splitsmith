"""The shooter's logo file where a render can read it (#1243).

Locally the file sits under ``<shooter>/identity/``. Hosted, the desktop
pushed it over the sync media channel to
``matches/<match_id>/shooters/<slug>/identity/<name>`` and a render runs
in a container whose disk never saw it, so :func:`ensure_local_logo`
mirrors it down the way ``ui.audio._try_pull_trim_from_storage`` mirrors
a trim: best effort, a storage hiccup is a log line and a card without a
logo, never a failed render.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from ..identity import LOGO_DIR, ResolvedIdentity, resolve_identity
from ..looks import Look
from ..match_project import MatchProject

logger = logging.getLogger(__name__)


def logo_storage_key(match_id: str, slug: str, name: str) -> str:
    """The object key the push writes a logo under; the mirror-down reads it."""
    return f"matches/{match_id}/shooters/{slug}/{LOGO_DIR}/{name}"


def ensure_local_logo(project: MatchProject, shooter_root: Path) -> Path | None:
    """The shooter's logo on local disk, or ``None``.

    The local file when it exists; else, with storage bound to the
    project (hosted), the object under the project's storage scope
    (``<scope>/identity/<name>``, the same shape the push writes) copied
    into place; else ``None``. Nothing here raises for storage: the
    caller draws without the logo.
    """
    name = project.identity.logo
    if name is None:
        return None
    local = shooter_root / LOGO_DIR / name
    if local.exists() and local.stat().st_size > 0:
        return local
    storage = project._storage  # type: ignore[attr-defined]
    scope = project._storage_scope  # type: ignore[attr-defined]
    if storage is None or scope is None:
        return None
    key = f"{scope}/{LOGO_DIR}/{name}"
    try:
        if not storage.exists(key):
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        with storage.open_stream(key) as src, local.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return local
    except Exception as exc:  # noqa: BLE001 -- a logo is never worth a failed render
        logger.info("identity: logo pull from %s failed: %s", key, exc)
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        return None


def resolved_identity_for(
    project: MatchProject,
    shooter_root: Path,
    *,
    look: Look,
    index: int,
    label: str,
    match_logo: Path | None = None,
) -> ResolvedIdentity:
    """The shooter's identity as a render draws it: the Look's defaults
    applied, the logo brought to local disk (hosted) and dropped when the
    file is not there, so a template is never handed a path that does
    not exist."""
    resolved = resolve_identity(
        label=label,
        identity=project.identity,
        index=index,
        look=look,
        shooter_root=None,
        match_logo=match_logo,
    )
    logo = ensure_local_logo(project, shooter_root)
    return replace(resolved, logo_path=logo if logo is not None else match_logo)


def grid_identities(
    bundles: Sequence[object], *, look: Look, match_logo: Path | None = None
) -> dict[str, ResolvedIdentity]:
    """Resolved identities for a grid, keyed by tile label, the slot index
    following the grid's own order (alphabetical by label, filler tiles
    included). A bundle whose project could not be read has no identity
    but keeps its slot, so the others' default accents do not shift."""
    out: dict[str, ResolvedIdentity] = {}
    ordered = sorted(bundles, key=lambda b: b.label)  # type: ignore[attr-defined]
    for index, bundle in enumerate(ordered):
        project = getattr(bundle, "project", None)
        if project is None:
            continue
        out[bundle.label] = resolved_identity_for(  # type: ignore[attr-defined]
            project,
            bundle.project_root,  # type: ignore[attr-defined]
            look=look,
            index=index,
            label=bundle.label,  # type: ignore[attr-defined]
            match_logo=match_logo,
        )
    return out


__all__ = ["ensure_local_logo", "grid_identities", "logo_storage_key", "resolved_identity_for"]
