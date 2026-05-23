"""Copy renderer DTDs into ``tests/fixtures/schemas/`` for validation (#202).

Apple's FCPXML DTD ships inside Final Cut Pro at
``Final Cut Pro.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources/``.
We don't redistribute it; users with FCP installed run this once to
populate ``tests/fixtures/schemas/`` so the validation tests light up.

The xmeml (FCP7 XML) DTD isn't bundled with modern Apple apps; drop a
copy at ``tests/fixtures/schemas/xmeml-v5.dtd`` manually.

Usage:

    uv run python scripts/fetch_dtds.py
    uv run python scripts/fetch_dtds.py --fcp-app /path/to/Final\\ Cut\\ Pro.app
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "tests" / "fixtures" / "schemas"

# splitsmith emits FCPXML 1.10, so that's the version we validate against.
FCPXML_VERSION = "1.10"
FCPXML_TARGET_NAME = f"FCPXML_{FCPXML_VERSION}.dtd"
FCPXML_BUNDLE_NAME = f"FCPXMLv{FCPXML_VERSION.replace('.', '_')}.dtd"

DEFAULT_FCP_APP = Path("/Applications/Final Cut Pro.app")
DTD_RELATIVE_PATH = Path("Contents/Frameworks/Interchange.framework/Versions/A/Resources")


def copy_fcpxml_dtd(fcp_app: Path) -> Path | None:
    """Copy ``FCPXMLv1_10.dtd`` from ``fcp_app`` into the schemas dir.

    Returns the destination path on success, ``None`` when the DTD isn't
    where we expected. Doesn't raise -- this is a best-effort helper.
    """
    src = fcp_app / DTD_RELATIVE_PATH / FCPXML_BUNDLE_NAME
    if not src.exists():
        print(
            f"FCPXML DTD not found at {src}",
            file=sys.stderr,
        )
        return None
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    dst = SCHEMAS_DIR / FCPXML_TARGET_NAME
    shutil.copyfile(src, dst)
    return dst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fcp-app",
        type=Path,
        default=DEFAULT_FCP_APP,
        help="Path to Final Cut Pro.app (defaults to /Applications/Final Cut Pro.app).",
    )
    args = parser.parse_args(argv)

    if not args.fcp_app.exists():
        print(
            f"Final Cut Pro not found at {args.fcp_app}; " "pass --fcp-app to point at your install.",
            file=sys.stderr,
        )
        return 1

    dst = copy_fcpxml_dtd(args.fcp_app)
    if dst is None:
        return 1
    print(f"Copied FCPXML {FCPXML_VERSION} DTD -> {dst.relative_to(REPO_ROOT)}")
    print(
        "FCP7 XML (xmeml) DTD is not bundled with FCP; drop a copy at "
        f"{(SCHEMAS_DIR / 'xmeml-v5.dtd').relative_to(REPO_ROOT)} to enable "
        "FCP7 validation tests."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
