#!/usr/bin/env bash
# The desktop build pipeline (spec: "Build pipeline"). Run from anywhere.
#   CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh   -> unsigned, fast
#   desktop/build.sh                                     -> signed + notarized
#     (needs a Developer ID Application cert in the keychain and
#      APPLE_API_KEY / APPLE_API_KEY_ID / APPLE_API_ISSUER in the env)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

echo "== 1/6 SPA"
(cd "$ROOT/src/splitsmith/ui_static" && pnpm install --frozen-lockfile && pnpm build && find dist -name '*.map' -delete)
echo "== 2/6 wheel"
(cd "$ROOT" && rm -rf dist && uv build --wheel)
echo "== 3/6 runtime"
"$HERE/build-runtime.sh"
echo "== 4/6 ffmpeg"
"$HERE/fetch-ffmpeg.sh"
echo "== 5/6 notices"
"$HERE/build-notices.sh"
echo "== 6/6 electron-builder"
version="$(cd "$ROOT" && uv run --no-project python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
(
  cd "$HERE"
  pnpm install --frozen-lockfile
  pnpm compile
  # The committed version is 0.0.0; the build carries pyproject's, and the
  # working-tree edit is reverted whether or not electron-builder succeeds.
  trap 'git checkout -q -- package.json' EXIT
  npm pkg set version="$version"
  pnpm exec electron-builder --mac --arm64
)
if [ "${CSC_IDENTITY_AUTO_DISCOVERY:-true}" != "false" ]; then
  "$HERE/verify-signed.sh" "$HERE/dist/mac-arm64/Splitsmith.app"
fi
ls -la "$HERE"/dist/*.dmg
