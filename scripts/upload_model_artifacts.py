"""Upload exported ONNX artifacts to R2 and refresh the calibration JSON.

End-to-end maintainer workflow (issue #377 -- doc 02 + doc 03):

1. Run ``scripts/export_pann_onnx.py`` and
   ``scripts/export_clap_onnx.py`` to produce ``build/onnx-spike/``.
2. Authenticate Wrangler against the Cloudflare account that owns
   ``splitsmith.app`` (``wrangler login`` -- one-time per machine).
3. Run this script. It SHA256s each artifact, runs
   ``wrangler r2 object put splitsmith-models/artifacts/<sha>/<filename>``,
   then patches the ``model_artifacts`` block in
   ``src/splitsmith/data/ensemble_calibration.json``. The next wheel
   build picks up the new SHAs; existing wheels keep working because
   the SHA-keyed URLs are immutable.

The script is idempotent: re-running with the same artifacts is a
no-op (Wrangler reports "already exists"). When an artifact's SHA
changes (re-export with different upstream weights, opset bump),
the new entry coexists with the old one in R2 -- doc 03 mandates
that old SHAs stay reachable so older wheels keep working.

Run:
    uv run python scripts/upload_model_artifacts.py
    uv run python scripts/upload_model_artifacts.py --dry-run
    uv run python scripts/upload_model_artifacts.py --skip pann_cnn14
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BUCKET = "splitsmith-models"
DEFAULT_BASE_URL = "https://models.splitsmith.app"
DEFAULT_BUILD_DIR = Path("build/onnx-spike")
DEFAULT_CALIBRATION = Path("src/splitsmith/data/ensemble_calibration.json")


@dataclass(frozen=True)
class ArtifactSpec:
    """Per-slug binding: local file + canonical filename on R2."""

    slug: str
    local_filename: str
    # ``remote_filename`` defaults to ``local_filename`` but stays explicit
    # so a future PR can decouple ("clap_audio.onnx" locally,
    # "clap_audio_encoder.onnx" on R2 for slug parity).
    remote_filename: str | None = None

    @property
    def remote(self) -> str:
        return self.remote_filename or self.local_filename


KNOWN_ARTIFACTS: tuple[ArtifactSpec, ...] = (
    ArtifactSpec(slug="pann_cnn14", local_filename="pann_cnn14.onnx"),
    ArtifactSpec(slug="clap_audio_encoder", local_filename="clap_audio.onnx"),
    ArtifactSpec(slug="clap_text_embeddings", local_filename="clap_text_embeddings.npy"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wrangler_put(
    bucket: str,
    key: str,
    file: Path,
    *,
    dry_run: bool,
    wrangler_bin: str,
) -> None:
    cmd = [wrangler_bin, "r2", "object", "put", f"{bucket}/{key}", f"--file={file}"]
    if dry_run:
        print(f"  DRY-RUN: would run {' '.join(cmd)}")
        return
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"wrangler r2 object put failed (rc={result.returncode})")


def _load_calibration(path: Path) -> dict:
    return json.loads(path.read_text())


def _save_calibration(path: Path, payload: dict) -> None:
    serialised = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(serialised)


def upload_all(
    artifacts: Iterable[ArtifactSpec],
    *,
    build_dir: Path,
    bucket: str,
    base_url: str,
    calibration_path: Path,
    dry_run: bool,
    wrangler_bin: str,
) -> dict[str, dict]:
    base_url = base_url.rstrip("/")
    payload = _load_calibration(calibration_path)
    block = dict(payload.get("model_artifacts") or {})
    existing_base_url = block.get("base_url")

    updated: dict[str, dict] = {}
    for spec in artifacts:
        local = build_dir / spec.local_filename
        if not local.is_file():
            raise SystemExit(
                f"missing local artifact {local} -- run the export scripts first "
                f"(scripts/export_{spec.slug.split('_')[0]}_onnx.py)"
            )
        sha = _sha256_file(local)
        size_bytes = local.stat().st_size
        key = f"artifacts/{sha}/{spec.remote}"
        print(f"\n  {spec.slug}: {local} ({size_bytes/1024/1024:.1f} MiB, sha={sha[:12]}...)")
        _wrangler_put(bucket, key, local, dry_run=dry_run, wrangler_bin=wrangler_bin)
        entry = {
            "filename": spec.remote,
            "sha256": sha,
            "size_bytes": size_bytes,
            "url": f"{base_url}/{key}",
        }
        block[spec.slug] = entry
        updated[spec.slug] = entry

    if existing_base_url is not None:
        block.setdefault("base_url", existing_base_url)

    payload["model_artifacts"] = block
    if dry_run:
        print("\n  DRY-RUN: would write the following model_artifacts block:")
        print(json.dumps(block, indent=2))
    else:
        _save_calibration(calibration_path, payload)
        print(f"\n  wrote model_artifacts -> {calibration_path}")
    return updated


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Public URL prefix; default {DEFAULT_BASE_URL}",
    )
    p.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    p.add_argument("--wrangler", default="wrangler", help="Wrangler binary on PATH or absolute path.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute SHA / report what would happen, but don't upload or modify calibration.",
    )
    p.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=[a.slug for a in KNOWN_ARTIFACTS],
        help="Skip a specific slug (repeatable).",
    )
    args = p.parse_args()

    if not args.dry_run and shutil.which(args.wrangler) is None:
        raise SystemExit(
            f"{args.wrangler!r} not on PATH. Install via `npm install -g wrangler`, " "then `wrangler login`."
        )
    if not args.calibration.is_file():
        raise SystemExit(f"calibration JSON not found at {args.calibration}")

    to_upload = [a for a in KNOWN_ARTIFACTS if a.slug not in set(args.skip)]
    if not to_upload:
        raise SystemExit("nothing to upload after --skip filters")

    upload_all(
        to_upload,
        build_dir=args.build_dir,
        bucket=args.bucket,
        base_url=args.base_url,
        calibration_path=args.calibration,
        dry_run=args.dry_run,
        wrangler_bin=args.wrangler,
    )
    if args.dry_run:
        print("\nDry-run complete. Re-run without --dry-run to actually upload.")
    else:
        print(
            "\nDone. Commit the calibration JSON change and ship a new wheel; "
            "slim runtime users pick up the artifacts on first detection."
        )


if __name__ == "__main__":
    main()
