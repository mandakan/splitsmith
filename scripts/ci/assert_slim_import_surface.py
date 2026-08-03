"""The slim wheel must not drag in the dev-only ML stack.

``torch`` / ``transformers`` / ``panns_inference`` live in the ``dev``
group and are exercised only when building artifacts. If any of them
resolves into a plain ``uv tool install splitsmith``, the user downloads
gigabytes they never run -- the leak this asserts against.

The positive half matters just as much: the slim runtime stack must
actually be present, which is what catches a transitive dependency going
missing (the Pillow regression in PR #388).

Run with the *slim venv's* interpreter, not the dev one:

    slim-venv/bin/python scripts/ci/assert_slim_import_surface.py
"""

from __future__ import annotations

import sys

FORBIDDEN = ("torch", "transformers", "panns_inference")
REQUIRED = ("onnxruntime", "librosa", "huggingface_hub", "numpy", "PIL")


def main() -> int:
    for name in FORBIDDEN:
        try:
            __import__(name)
        except ImportError:
            continue
        print(
            f"slim wheel pulled in {name} -- the [dev] group leaked into the runtime install",
            file=sys.stderr,
        )
        return 1

    for name in REQUIRED:
        try:
            __import__(name)
        except ImportError as exc:
            print(f"slim wheel is missing {name}: {exc}", file=sys.stderr)
            return 1

    print("import surface ok: no torch trio; slim ML stack present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
