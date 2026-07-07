"""Audit the R2 bucket for raw videos that are missing a proxy.

For every object under users/*/raw/ (raw uploaded footage), checks that a
corresponding users/*/raw_proxy/<name>.mp4 object exists. Prints a summary and
exits with code 1 if any proxies are missing, so this can gate a deploy.

Read-only - this script never writes or deletes anything.

The proxy key mapping comes from splitsmith.proxy.proxy_key_for so the
derivation stays in sync with the server: raw/<name>.<ext> -> raw_proxy/<name>.mp4.

Credentials are read from the same SPLITSMITH_S3_* env vars the server uses:
  SPLITSMITH_S3_BUCKET            - bucket name
  SPLITSMITH_S3_ENDPOINT_URL      - R2 endpoint (https://<account>.r2.cloudflarestorage.com)
  SPLITSMITH_S3_REGION            - region name (default: auto)
  SPLITSMITH_S3_ACCESS_KEY_ID     - access key
  SPLITSMITH_S3_SECRET_ACCESS_KEY - secret key

Usage:
    uv run python scripts/check_proxy_backfill.py
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

from splitsmith.proxy import proxy_key_for

# Env var names - match server.py exactly.
_ENV_BUCKET = "SPLITSMITH_S3_BUCKET"
_ENV_ENDPOINT = "SPLITSMITH_S3_ENDPOINT_URL"
_ENV_REGION = "SPLITSMITH_S3_REGION"
_ENV_ACCESS_KEY = "SPLITSMITH_S3_ACCESS_KEY_ID"
_ENV_SECRET_KEY = "SPLITSMITH_S3_SECRET_ACCESS_KEY"

# Bucket key segment that marks raw uploaded footage.
_RAW_MARKER = "/raw/"


def _build_client() -> tuple[object, str]:
    """Read SPLITSMITH_S3_* env vars and return (boto3_client, bucket).

    Mirrors _build_hosted_s3_client() in server.py so both use the same
    credential source and client construction pattern.
    """
    bucket = os.environ.get(_ENV_BUCKET, "").strip()
    if not bucket:
        print(f"error: {_ENV_BUCKET} is not set", file=sys.stderr)
        sys.exit(1)

    endpoint = os.environ.get(_ENV_ENDPOINT, "").strip() or None
    region = os.environ.get(_ENV_REGION, "").strip() or "auto"
    access_key = os.environ.get(_ENV_ACCESS_KEY, "").strip()
    secret_key = os.environ.get(_ENV_SECRET_KEY, "").strip()

    if not access_key or not secret_key:
        print(
            f"error: {_ENV_BUCKET} is set but {_ENV_ACCESS_KEY} / {_ENV_SECRET_KEY} are missing",
            file=sys.stderr,
        )
        sys.exit(1)

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    return client, bucket


def _list_all_keys(client: object, bucket: str, prefix: str = "") -> Iterator[str]:
    """Yield every object key in the bucket under the given prefix."""
    paginator = client.get_paginator("list_objects_v2")  # type: ignore[attr-defined]
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            yield obj["Key"]


def _split_at_raw(key: str) -> tuple[str, str] | None:
    """Split a bucket key into (tenant_prefix, relative_raw_path).

    For "users/abc123/raw/match.mp4" returns ("users/abc123/", "raw/match.mp4").
    Returns None when the key does not contain /raw/ (not a raw video object).
    """
    idx = key.find(_RAW_MARKER)
    if idx == -1:
        return None
    tenant_prefix = key[: idx + 1]  # everything up to and including the slash before "raw/"
    relative = key[idx + 1 :]  # "raw/<name>.<ext>"
    return tenant_prefix, relative


def main() -> None:
    client, bucket = _build_client()

    print(f"scanning bucket: {bucket}")
    print("listing all objects ... (this may take a moment for large buckets)")

    all_keys: list[str] = list(_list_all_keys(client, bucket, prefix="users/"))
    print(f"total objects found: {len(all_keys)}")

    # Build a set of all proxy keys for O(1) lookup.
    proxy_key_set: set[str] = {k for k in all_keys if "/raw_proxy/" in k}

    # Find every raw video and check its expected proxy exists.
    raw_keys: list[str] = []
    missing: list[str] = []

    for key in all_keys:
        split = _split_at_raw(key)
        if split is None:
            continue
        # Skip directory markers (zero-byte keys ending in "/").
        if key.endswith("/"):
            continue
        raw_keys.append(key)
        tenant_prefix, relative = split
        try:
            relative_proxy = proxy_key_for(relative)
        except ValueError:
            # relative didn't start with "raw/" - unexpected but skip gracefully.
            continue
        expected_proxy_key = f"{tenant_prefix}{relative_proxy}"
        if expected_proxy_key not in proxy_key_set:
            missing.append(key)

    # Summary
    print()
    print(f"raw videos:     {len(raw_keys)}")
    print(f"have proxy:     {len(raw_keys) - len(missing)}")
    print(f"missing proxy:  {len(missing)}")

    if missing:
        print("\nkeys missing a proxy (re-run generate_proxy for each):")
        for key in sorted(missing):
            print(f"  {key}")
        print(
            "\nresolution: re-queue a generate_proxy job for each missing key via the admin API,"
            " or run the proxy worker locally against the raw file."
        )
        sys.exit(1)
    else:
        print("\nall raw videos have a proxy - backfill is complete.")


if __name__ == "__main__":
    main()
