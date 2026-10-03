"""Fail unless the installed splitsmith carries the baked YouTube OAuth client.

publish-pypi.yml bakes the Desktop client into the wheel; a wheel built
from a checkout has empty constants, and an app built from it cannot
connect YouTube (the 0.53.0 DMG shipped that way). build-runtime.sh runs
this with the bundle's own interpreter when SPLITSMITH_REQUIRE_YOUTUBE_CLIENT=1,
which every release build sets. It reads the constants with ast and never
imports the module, so it needs nothing beyond the stdlib. The values are
never printed.

    <bundle>/python/bin/python3.12 desktop/scripts/check_youtube_client.py
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

NAMES = ("BUILTIN_CLIENT_ID", "BUILTIN_CLIENT_SECRET")


def empty_constants(source: str) -> list[str]:
    values: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                values[node.targets[0].id] = node.value.value
    return [name for name in NAMES if not values.get(name)]


def main() -> int:
    spec = importlib.util.find_spec("splitsmith.youtube")
    if spec is None or not spec.submodule_search_locations:
        print("splitsmith.youtube is not installed", file=sys.stderr)
        return 1
    oauth = Path(next(iter(spec.submodule_search_locations))) / "oauth.py"
    missing = empty_constants(oauth.read_text(encoding="utf-8"))
    if missing:
        print(
            f"{oauth}: empty {', '.join(missing)}; the bundle must ship the wheel publish-pypi baked "
            "(pass --wheel with the PyPI wheel, see scripts/ci/fetch_pypi_wheel.py)",
            file=sys.stderr,
        )
        return 1
    print("YouTube OAuth client baked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
