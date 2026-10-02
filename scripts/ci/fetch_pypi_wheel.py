"""Download the splitsmith wheel PyPI serves for one version.

The Linux desktop bundle ships the published wheel, never one built from a
checkout: publish-pypi.yml bakes the YouTube OAuth client into the wheel,
and a checkout has empty constants. PyPI's JSON can lag the upload by a
minute, and pypi.org itself can blip right after a publish (a 5xx, a
URLError, a socket timeout), so a missing version or a transient failure
is retried before the job fails. Any other HTTP error (a 403, say) is not
a transient condition and raises immediately.

    uv run --no-project python scripts/ci/fetch_pypi_wheel.py 0.53.0 build/wheel
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

JSON_URL = "https://pypi.org/pypi/splitsmith/{version}/json"


class NoWheel(RuntimeError):
    pass


class NotYetPublished(RuntimeError):
    pass


class ShaMismatch(RuntimeError):
    pass


def pick_wheel(release: dict) -> tuple[str, str, str]:
    for f in release.get("urls", []):
        if f.get("packagetype") == "bdist_wheel" and f["filename"].endswith("-py3-none-any.whl"):
            return f["filename"], f["url"], f["digests"]["sha256"]
    raise NoWheel("PyPI lists no py3-none-any wheel for this version")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def fetch(
    version: str,
    out_dir: Path,
    *,
    get_json: Callable[[str], dict | None] = _get_json,
    get_bytes: Callable[[str], bytes] = _get_bytes,
    attempts: int = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    release = None
    last_reason = "not on PyPI"
    for i in range(attempts):
        try:
            release = get_json(JSON_URL.format(version=version))
        except urllib.error.HTTPError as e:
            if e.code == 404 or 500 <= e.code < 600:
                last_reason = f"HTTP {e.code}"
                release = None
            else:
                raise
        except urllib.error.URLError as e:
            last_reason = str(e.reason)
            release = None
        except TimeoutError as e:
            last_reason = str(e) or "timed out"
            release = None
        if release is not None:
            break
        if i + 1 < attempts:
            sleep(15)
    if release is None:
        raise NotYetPublished(
            f"splitsmith {version} is not on PyPI after {attempts} attempts ({last_reason})"
        )
    name, url, sha = pick_wheel(release)
    body = get_bytes(url)
    got = hashlib.sha256(body).hexdigest()
    if got != sha:
        raise ShaMismatch(f"{name}: PyPI says {sha}, downloaded {got}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name
    out.write_bytes(body)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: fetch_pypi_wheel.py <version> <out_dir>")
    print(fetch(sys.argv[1].removeprefix("v"), Path(sys.argv[2])))
