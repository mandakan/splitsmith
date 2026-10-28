"""Account-level features for hosted mode (spec 2026-10-03).

A tier is a named set of :class:`Feature`; code checks features, never
tier names, so adding a tier is configuration. Match-level capabilities
(``ui/capabilities.py``) are a separate axis that depends on a match's
origin; a request needs both. Local mode never consults this module.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class Feature(StrEnum):
    sync = "sync"
    share = "share"
    create_match = "create_match"
    raw_upload = "raw_upload"
    hosted_compute = "hosted_compute"


ALL_FEATURES: frozenset[Feature] = frozenset(Feature)


def _default_tiers() -> dict[str, frozenset[Feature]]:
    return {
        "full": ALL_FEATURES,
        "sharing": frozenset({Feature.sync, Feature.share}),
        "disabled": frozenset(),
    }


#: Exports an account may run at once when neither its tier nor its own
#: override names a number (spec 2026-10-25).
DEFAULT_CONCURRENT_EXPORTS = 1


class AccessConfig(BaseModel):
    tiers: dict[str, frozenset[Feature]] = Field(default_factory=_default_tiers)
    default_tier: str = "full"
    # Exports at once, by tier; a tier missing here gets the default.
    concurrent_exports: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _default_tier_exists(self) -> AccessConfig:
        if self.default_tier not in self.tiers:
            raise ValueError(f"default_tier {self.default_tier!r} is not one of {sorted(self.tiers)}")
        return self


class FeatureRequiredError(Exception):
    """Raised where a hosted account lacks ``feature`` (mapped to a 403)."""

    def __init__(self, feature: Feature) -> None:
        super().__init__(f"feature required: {feature.value}")
        self.feature = feature


#: The job kinds hosted runs for a desktop mirror (``matches.origin ==
#: "desktop"``), whatever the account's features. The desktop owns a mirror's
#: trims, detection and renders and runs them at no cost to us; hosted only
#: derives the audit WAV from a pushed web rendition, seconds of ffmpeg. So
#: the desktop-plus-sync offering never bills hosted ingest or render time,
#: which is what keeps its per-subscriber cost near zero.
MIRROR_JOB_KINDS: frozenset[str] = frozenset({"derive_audio"})


class MirrorComputeRefusedError(FeatureRequiredError):
    """A job kind hosted never runs for a desktop mirror (``MIRROR_JOB_KINDS``).

    A :class:`FeatureRequiredError` so every handler that skips a chained job
    on a missing feature skips this one the same way; the app maps it to its
    own 409, since no tier would unlock it.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(Feature.hosted_compute)
        self.kind = kind
        self.args = (f"{kind} runs on the desktop for a desktop-synced match, never hosted",)


def export_limit(tier: str | None, override: int | None, config: AccessConfig) -> int:
    """How many exports an account may run at once: its own override when
    an admin set one, else its tier's number, else the default."""
    if override is not None:
        return override
    if tier is not None and tier in config.concurrent_exports:
        return config.concurrent_exports[tier]
    return DEFAULT_CONCURRENT_EXPORTS


def features_for(
    tier: str | None, email: str, config: AccessConfig, admin_emails: frozenset[str]
) -> frozenset[Feature]:
    if email.strip().lower() in admin_emails:
        return ALL_FEATURES
    if tier is None or tier not in config.tiers:
        logger.warning("account tier %r is not in the access registry; no features granted", tier)
        return frozenset()
    return config.tiers[tier]


#: Overrides ``access.default_tier`` for new accounts. Read by ``serve``
#: only (the one process that creates accounts), applied on top of the
#: ``SPLITSMITH_CONFIG`` registry, and validated against it: an unknown
#: tier fails boot.
ENV_ACCESS_DEFAULT_TIER = "SPLITSMITH_ACCESS_DEFAULT_TIER"


def access_config() -> AccessConfig:
    """The registry from ``SPLITSMITH_CONFIG`` when set, else the defaults,
    with ``SPLITSMITH_ACCESS_DEFAULT_TIER`` applied on top."""
    from .config import Config
    from .runtime import ENV_CONFIG_FILE

    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    config = Config.load(Path(raw).expanduser()).access if raw else AccessConfig()
    default_tier = os.environ.get(ENV_ACCESS_DEFAULT_TIER, "").strip()
    if not default_tier:
        return config
    return AccessConfig.model_validate({"tiers": config.tiers, "default_tier": default_tier})
