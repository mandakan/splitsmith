#!/usr/bin/env bash
# Generate build/NOTICES.md: the hand-written head + both dependency trees.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$HERE/build/runtime/python/bin/python3.12"
OUT="$HERE/build/NOTICES.md"
[ -x "$PY" ] || { echo "runtime missing; run build-runtime.sh" >&2; exit 1; }
# The ffmpeg source release the fetch script pins.
release_url="$(grep -E '^TAG=' "$HERE/fetch-ffmpeg.sh" | sed -E 's/^TAG="?([^"]+)"?/https:\/\/github.com\/mandakan\/splitsmith\/releases\/tag\/\1/')"
{
  sed "s|FFMPEG_SOURCE_RELEASE_URL|$release_url|" "$HERE/NOTICES.head.md"
  echo
  uvx pip-licenses --python "$PY" --format=markdown --order=name
  echo
  echo "## JavaScript packages (the web UI)"
  echo
  echo '```'
  (cd "$ROOT/src/splitsmith/ui_static" && pnpm dlx license-checker --production --summary 2>/dev/null | grep -v UNLICENSED)
  echo '```'
  echo
  echo "(UNLICENSED in license-checker's raw output is the UI package itself, which is part of Splitsmith.)"
} > "$OUT"
if grep -q -E '\| UNKNOWN' "$OUT"; then echo "a package has an UNKNOWN license; add it to NOTICES.head.md" >&2; exit 1; fi
echo "wrote $OUT ($(wc -l < "$OUT" | tr -d ' ') lines)"
