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


class AccessConfig(BaseModel):
    tiers: dict[str, frozenset[Feature]] = Field(default_factory=_default_tiers)
    default_tier: str = "full"

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
