#!/usr/bin/env bash
# The desktop build pipeline (spec: "Build pipeline"). Run from anywhere.
#   CSC_IDENTITY_AUTO_DISCOVERY=false desktop/build.sh   -> unsigned, fast
#   desktop/build.sh                                     -> signed + notarized
#     (needs a Developer ID Application cert in the keychain and
#      APPLE_API_KEY / APPLE_API_KEY_ID / APPLE_API_ISSUER in the env)
#   desktop/build.sh --linux [--wheel PATH]              -> AppImage + .deb
# The target defaults to the build host's (lib/target.sh); --mac / --linux
# name it. --wheel installs that wheel instead of building one from the tree.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# shellcheck source=lib/target.sh
source "$HERE/lib/target.sh"
TARGET="$(host_target)"
WHEEL=""
while [ $# -gt 0 ]; do
  if t="$(parse_target_flag "$1")"; then TARGET="$t"; shift; continue; fi
  case "$1" in
    --wheel) WHEEL="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

echo "== 1/6 SPA"
(cd "$ROOT/src/splitsmith/ui_static" && pnpm install --frozen-lockfile && pnpm build && find dist -name '*.map' -delete)
echo "== 2/6 wheel"
if [ -z "$WHEEL" ]; then
  (cd "$ROOT" && rm -rf dist && uv build --wheel)
else
  echo "using $WHEEL"
fi
echo "== 3/6 runtime"
"$HERE/build-runtime.sh" --target "$TARGET" ${WHEEL:+--wheel "$WHEEL"}
echo "== 4/6 ffmpeg"
"$HERE/fetch-ffmpeg.sh" --target "$TARGET"
echo "== 5/6 notices"
"$HERE/build-notices.sh"
echo "== 6/6 electron-builder"
version="$(cd "$ROOT" && uv run --no-project python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
case "$TARGET" in
  macos-aarch64) builder_args=(--mac --arm64) ;;
  linux-x86_64)
    # fpm refuses a deb without a homepage (its Homepage field). Passed here,
    # not in package.json or the YAML, so the macOS bundle does not change.
    builder_args=(--linux --x64 -c.extraMetadata.homepage=https://splitsmith.app)
    # The deb scripts are electron-builder templates, so the CLI link
    # snippet is inlined at the marker rather than sourced at install time.
    mkdir -p "$HERE/build/linux"
    for f in postinst postrm; do
      awk -v snip="$HERE/linux/cli-link.sh" '/@@SPLITSMITH_CLI_LINK@@/ { while ((getline l < snip) > 0) print l; next } { print }' \
        "$HERE/linux/$f.sh" > "$HERE/build/linux/$f.sh"
    done
    ;;
  *) echo "unknown target $TARGET" >&2; exit 2 ;;
esac
(
  cd "$HERE"
  pnpm install --frozen-lockfile
  pnpm compile
  # The committed version is 0.0.0; the build carries pyproject's, and the
  # working-tree edit is reverted whether or not electron-builder succeeds.
  trap 'git checkout -q -- package.json' EXIT
  npm pkg set version="$version"
  pnpm exec electron-builder "${builder_args[@]}"
)
if [ "$TARGET" = macos-aarch64 ]; then
  if [ "${CSC_IDENTITY_AUTO_DISCOVERY:-true}" != "false" ]; then
    "$HERE/verify-signed.sh" "$HERE/dist/mac-arm64/Splitsmith.app"
  fi
  ls -la "$HERE"/dist/*.dmg
else
  ls -la "$HERE"/dist/*.AppImage "$HERE"/dist/*.deb
fi
