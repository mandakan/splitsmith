"""The account profile (spec 2026-10-08-account-identity-and-shooter-book-design):
the video maker's own brand, set once and drawn on every video they render.

Who you are as a shooter is not here: that is the existing
``user_config.ScoreboardIdentity`` (your SSI shooter id) and your entry in
the shooter book (``shooter_book``). The brand is a logo and a line, the
shape a Look's brand has (``looks.LookBrand``), stored by the same rules
(``look_brand.save_brand_logo``: sniffed, sized, content-named, in a
``brand/`` folder). Renderers never read this: the request layer turns it
into a ``composition.BrandMark`` once per export (:func:`load_brand`).

Local mode stores ``<user config>/account/profile.json`` and the logo in
``account/brand/``. Hosted has its own store (``db``); until then hosted
reads :class:`EmptyAccountProfileStore`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from . import user_config
from .async_bridge import run_sync
from .composition import BrandMark
from .look_brand import BrandError, save_brand_logo
from .looks import BRAND_DIR, LookBrand
from .shooter_book import _safe_file, account_dir

logger = logging.getLogger(__name__)

PROFILE_FILENAME = "profile.json"


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    brand: LookBrand | None = None


class AccountProfileStore(Protocol):
    async def load(self) -> AccountProfile: ...

    async def save(self, profile: AccountProfile) -> None: ...

    async def put_brand_logo(self, data: bytes) -> str:
        """Check and store the brand logo; its content name. ``BrandError``
        with the user's reason when refused."""
        ...

    async def brand_file(self, name: str) -> Path | None: ...


async def resolve_brand(store: AccountProfileStore) -> BrandMark | None:
    """The brand an export draws: ``None`` when there is no logo and no line;
    a logo that is not on this disk is left out, the line kept."""
    profile = await store.load()
    brand = profile.brand
    if brand is None or (not brand.logo and not brand.line):
        return None
    logo = await store.brand_file(brand.logo) if brand.logo else None
    if logo is None and not brand.line:
        return None
    return BrandMark(logo_path=logo, line=brand.line or None)


def brand_digest(brand: BrandMark) -> str:
    """A cache-key token for the brand a card draws: the logo's content name
    (the file is content-named) and the line."""
    import hashlib

    payload = json.dumps(
        {"logo": brand.logo_path.name if brand.logo_path else None, "line": brand.line}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_brand(store: AccountProfileStore | None) -> BrandMark | None:
    """:func:`resolve_brand` from sync code; a store that fails to read is no
    brand, logged, never a failed render."""
    if store is None:
        return None
    try:
        return run_sync(resolve_brand(store))
    except Exception as exc:  # noqa: BLE001 -- a brand is never worth a failed render
        logger.warning("account profile: could not read it (%s); rendering without your brand", exc)
        return None


class JsonAccountProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def _dir(self) -> Path | None:
        return self._root if self._root is not None else account_dir()

    async def load(self) -> AccountProfile:
        folder = self._dir()
        if folder is None:
            return AccountProfile()
        path = folder / PROFILE_FILENAME
        if path.is_symlink():
            logger.warning("account profile: %s is a symlink; ignored", path)
            return AccountProfile()
        try:
            return AccountProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return AccountProfile()
        except Exception as exc:  # noqa: BLE001 -- a bad file reads as no profile
            logger.warning("account profile: ignoring unreadable %s: %s", path, exc)
            return AccountProfile()

    async def save(self, profile: AccountProfile) -> None:
        folder = self._dir()
        if folder is None:
            return
        payload = {"schema_version": 1, **profile.model_dump(mode="json")}
        user_config._atomic_write_text(folder / PROFILE_FILENAME, json.dumps(payload, indent=2))

    async def put_brand_logo(self, data: bytes) -> str:
        folder = self._dir()
        if folder is None:
            raise BrandError("The user settings folder is disabled; nothing can be stored.")
        return save_brand_logo(folder, data)

    async def brand_file(self, name: str) -> Path | None:
        folder = self._dir()
        return _safe_file(folder / BRAND_DIR, name) if folder is not None else None


class EmptyAccountProfileStore:
    """No profile and no writes: hosted until its own store exists."""

    async def load(self) -> AccountProfile:
        return AccountProfile()

    async def save(self, profile: AccountProfile) -> None:
        return None

    async def put_brand_logo(self, data: bytes) -> str:
        raise BrandError("Your brand is not available here yet.")

    async def brand_file(self, name: str) -> Path | None:
        return None


__all__ = [
    "AccountProfile",
    "AccountProfileStore",
    "EmptyAccountProfileStore",
    "brand_digest",
    "JsonAccountProfileStore",
    "load_brand",
    "resolve_brand",
]
