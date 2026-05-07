"""DTD validation helper for the renderer test suites (#202).

Shells out to ``xmllint`` (ships with libxml2; preinstalled on macOS,
common on Linux). The helper is gated -- when ``xmllint`` isn't on PATH
or the requested DTD file is missing, callers skip cleanly instead of
failing the suite.

Usage:

    from tests._dtd import fcpxml_dtd, validate_against_dtd

    @fcpxml_dtd
    def test_my_renderer_emits_valid_fcpxml(tmp_path):
        ...
        validate_against_dtd(out_path, dtd=fcpxml_dtd_path())
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "tests" / "fixtures" / "schemas"

_FCPXML_DTD = SCHEMAS_DIR / "FCPXML_1.10.dtd"
_XMEML_DTD = SCHEMAS_DIR / "xmeml-v5.dtd"


def fcpxml_dtd_path() -> Path:
    """Path to the FCPXML 1.10 DTD; ``Path`` may not exist."""
    return _FCPXML_DTD


def xmeml_dtd_path() -> Path:
    """Path to the xmeml v5 DTD; ``Path`` may not exist."""
    return _XMEML_DTD


def _xmllint_available() -> bool:
    return shutil.which("xmllint") is not None


fcpxml_dtd = pytest.mark.skipif(
    not _xmllint_available() or not _FCPXML_DTD.exists(),
    reason=(
        "FCPXML 1.10 DTD not present at tests/fixtures/schemas/FCPXML_1.10.dtd; "
        "run `uv run python scripts/fetch_dtds.py` to populate from a local "
        "Final Cut Pro install. Or install xmllint."
    ),
)

xmeml_dtd = pytest.mark.skipif(
    not _xmllint_available() or not _XMEML_DTD.exists(),
    reason=(
        "xmeml v5 DTD not present at tests/fixtures/schemas/xmeml-v5.dtd; "
        "drop a copy in (see tests/fixtures/schemas/README.md). Or install "
        "xmllint."
    ),
)


def validate_against_dtd(xml_path: Path, *, dtd: Path) -> None:
    """Validate ``xml_path`` against ``dtd`` via ``xmllint``.

    Raises ``AssertionError`` with the validator's stderr when the file
    fails validation; succeeds silently. The xmllint flags suppress the
    parsed-document echo (``--noout``) and force DTD validation against
    the supplied file rather than any internal subset
    (``--dtdvalid``).
    """
    proc = subprocess.run(
        ["xmllint", "--noout", "--dtdvalid", str(dtd), str(xml_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"DTD validation failed for {xml_path.name} against {dtd.name}:\n"
            f"{proc.stderr.strip() or proc.stdout.strip() or '(no output)'}"
        )
