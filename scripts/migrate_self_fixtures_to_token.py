"""One-shot migration: scrub PII from fixtures + rename to shooter-token slugs.

Background
----------
Every existing fixture under ``tests/fixtures/`` was promoted from a
project owned by Mathias (SSI shooter id 41643). They carry the legacy
sentinel ``shooter.id == "self"`` and store full local paths like
``/Users/mathias/matches/<match>/raw/<file>.MOV``. Both leak PII once
the repo is public.

This migration:

* Computes the canonical shooter token for SSI id 41643
  (``s97dcec94``).
* Renames every fixture sibling file by inserting ``-<token>`` after
  ``-stage<N>`` -- so multi-shooter promotions of the same match/stage
  no longer collide on filename and the suffix is grep-able as a
  stable shooter prefix.
* Rewrites JSON ``shooter.id`` and ``event_id`` to the token, and
  scrubs ``source`` / ``source_video`` / ``secondary_source`` /
  ``provenance.project_root`` of any ``/Users/<name>/...`` prefix.
* Updates sidecars: ``tests/fixtures/full/*_full.{json,wav}``,
  ``tests/fixtures/full/_sources.yaml``,
  ``tests/fixtures/full/_mining_report.json``,
  ``tests/fixtures/beep_calibration/manifest.yaml`` +
  ``baseline.json``, and
  ``src/splitsmith/data/ensemble_calibration.json``.

Run once on a clean working tree, review ``git status``, commit.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from splitsmith.lab.core import scrub_local_path, shooter_token  # noqa: E402

OWNER_SSI_ID = 41643
TOKEN = shooter_token(OWNER_SSI_ID)
LEGACY_SENTINEL = "self"

FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"
FULL_ROOT = FIXTURES_ROOT / "full"
BEEP_CALIB_ROOT = FIXTURES_ROOT / "beep_calibration"
ENSEMBLE_CALIB = REPO_ROOT / "src" / "splitsmith" / "data" / "ensemble_calibration.json"


_STAGE_RE = re.compile(r"^(stage-shots-.+-stage\d+)(.*)$")


def map_stem(old_stem: str) -> str | None:
    """Return the token-suffixed stem for a legacy fixture stem.

    Returns ``None`` for stems that don't match the canonical pattern
    (``beep-test``, etc.) so callers can leave them alone. Idempotent:
    a stem that already carries the token is returned unchanged so the
    script can be re-run safely.
    """
    m = _STAGE_RE.match(old_stem)
    if not m:
        return None
    base, tail = m.group(1), m.group(2)
    if f"-{TOKEN}" in tail or tail.startswith(f"-{TOKEN}"):
        return old_stem
    return f"{base}-{TOKEN}{tail}"


def map_filename(old_name: str) -> str | None:
    """Map a fixture filename (with extension/suffix) to its new name.

    Splits on the first dot so ``foo.json.bak`` and ``foo.peaks-1500.json``
    both rename correctly.
    """
    if "." in old_name:
        stem, suffix = old_name.split(".", 1)
        suffix = "." + suffix
    else:
        stem, suffix = old_name, ""
    new_stem = map_stem(stem)
    if new_stem is None:
        return None
    return new_stem + suffix


def rewrite_local_path(value: Any) -> Any:
    """Apply :func:`scrub_local_path` element-wise where it makes sense."""
    if isinstance(value, str):
        return scrub_local_path(value)
    return value


def deep_scrub_paths(node: Any) -> Any:
    """Recursively replace every home-dir path string in a JSON tree.

    Catches PII paths embedded in nested blocks like ``history[*].details``
    that the field-by-field rewrites above don't reach.
    """
    if isinstance(node, dict):
        return {k: deep_scrub_paths(v) for k, v in node.items()}
    if isinstance(node, list):
        return [deep_scrub_paths(v) for v in node]
    if isinstance(node, str) and (node.startswith("/Users/") or node.startswith("/home/")):
        return scrub_local_path(node)
    return node


def rewrite_event_id(value: Any) -> Any:
    if isinstance(value, str) and value.endswith(f":{LEGACY_SENTINEL}"):
        return value[: -len(LEGACY_SENTINEL)] + TOKEN
    return value


def rewrite_shooter_block(payload: dict[str, Any]) -> None:
    block = payload.get("shooter")
    if isinstance(block, dict):
        if block.get("id") == LEGACY_SENTINEL:
            block["id"] = TOKEN
        # Drop any leftover PII fields that older promotes wrote.
        for key in ("name", "ssi_shooter_id"):
            block.pop(key, None)


def rewrite_fixture_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply all PII / token rewrites to a primary or derived fixture JSON."""
    rewrite_shooter_block(payload)
    if "event_id" in payload:
        payload["event_id"] = rewrite_event_id(payload["event_id"])
    for key in ("source", "source_video"):
        if key in payload:
            payload[key] = rewrite_local_path(payload[key])
    prov = payload.get("provenance")
    if isinstance(prov, dict):
        prov.pop("project_root", None)
        for key, value in list(prov.items()):
            if isinstance(value, str):
                prov[key] = scrub_local_path(value)
    # Promotion-style derived fixtures embed an ``anchor`` block with a
    # ``fixture_slug`` that references how it was promoted; the slug
    # there is a free-form descriptor, so leave it alone.
    # Final pass: walk the whole tree to catch path PII in nested
    # blocks (history[*].details, candidate metadata, etc.).
    return deep_scrub_paths(payload)


