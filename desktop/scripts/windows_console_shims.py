"""Make the Windows runtime's console scripts relocatable.

uv writes each console script as ``Scripts\\<name>.exe``, a launcher that
holds the interpreter's absolute path: the build dir, which the installed
app never sees. For every console script of the named distributions this
writes ``<name>.cmd``, which runs ``..\\python.exe`` next to it, plus the
``<name>-script.py`` it runs, then deletes everything else in Scripts: the
dependencies' launchers and plain scripts too (numba's ``Scripts\\numba``
carries a shebang naming the build dir), since the bundle runs none of them.

Run with the bundle's own interpreter, from build-runtime.sh:

    python.exe desktop/scripts/windows_console_shims.py splitsmith
"""

from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

SCRIPT = """\
import sys

from {module} import {attr}

# The name a user typed, not this file's, in usage and help text.
sys.argv[0] = {name!r}
sys.exit({call}())
"""

# %~dp0 is this file's own dir with a trailing backslash, wherever it is.
CMD = '@"%~dp0..\\python.exe" "%~dp0{name}-script.py" %*\r\n'


def write_shims(scripts: Path, dists: list[str]) -> list[str]:
    names = []
    written: set[Path] = set()
    for dist in dists:
        for ep in md.distribution(dist).entry_points.select(group="console_scripts"):
            module, _, attr = ep.value.partition(":")
            attr = attr.strip()
            head = attr.split(".")[0]
            script, cmd = scripts / f"{ep.name}-script.py", scripts / f"{ep.name}.cmd"
            script.write_text(
                SCRIPT.format(module=module.strip(), attr=head, name=ep.name, call=attr),
                encoding="utf-8",
            )
            cmd.write_text(CMD.format(name=ep.name), encoding="ascii", newline="")
            written |= {script, cmd}
            names.append(ep.name)
    for entry in scripts.iterdir():
        if entry not in written and entry.is_file():
            entry.unlink()
    return names


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: windows_console_shims.py DIST [DIST ...]", file=sys.stderr)
        return 2
    scripts = Path(sys.prefix) / "Scripts"
    names = write_shims(scripts, argv)
    if not names:
        print(f"no console scripts in {', '.join(argv)}", file=sys.stderr)
        return 1
    print(f"shims in {scripts}: {', '.join(sorted(names))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
