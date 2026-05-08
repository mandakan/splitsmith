"""Build the labeled beep calibration manifest from existing audit JSONs.

This is the layer-1 inventory step for issue #220 (``beep: improve
detection accuracy``). It walks ``tests/fixtures/*.json``, pulls out
ground-truth beep positions, classifies each fixture by camera mount
and seeds heuristic failure-mode tags, then writes:

    tests/fixtures/beep_calibration/manifest.yaml

The manifest is the input to ``scripts/eval_beep_detector.py`` -- it
defines WHAT to evaluate. The detector itself is unchanged here; this
script never imports ``beep_detect``.

Run::

    uv run python scripts/build_beep_calibration.py
    uv run python scripts/build_beep_calibration.py --dry-run
    uv run python scripts/build_beep_calibration.py --preserve-tags

Hand-edit ``manifest.yaml`` to add fine-grained tags (``cross-bay``,
``steel-fp-observed``, ``ro-chatter``, ``low-spl``, ...) -- these are
the buckets the layer-2 detector work measures against. By default a
re-run will overwrite the tags from the auto-heuristics; pass
``--preserve-tags`` to keep your hand-tagging when re-running.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from splitsmith.beep_calibration import (
    DEFAULT_TOLERANCE_MS,
    BeepCalibrationManifest,
    BeepFixtureEntry,
    auto_tags,
    compute_full_beep_time,
    derive_camera_kind,
    load_manifest,
    read_audit_json,
    save_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
FULL_DIR = FIXTURES_DIR / "full"
MANIFEST_PATH = FIXTURES_DIR / "beep_calibration" / "manifest.yaml"

# Audit JSON suffixes that aren't real fixtures. Anything with one of
# these substrings in the filename is skipped; the substring set comes
# from the existing tests/fixtures/ tree (.bak, before-promote, etc.).
NON_FIXTURE_MARKERS = (
    ".bak",
    ".before-",
    "promotion-report",
    "candidates",
    "peaks-",
)


def is_fixture_audit_json(path: Path) -> bool:
    """Filter out backup / report JSONs that share the fixture directory."""
    if path.suffix != ".json":
        return False
    name = path.name
    return not any(marker in name for marker in NON_FIXTURE_MARKERS)


def build_entry(audit_path: Path) -> BeepFixtureEntry | None:
    """Translate one audit JSON into a manifest entry, or return None.

    Returns ``None`` for JSONs that aren't fixture audits (no
    ``beep_time``, missing matching WAV, etc.) -- callers should treat
    those as "skip silently".
    """
    audit = read_audit_json(audit_path)
    if not isinstance(audit, dict):
        return None
    beep_time = audit.get("beep_time")
    if beep_time is None:
        return None
    stem = audit_path.stem
    clip_wav = audit_path.with_suffix(".wav")
    if not clip_wav.exists():
        return None

    camera_kind = derive_camera_kind(audit.get("camera"))
    camera_id = (audit.get("camera") or {}).get("id")
    tolerance_ms = float(audit.get("tolerance_ms") or DEFAULT_TOLERANCE_MS)

    full_wav_rel: str | None = None
    full_beep: float | None = None
    full_duration: float | None = None
    full_sidecar = FULL_DIR / f"{stem}_full.json"
    full_wav = FULL_DIR / f"{stem}_full.wav"
    fws = audit.get("fixture_window_in_source")
    if full_sidecar.exists() and full_wav.exists() and isinstance(fws, list) and len(fws) == 2:
        sidecar = read_audit_json(full_sidecar)
        full_window = sidecar.get("full_window_in_source")
        if isinstance(full_window, list) and len(full_window) == 2:
            full_beep = compute_full_beep_time(
                fixture_window_in_source=(float(fws[0]), float(fws[1])),
                full_window_in_source=(float(full_window[0]), float(full_window[1])),
                clip_beep_time=float(beep_time),
            )
            full_wav_rel = full_wav.relative_to(FIXTURES_DIR).as_posix()
            full_duration = float(sidecar.get("extracted_seconds") or 0.0) or None

    tags = auto_tags(
        camera_kind=camera_kind,
        ground_truth_in_full=full_beep,
        stage_rounds=audit.get("stage_rounds"),
    )

    return BeepFixtureEntry(
        stem=stem,
        camera_kind=camera_kind,
        camera_id=camera_id,
        clip_wav=clip_wav.relative_to(FIXTURES_DIR).as_posix(),
        ground_truth_in_clip=float(beep_time),
        tolerance_ms=tolerance_ms,
        full_wav=full_wav_rel,
        ground_truth_in_full=full_beep,
        full_duration_s=full_duration,
        tags=tags,
    )


def build_manifest(
    fixtures_dir: Path = FIXTURES_DIR,
    *,
    preserve_tags: bool = False,
    existing: BeepCalibrationManifest | None = None,
) -> BeepCalibrationManifest:
    """Walk ``fixtures_dir`` and return the rebuilt manifest.

    ``preserve_tags=True`` merges hand-edited tags from ``existing`` --
    the auto-heuristic tags are unioned with whatever the user added.
    """
    prior_tags: dict[str, list[str]] = {}
    if preserve_tags and existing is not None:
        prior_tags = {e.stem: list(e.tags) for e in existing.fixtures}

    entries: list[BeepFixtureEntry] = []
    for audit_path in sorted(fixtures_dir.iterdir()):
        if not is_fixture_audit_json(audit_path):
            continue
        entry = build_entry(audit_path)
        if entry is None:
            continue
        if preserve_tags and entry.stem in prior_tags:
            merged = list(dict.fromkeys(entry.tags + prior_tags[entry.stem]))
            entry = entry.model_copy(update={"tags": merged})
        entries.append(entry)
    return BeepCalibrationManifest(fixtures=entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the would-be manifest summary; don't write the YAML.",
    )
    parser.add_argument(
        "--preserve-tags",
        action="store_true",
        help="Keep hand-edited tags when rebuilding (union with auto tags).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
        help=f"Output manifest path (default: {MANIFEST_PATH}).",
    )
    args = parser.parse_args()

    existing = load_manifest(args.manifest) if args.preserve_tags else None
    manifest = build_manifest(preserve_tags=args.preserve_tags, existing=existing)

    print(f"Discovered {len(manifest.fixtures)} fixtures:")
    for entry in manifest.fixtures:
        full = " + full" if entry.full_wav else ""
        tags = ",".join(entry.tags) if entry.tags else "-"
        print(f"  {entry.stem}  ({entry.camera_kind}{full})  tags={tags}")

    if args.dry_run:
        print("\n(dry-run -- not writing)")
        return
    save_manifest(manifest, args.manifest)
    print(f"\nWrote {args.manifest}")


if __name__ == "__main__":
    main()
