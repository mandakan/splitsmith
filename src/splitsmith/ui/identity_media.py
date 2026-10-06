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
from pathlib import Path

from ..identity import LOGO_DIR
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


__all__ = ["ensure_local_logo", "logo_storage_key"]
