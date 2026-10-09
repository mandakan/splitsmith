"""Record every version a shipped Look template has had, from git.

``data/looks/_history.json`` maps the sha256 of each version of each
shipped template (``data/looks/<look>/*.html``) to its file name. A user
Look holding a byte-identical copy of one is an unedited copy, which
``looks.outdated_copies`` flags once the shipped file has moved on and
``looks refresh`` removes, so the Look draws the current template.

Run it after changing a shipped template (``tests/test_template_history.py``
fails until you do), and commit the result::

    uv run python scripts/record_template_history.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOOKS = Path("src/splitsmith/data/looks")
OUT = REPO / LOOKS / "_history.json"


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True).stdout


def shipped_template_paths() -> list[str]:
    """Every ``<look>/*.html`` path git has ever held under the shipped
    Looks, the shared scripts and starters (``_``-prefixed) excluded."""
    names = _git("log", "--format=", "--name-only", "--", str(LOOKS)).decode().split()
    current = [str(p.relative_to(REPO)) for p in (REPO / LOOKS).glob("*/*.html")]
    paths = set(names) | set(current)
    return sorted(
        p
        for p in paths
        if p.endswith(".html")
        and len(Path(p).parts) == len(LOOKS.parts) + 2
        and not Path(p).parts[len(LOOKS.parts)].startswith("_")
    )


def main() -> None:
    history: dict[str, str] = {}
    for path in shipped_template_paths():
        name = Path(path).name
        for sha in _git("log", "--format=%H", "--", path).decode().split():
            try:
                blob = _git("show", f"{sha}:{path}")
            except subprocess.CalledProcessError:
                continue  # the commit deleted it
            history[hashlib.sha256(blob).hexdigest()] = name
        working = REPO / path
        if working.is_file():
            history[hashlib.sha256(working.read_bytes()).hexdigest()] = name
    OUT.write_text(json.dumps(dict(sorted(history.items())), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(history)} template versions to {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
