"""Upload exported ONNX artifacts to R2 and refresh the calibration JSON.

End-to-end maintainer workflow (issue #377 -- doc 02 + doc 03):

1. Run ``scripts/export_pann_onnx.py`` and
   ``scripts/export_clap_onnx.py`` to produce ``build/onnx-spike/``.
2. Provide R2 credentials. Two paths:
   - **boto3 path (recommended, no size limit)**: set
     ``R2_ACCESS_KEY_ID`` + ``R2_SECRET_ACCESS_KEY`` env vars
     (create the keys at
     ``https://dash.cloudflare.com/?to=/:account/r2/api-tokens``).
     This routes uploads through R2's S3-compatible endpoint with
     multipart support; needed for the PANN artifact which is
     ~312 MiB (above wrangler's 300 MiB single-PUT cap).
   - **wrangler fallback**: run ``wrangler login`` once. Works for
     artifacts under 300 MiB.
3. Run this script. It SHA256s each artifact, uploads to
   ``splitsmith-models/artifacts/<sha>/<filename>``, then patches
   the ``model_artifacts`` block in
   ``src/splitsmith/data/ensemble_calibration.json``. The next wheel
   build picks up the new SHAs; existing wheels keep working because
   the SHA-keyed URLs are immutable.

The script is idempotent: re-running with the same artifacts is a
no-op (object content matches existing). When an artifact's SHA
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
import os
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
DEFAULT_ACCOUNT_ID = "e1854db8e2a989281305b1b229319c31"
ENV_R2_ACCESS_KEY_ID = "R2_ACCESS_KEY_ID"
ENV_R2_SECRET_ACCESS_KEY = "R2_SECRET_ACCESS_KEY"
ENV_CF_ACCOUNT_ID = "CF_ACCOUNT_ID"


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
    # ``--remote`` targets the real R2 bucket. Without it, wrangler v4+
    # writes to a local emulator under ``.wrangler/state/`` and silently
    # reports "Upload complete"; the artifact never reaches R2. Burned
    # us once on 2026-05-23 -- the bucket was empty after a successful-
    # looking run. The flag is a no-op on older wrangler that didn't
    # have local emulation.
    cmd = [wrangler_bin, "r2", "object", "put", f"{bucket}/{key}", f"--file={file}", "--remote"]
    if dry_run:
        print(f"  DRY-RUN: would run {' '.join(cmd)}")
        return
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"wrangler r2 object put failed (rc={result.returncode})")


def _s3_put(
    s3_client,
    bucket: str,
    key: str,
    file: Path,
    *,
    dry_run: bool,
) -> None:
    """Upload via R2's S3-compatible endpoint. Auto-multipart for large files."""
    if dry_run:
        print(f"  DRY-RUN: would S3 PUT s3://{bucket}/{key} ({file.stat().st_size / 1024 / 1024:.1f} MiB)")
        return
    print(f"  S3 PUT s3://{bucket}/{key}")
    # ``upload_file`` uses S3TransferManager which multipart-uploads
    # files above 8 MiB by default. No special handling needed for the
    # 300 MiB+ PANN artifact.
    s3_client.upload_file(
        Filename=str(file),
        Bucket=bucket,
        Key=key,
        ExtraArgs={"ContentType": "application/octet-stream"},
    )


def _make_s3_client(account_id: str):
    """Build a boto3 S3 client pointed at R2's S3-compatible endpoint."""
    try:
        import boto3
    except ImportError as exc:
        raise SystemExit(
            "boto3 is required for the S3 upload path. Install with "
            "`uv sync --all-groups` or unset R2_ACCESS_KEY_ID to fall "
            "back to the wrangler path."
        ) from exc
    access_key = os.environ[ENV_R2_ACCESS_KEY_ID]
    secret_key = os.environ[ENV_R2_SECRET_ACCESS_KEY]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def _load_calibration(path: Path) -> dict:
    return json.loads(path.read_text())


def _save_calibration(path: Path, payload: dict) -> None:
    # Preserve insertion order; sorting alphabetically would churn the
    # whole file every time the build script runs even when only the
    # ``model_artifacts`` block changed.
    serialised = json.dumps(payload, indent=2) + "\n"
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
    use_s3: bool,
    account_id: str,
    force_upload: bool = False,
) -> dict[str, dict]:
    """Upload-and-pin each artifact; skip the network call when bytes already match.

    The R2 keys are content-addressed (``artifacts/<sha>/<filename>``)
    so a re-export that produced identical bytes lands at the same URL
    that the calibration JSON already pins. In that case we skip the
    S3 PUT entirely -- saves the upload bandwidth and makes it safe to
    invoke this script on every commit. ``force_upload=True`` bypasses
    the skip for cache-wipe / disaster-recovery scenarios.
    """
    base_url = base_url.rstrip("/")
    payload = _load_calibration(calibration_path)
    block = dict(payload.get("model_artifacts") or {})
    existing_base_url = block.get("base_url")
    s3_client = _make_s3_client(account_id) if (use_s3 and not dry_run) else None

    updated: dict[str, dict] = {}
    skipped = 0
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

        existing = block.get(spec.slug) or {}
        already_pinned = existing.get("sha256") == sha and existing.get("filename") == spec.remote
        if already_pinned and not force_upload:
            print("    skip: calibration already pins this SHA; R2 object is immutable")
            updated[spec.slug] = existing
            skipped += 1
            continue

        if use_s3:
            _s3_put(s3_client, bucket, key, local, dry_run=dry_run)
        else:
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
    elif skipped == len(updated):
        print(f"\n  no changes -- all {skipped} artifact(s) already pinned in {calibration_path}")
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
        "--account-id",
        default=os.environ.get(ENV_CF_ACCOUNT_ID, DEFAULT_ACCOUNT_ID),
        help=(
            f"Cloudflare account ID for the S3-compatible endpoint. "
            f"Default: ${ENV_CF_ACCOUNT_ID} env var, then the splitsmith.app account."
        ),
    )
    p.add_argument(
        "--force-wrangler",
        action="store_true",
        help="Skip the boto3/S3 path even when R2 creds are set (useful for testing).",
    )
    p.add_argument(
        "--force-upload",
        action="store_true",
        help=(
            "Upload even when the calibration JSON already pins the local file's "
            "SHA (used for cache-wipe / disaster recovery). Without this flag, "
            "unchanged artifacts are skipped to save bandwidth."
        ),
    )
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

    use_s3 = (
        not args.force_wrangler
        and ENV_R2_ACCESS_KEY_ID in os.environ
        and ENV_R2_SECRET_ACCESS_KEY in os.environ
    )
    if not args.dry_run and not use_s3 and shutil.which(args.wrangler) is None:
        raise SystemExit(
            f"{args.wrangler!r} not on PATH and no R2 S3 credentials set. Either "
            "install wrangler (`npm install -g wrangler && wrangler login`) or "
            f"export {ENV_R2_ACCESS_KEY_ID} + {ENV_R2_SECRET_ACCESS_KEY} for the "
            "boto3 path."
        )
    if not args.calibration.is_file():
        raise SystemExit(f"calibration JSON not found at {args.calibration}")
    if use_s3:
        print(f"upload path: S3 (boto3) via {args.account_id}.r2.cloudflarestorage.com")
    else:
        print("upload path: wrangler")

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
        use_s3=use_s3,
        account_id=args.account_id,
        force_upload=args.force_upload,
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
