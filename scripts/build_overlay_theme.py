"""Build ``overlay_theme.json`` from the web UI's design tokens.

The overlay renderer (``splitsmith.overlay_render``) needs the same colors
the Shot Timer UI uses so the optional ``designsystem`` overlay variant
stays in sync with whatever is in
``src/splitsmith/ui_static/src/styles/index.css``. Re-parsing the CSS at
import time would mean shipping a CSS parser as a runtime dep; instead we
extract the handful of tokens overlays care about once at build time and
mirror them into ``src/splitsmith/data/overlay_theme.json``.

Run::

    uv run python scripts/build_overlay_theme.py

The script is intentionally narrow -- it only extracts the colors and
font names overlays use today. Add tokens here as new overlay variants
need them; the JSON schema is consumed by ``splitsmith.overlay_theme``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSS_PATH = REPO_ROOT / "src/splitsmith/ui_static/src/styles/index.css"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "src/splitsmith/data/overlay_theme.json"

# Map overlay role -> CSS variable. Roles are stable; CSS vars can move.
# When a role's CSS var disappears, the build fails loudly rather than
# silently writing a partial JSON.
TOKEN_MAP: dict[str, str] = {
    "ink": "--color-ink",
    "split": "--color-live",
    "split_good": "--color-done",
    "split_slow": "--color-led",
    "stroke": "--color-bg",
    "accent": "--color-led",
}

FONT_MAP: dict[str, str] = {
    "display": "--font-display",
    "sans": "--font-sans",
    "mono": "--font-mono",
}

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
_FIRST_FAMILY_RE = re.compile(r'"([^"]+)"|([A-Za-z][\w\- ]*)')


def _read_theme_block(css_text: str) -> str:
    """Return just the ``@theme { ... }`` body so legacy ``@layer`` blocks
    can't shadow the canonical tokens."""
    start = css_text.find("@theme")
    if start < 0:
        raise SystemExit("no @theme block found in CSS")
    brace = css_text.find("{", start)
    if brace < 0:
        raise SystemExit("malformed @theme block (no opening brace)")
    depth = 1
    i = brace + 1
    while i < len(css_text) and depth > 0:
        c = css_text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        raise SystemExit("malformed @theme block (unbalanced braces)")
    return css_text[brace + 1 : i - 1]


def _parse_declarations(block: str) -> dict[str, str]:
    """Tokenize ``--name: value;`` pairs. Comments stripped first so the
    ``/* ... */`` blocks inside the @theme don't confuse the regex."""
    no_comments = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    decls: dict[str, str] = {}
    for line in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", no_comments):
        name, value = line
        decls[name] = value.strip()
    return decls


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    m = _HEX_RE.match(value.strip())
    if not m:
        raise SystemExit(f"expected #rrggbb, got {value!r}")
    raw = m.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _first_family(value: str) -> str:
    """Pull the first font-family token out of a CSS font stack."""
    m = _FIRST_FAMILY_RE.search(value)
    if not m:
        raise SystemExit(f"no font family in {value!r}")
    return (m.group(1) or m.group(2)).strip()


def build_theme(css_path: Path) -> dict[str, object]:
    css_text = css_path.read_text(encoding="utf-8")
    decls = _parse_declarations(_read_theme_block(css_text))

    colors: dict[str, list[int]] = {}
    for role, var in TOKEN_MAP.items():
        if var not in decls:
            raise SystemExit(f"missing CSS variable {var} (mapped to overlay role {role!r})")
        r, g, b = _hex_to_rgb(decls[var])
        colors[role] = [r, g, b]

    fonts: dict[str, str] = {}
    for role, var in FONT_MAP.items():
        if var not in decls:
            raise SystemExit(f"missing CSS variable {var} (mapped to overlay font {role!r})")
        fonts[role] = _first_family(decls[var])

    return {
        "source": str(css_path.relative_to(REPO_ROOT)),
        "colors": colors,
        "fonts": fonts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--css", type=Path, default=DEFAULT_CSS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the output file is missing or out of sync.",
    )
    args = parser.parse_args()

    theme = build_theme(args.css)
    rendered = json.dumps(theme, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not args.output.exists():
            print(f"missing {args.output}", file=sys.stderr)
            return 1
        existing = args.output.read_text(encoding="utf-8")
        if existing != rendered:
            print(f"{args.output} is out of sync; re-run without --check", file=sys.stderr)
            return 1
        print(f"{args.output} up to date")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
