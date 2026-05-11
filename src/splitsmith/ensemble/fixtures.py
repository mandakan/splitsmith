"""Single source of truth for the audited fixture corpus.

For most of 2026 every script that touched the calibration corpus kept
its own ``DEFAULT_FIXTURES`` literal -- 11 separate lists across
``scripts/``, in slow drift against ``tests/fixtures/``. Symptoms:

* New fixtures (e.g. shooter ``s0fe3d797``) had to be added to each list
  by hand; missing one silently shrank that script's corpus.
* ``build_ensemble_artifacts.py`` listed ``tallmilan-2026-stage4-s97dcec94``
  but neither ``extract_clap_features.py`` nor ``extract_audio_embeddings.py``
  did, so the artifact build saw a fixture whose feature cache had never
  been generated.

This module replaces those literals with on-disk discovery: every audit
JSON under ``tests/fixtures/`` is parsed once per process and exposed as
a ``Fixture`` with the metadata the scripts care about (mount, shooter,
match, expected_rounds). Filters return the same set you got before
without naming any individual fixture in code.

The corpus *can* still grow without anyone updating this module -- just
drop the audited JSON + WAV alongside the others and it shows up
automatically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

FIXTURES_DIR: Path = Path(__file__).resolve().parents[3] / "tests" / "fixtures"

# Fixtures excluded from Voter E (CLIP visual probe) training because the
# extracted clip is from the wrong stage window -- audio is correct,
# video is not. Audio-only voters (A/B/C) still see these.
WRONG_CLIP_FIXTURES: frozenset[str] = frozenset(
    {
        "stage-shots-blacksmith-2026-stage6-s97dcec94-apple-iphone17pro",
        "stage-shots-tallmilan-2026-stage4-s97dcec94-apple-iphone17pro",
    }
)

# Files in tests/fixtures/ that look like stage-shots-*.json but are not
# audit JSONs. Skip these in discovery.
_NON_AUDIT_SUFFIXES = (
    ".json.bak",
    ".json.before-promote",
    "-promotion-report.json",
    "-candidates.csv",  # not a json, but defensive
    ".peaks-1200.json",
    ".peaks-1500.json",
)

_MATCH_STAGE_RE = re.compile(
    r"^stage-shots-(?P<match>.+?-\d{4})-stage(?P<stage>\d+)(?:-(?P<rest>.+))?$"
)


@dataclass(frozen=True)
class Fixture:
    """Audited fixture descriptor parsed from ``tests/fixtures/<stem>.json``."""

    stem: str
    mount: str | None
    camera_id: str | None
    camera_make: str | None
    camera_model: str | None
    shooter_id: str | None
    match: str | None
    stage_number: int | None
    expected_rounds: int | None
    n_audited_shots: int
    has_wav: bool

    @property
    def audited(self) -> bool:
        return self.n_audited_shots > 0

    @property
    def is_headcam(self) -> bool:
        return self.mount == "head"

    @property
    def camera_class(self) -> str:
        """Same mapping the ensemble runtime uses (mount=head -> headcam)."""
        return "headcam" if self.mount == "head" else "handheld"


def _parse_match_and_stage(stem: str) -> tuple[str | None, int | None]:
    m = _MATCH_STAGE_RE.match(stem)
    if not m:
        return None, None
    try:
        stage = int(m.group("stage"))
    except (TypeError, ValueError):
        stage = None
    return m.group("match"), stage


def _looks_like_audit_json(path: Path) -> bool:
    name = path.name
    if not name.startswith("stage-shots-"):
        return False
    if any(name.endswith(suf) for suf in _NON_AUDIT_SUFFIXES):
        return False
    return path.suffix == ".json"


_DISCOVERY_CACHE: dict[Path, tuple[Fixture, ...]] = {}


def _scan(fixtures_dir: Path) -> Iterator[Fixture]:
    for json_path in sorted(fixtures_dir.glob("stage-shots-*.json")):
        if not _looks_like_audit_json(json_path):
            continue
        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if "beep_time" not in data:
            continue
        stem = json_path.stem
        camera = data.get("camera") or {}
        shooter = data.get("shooter") or {}
        rounds = data.get("stage_rounds") or {}
        match, stage_number = _parse_match_and_stage(stem)
        yield Fixture(
            stem=stem,
            mount=camera.get("mount"),
            camera_id=camera.get("id"),
            camera_make=camera.get("make"),
            camera_model=camera.get("model"),
            shooter_id=shooter.get("id"),
            match=match,
            stage_number=stage_number,
            expected_rounds=rounds.get("expected"),
            n_audited_shots=len(data.get("shots") or []),
            has_wav=json_path.with_suffix(".wav").exists(),
        )


def all_fixtures(fixtures_dir: Path | None = None) -> tuple[Fixture, ...]:
    """Discover every audit JSON under ``fixtures_dir`` and return descriptors.

    When ``fixtures_dir`` is ``None`` the module-level :data:`FIXTURES_DIR`
    is resolved at call time -- this is what lets tests monkeypatch the
    directory.

    Results are cached per resolved directory. Tests that drop a new
    fixture on disk should call :func:`all_fixtures.cache_clear` to force
    rediscovery.

    Files are skipped silently when:

    * the name matches a non-audit pattern (``*.json.bak``, promotion
      reports, peaks summaries);
    * the JSON fails to parse;
    * the JSON has no ``beep_time`` field (a cheap proxy for "is this
      really an audit JSON?").

    Returned tuple is sorted by ``stem`` for a stable iteration order.
    """
    dir_ = fixtures_dir if fixtures_dir is not None else FIXTURES_DIR
    if dir_ not in _DISCOVERY_CACHE:
        _DISCOVERY_CACHE[dir_] = tuple(_scan(dir_))
    return _DISCOVERY_CACHE[dir_]


def _cache_clear() -> None:
    _DISCOVERY_CACHE.clear()


# Mirror ``functools.cache``'s interface so call sites that expect
# ``all_fixtures.cache_clear()`` keep working.
all_fixtures.cache_clear = _cache_clear  # type: ignore[attr-defined]


def audited(
    *,
    mount: str | None = None,
    shooter_id: str | None = None,
    match: str | None = None,
    exclude_wrong_clip: bool = False,
) -> list[Fixture]:
    """Return audited fixtures (``n_audited_shots > 0``), optionally filtered.

    All filter args are AND-combined. Unfiltered ``audited()`` returns
    every fixture with at least one verified shot.
    """
    items = [f for f in all_fixtures() if f.audited]
    if mount is not None:
        items = [f for f in items if f.mount == mount]
    if shooter_id is not None:
        items = [f for f in items if f.shooter_id == shooter_id]
    if match is not None:
        items = [f for f in items if f.match == match]
    if exclude_wrong_clip:
        items = [f for f in items if f.stem not in WRONG_CLIP_FIXTURES]
    return items


def fixture_stems(
    *,
    mount: str | None = None,
    shooter_id: str | None = None,
    match: str | None = None,
    exclude_wrong_clip: bool = False,
) -> list[str]:
    """Drop-in replacement for the old ``DEFAULT_FIXTURES`` literals.

    Same filter args as :func:`audited`; returns just the stems in stable
    sorted order.
    """
    return [
        f.stem
        for f in audited(
            mount=mount,
            shooter_id=shooter_id,
            match=match,
            exclude_wrong_clip=exclude_wrong_clip,
        )
    ]