def rewrite_promotion_report(payload: dict[str, Any]) -> dict[str, Any]:
    if "slug" in payload:
        new = map_stem(payload["slug"])
        if new is not None:
            payload["slug"] = new
    if "anchor_slug" in payload:
        # Best effort: only rewrite if it parses as a stage-shots stem.
        new = map_stem(payload["anchor_slug"])
        if new is not None:
            payload["anchor_slug"] = new
    return deep_scrub_paths(payload)


def rewrite_full_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "fixture_stem" in payload:
        new = map_stem(payload["fixture_stem"])
        if new is not None:
            payload["fixture_stem"] = new
    return deep_scrub_paths(payload)


def write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    path.write_text(text, encoding="utf-8")


def rewrite_and_rename(path: Path, transform) -> Path | None:
    """Rewrite a JSON file via ``transform``, then rename it. Returns new path."""
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = transform(payload)
        write_json(path, payload)
    new_name = map_filename(path.name)
    if new_name is None or new_name == path.name:
        return path
    new_path = path.with_name(new_name)
    shutil.move(str(path), str(new_path))
    return new_path


def migrate_main_fixtures() -> None:
    """Rename + rewrite every ``tests/fixtures/stage-shots-*`` file."""
    paths = sorted(FIXTURES_ROOT.glob("stage-shots-*"))
    for path in paths:
        if path.is_dir():
            continue
        name = path.name
        # Sidecars get their own JSON rewrites.
        if name.endswith("-promotion-report.json"):
            rewrite_and_rename(path, rewrite_promotion_report)
            continue
        if name.endswith(".json") and ".peaks-" not in name and not name.endswith(
            (".bak", ".before-promote", ".before-rise-foot")
        ):
            # Could be a primary or derived fixture (whose payload we want
            # to fully rewrite). The .json.bak / .before-* and .peaks-*
            # variants are byte-identical clones we just rename.
            rewrite_and_rename(path, rewrite_fixture_payload)
            continue
        # Everything else: just rename.
        new_name = map_filename(name)
        if new_name and new_name != name:
            shutil.move(str(path), str(path.with_name(new_name)))


def migrate_full_dir() -> None:
    """Rename + rewrite ``tests/fixtures/full/`` JSONs and WAVs."""
    for path in sorted(FULL_ROOT.glob("stage-shots-*")):
        if path.is_dir():
            continue
        if path.suffix == ".json":
            rewrite_and_rename(path, rewrite_full_payload)
        else:
            new_name = map_filename(path.name)
            if new_name and new_name != path.name:
                shutil.move(str(path), str(path.with_name(new_name)))

    sources_yaml = FULL_ROOT / "_sources.yaml"
    if sources_yaml.exists():
        text = sources_yaml.read_text(encoding="utf-8")
        # Preserve the file's leading comments by doing a regex sub on
        # only the keys we care about. yaml.safe_load + safe_dump would
        # strip the doc comment block.
        def _sub(match: re.Match[str]) -> str:
            stem = match.group(1)
            new = map_stem(stem)
            return f"  {new}:" if new else match.group(0)

        text = re.sub(r"^  (stage-shots-[^:]+):", _sub, text, flags=re.MULTILINE)
        sources_yaml.write_text(text, encoding="utf-8")

    mining_report = FULL_ROOT / "_mining_report.json"
    if mining_report.exists():
        payload = json.loads(mining_report.read_text(encoding="utf-8"))
        per = payload.get("per_fixture")
        if isinstance(per, dict):
            payload["per_fixture"] = {
                (map_stem(k) or k): v for k, v in per.items()
            }
        write_json(mining_report, payload)


def migrate_beep_calibration() -> None:
    manifest = BEEP_CALIB_ROOT / "manifest.yaml"
    if manifest.exists():
        text = manifest.read_text(encoding="utf-8")
        text = re.sub(
            r"\bstage-shots-[A-Za-z0-9_-]+",
            lambda m: map_stem(m.group(0)) or m.group(0),
            text,
        )
        manifest.write_text(text, encoding="utf-8")

    baseline = BEEP_CALIB_ROOT / "baseline.json"
    if baseline.exists():
        payload = json.loads(baseline.read_text(encoding="utf-8"))

        def _walk(node: Any) -> Any:
            if isinstance(node, dict):
                return {k: _walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_walk(v) for v in node]
            if isinstance(node, str) and node.startswith("stage-shots-"):
                return map_stem(node) or node
            return node

        write_json(baseline, _walk(payload))


def migrate_ensemble_calibration() -> None:
    if not ENSEMBLE_CALIB.exists():
        return
    payload = json.loads(ENSEMBLE_CALIB.read_text(encoding="utf-8"))

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str) and node.startswith("stage-shots-"):
            return map_stem(node) or node
        return node

    write_json(ENSEMBLE_CALIB, _walk(payload))


def main() -> None:
    print(f"Token for SSI id {OWNER_SSI_ID}: {TOKEN}")
    print(f"Migrating fixtures under {FIXTURES_ROOT}...")
    migrate_main_fixtures()
    migrate_full_dir()
    migrate_beep_calibration()
    migrate_ensemble_calibration()
    print("Done. Run pytest and review git status before committing.")
    # yaml is imported only to confirm it's available -- the migration
    # script doesn't actually need it (we use regex on _sources.yaml /
    # manifest.yaml because both files have hand-authored comments
    # we don't want to lose).
    _ = yaml


if __name__ == "__main__":
    main()
