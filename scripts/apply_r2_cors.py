"""Apply a CORS rule to the Splitsmith R2 bucket.

Targets Cloudflare R2 via its S3-compatible API. Must be run once per bucket
(staging + prod) before the frontend starts issuing direct presigned GET
requests. Running it again is a no-op: put_bucket_cors is a full replace, so
the bucket always ends up in the exact state described below.

Credentials are read from the same SPLITSMITH_S3_* env vars the server uses:
  SPLITSMITH_S3_BUCKET            - bucket name
  SPLITSMITH_S3_ENDPOINT_URL      - R2 endpoint (https://<account>.r2.cloudflarestorage.com)
  SPLITSMITH_S3_REGION            - region name (default: auto)
  SPLITSMITH_S3_ACCESS_KEY_ID     - access key
  SPLITSMITH_S3_SECRET_ACCESS_KEY - secret key

Usage:
    uv run python scripts/apply_r2_cors.py
    uv run python scripts/apply_r2_cors.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Allowed origins - edit here when adding new app domains.
# ---------------------------------------------------------------------------
ALLOWED_ORIGINS: list[str] = [
    "https://my.splitsmith.app",  # production
    "https://my.staging.splitsmith.app",  # staging
    "http://localhost:5173",  # local dev (Vite default port)
]

# Full CORS rule applied to the bucket. Range is the critical header for
# video seeking; Content-Range and Accept-Ranges must be exposed so the
# browser can honour partial-content (206) responses.
CORS_RULE: dict = {
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": ALLOWED_ORIGINS,
    "AllowedHeaders": ["Range", "*"],
    "ExposeHeaders": ["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"],
    "MaxAgeSeconds": 3600,
}

# Env var names - match server.py exactly so they can share the same Railway
# variable set without duplication.
_ENV_BUCKET = "SPLITSMITH_S3_BUCKET"
_ENV_ENDPOINT = "SPLITSMITH_S3_ENDPOINT_URL"
_ENV_REGION = "SPLITSMITH_S3_REGION"
_ENV_ACCESS_KEY = "SPLITSMITH_S3_ACCESS_KEY_ID"
_ENV_SECRET_KEY = "SPLITSMITH_S3_SECRET_ACCESS_KEY"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply CORS rule to the Splitsmith R2 bucket.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the intended CORS config without calling put_bucket_cors.",
    )
    args = parser.parse_args()

    cors_config = {"CORSRules": [CORS_RULE]}

    print("CORS configuration to apply:")
    print(json.dumps(cors_config, indent=2))

    if args.dry_run:
        print("\ndry-run: no changes made.")
        return

    client, bucket = _build_client()

    print(f"\napplying to bucket: {bucket}")
    client.put_bucket_cors(Bucket=bucket, CORSConfiguration=cors_config)
    print("done - CORS rule applied successfully.")


if __name__ == "__main__":
    main()
