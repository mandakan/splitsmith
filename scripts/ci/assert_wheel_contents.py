"""The built wheel must ship the SPA and no source maps.

Regression gate for #642: the root ``.gitignore``'s unanchored ``dist/``
made hatchling silently drop the SPA, so published wheels served a bare
404 on ``GET /``. CI built and installed the wheel; it just never checked
the UI was inside it.

Stdlib only, so it runs before any environment is provisioned. Called
from both the always-on ``packaging`` job and the ``slim-smoke`` release
gate -- one copy so the two cannot drift.

Usage: ``python scripts/ci/assert_wheel_contents.py [dist-glob]``
"""

from __future__ import annotations

import glob
import sys
import zipfile

DEFAULT_GLOB = "dist/splitsmith-*.whl"


def main(argv: list[str]) -> int:
    pattern = argv[1] if len(argv) > 1 else DEFAULT_GLOB
    # ``glob.glob`` rather than ``Path.glob`` (PTH207): the latter raises
    # NotImplementedError on an absolute pattern, and the argument form
    # exists so this can be pointed at a wheel outside the repo.
    matches = sorted(glob.glob(pattern))  # noqa: PTH207
    if not matches:
        print(f"no wheel matched {pattern!r} -- did the build step run?", file=sys.stderr)
        return 1
    wheel = matches[0]

    names = zipfile.ZipFile(wheel).namelist()
    if not any(n.endswith("splitsmith/ui_static/dist/index.html") for n in names):
        print(f"{wheel} ships no SPA: ui_static/dist/index.html missing (see #642)", file=sys.stderr)
        return 1
    maps = [n for n in names if n.endswith(".map")]
    if maps:
        print(f"{wheel} ships source maps: {maps}", file=sys.stderr)
        return 1

    print(f"{wheel}: SPA present, no source maps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
