#!/usr/bin/env bash
# Build, sign, notarize, smoke and publish the desktop DMG for a release.
#
#   desktop/release.sh              -> the newest vX.Y.Z GitHub release
#   desktop/release.sh v0.43.0      -> that release
#   desktop/release.sh --replace    -> re-upload over an existing DMG
#   desktop/release.sh --keep       -> leave the build worktree behind
#
# The build runs from a throwaway worktree at the tag, so the working copy
# (its branch, its local edits) never reaches a shipped DMG. Signing stays
# local by design (spec 2026-09-20): the Developer ID identity comes from
# the login keychain, notarization from APPLE_API_KEY (path to the .p8),
# APPLE_API_KEY_ID and APPLE_API_ISSUER, read from the environment or,
# when unset, from $SPLITSMITH_DESKTOP_RELEASE_ENV
# (default ~/.appstoreconnect/splitsmith-desktop.env).
#
# The update feed (functions/desktop/latest.json.js) announces a release
# only once it carries a .dmg, so the upload here is what ships it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
cd "$ROOT"

tag="" replace=0 keep=0
for arg in "$@"; do
  case "$arg" in
    --replace) replace=1 ;;
    --keep) keep=1 ;;
    v*) tag="$arg" ;;
    *) echo "usage: desktop/release.sh [vX.Y.Z] [--replace] [--keep]" >&2; exit 2 ;;
  esac
done

die() { echo "release: $*" >&2; exit 1; }

# -- preflight: everything that can fail fast fails before a 30+ min build.
command -v gh >/dev/null || die "gh not on PATH"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated (gh auth login)"
[ "$(uname -m)" = arm64 ] || die "build on an arm64 Mac"

env_file="${SPLITSMITH_DESKTOP_RELEASE_ENV:-$HOME/.appstoreconnect/splitsmith-desktop.env}"
if [ -z "${APPLE_API_KEY:-}" ] && [ -f "$env_file" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
fi
for var in APPLE_API_KEY APPLE_API_KEY_ID APPLE_API_ISSUER; do
  [ -n "${!var:-}" ] || die "$var is not set (export it or put it in $env_file)"
done
[ -f "$APPLE_API_KEY" ] || die "APPLE_API_KEY points at a missing file: $APPLE_API_KEY"
security find-identity -v -p codesigning | grep -q "Developer ID Application" \
  || die "no Developer ID Application identity in the keychain"
[ "${CSC_IDENTITY_AUTO_DISCOVERY:-true}" != "false" ] \
  || die "CSC_IDENTITY_AUTO_DISCOVERY=false would publish an unsigned build"

if [ -z "$tag" ]; then
  tag="$(gh release list --limit 50 --exclude-drafts --exclude-pre-releases \
    --json tagName --jq '.[].tagName' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
  [ -n "$tag" ] || die "no vX.Y.Z release found"
fi
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "not a release tag: $tag"
version="${tag#v}"
dmg_name="Splitsmith-$version-arm64.dmg"

gh release view "$tag" >/dev/null 2>&1 || die "no GitHub release $tag"
if gh release view "$tag" --json assets --jq '.assets[].name' | grep -qx "$dmg_name"; then
  [ "$replace" = 1 ] || die "$tag already has $dmg_name (pass --replace to rebuild and re-upload)"
fi

git fetch -q origin "refs/tags/$tag:refs/tags/$tag" 2>/dev/null || true
git rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "tag $tag is not available locally or on origin"

# -- build from a clean worktree at the tag.
wt="$(mktemp -d "${TMPDIR:-/tmp}/splitsmith-release-$version.XXXXXX")"
cleanup() {
  if [ "$keep" = 1 ]; then
    echo "release: build worktree kept at $wt (git worktree remove --force $wt)"
  else
    git -C "$ROOT" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
  fi
}
trap cleanup EXIT
git worktree add -q --detach "$wt" "$tag"

pyver="$(cd "$wt" && uv run --no-project python -c \
  "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
[ "$pyver" = "$version" ] || die "$tag carries pyproject version $pyver, not $version"

# The DMG ships the wheel PyPI serves for this version, the one with the
# YouTube OAuth client baked in; build-runtime.sh refuses one without it.
grep -q -- '--wheel' "$wt/desktop/build.sh" \
  || die "$tag predates build.sh --wheel; its DMG cannot carry the baked YouTube client"
echo "== release $tag: wheel from PyPI"
wheel="$(uv run --no-project python "$ROOT/scripts/ci/fetch_pypi_wheel.py" "$version" "$wt/build/wheel")"
echo "== release $tag: build (signed + notarized; notarization can take close to an hour)"
SPLITSMITH_REQUIRE_YOUTUBE_CLIENT=1 "$wt/desktop/build.sh" --mac --wheel "$wheel"
dmg="$wt/desktop/dist/$dmg_name"
[ -f "$dmg" ] || die "build did not produce $dmg_name"

echo "== release $tag: smoke"
"$wt/desktop/smoke.sh" "$wt/desktop/dist/mac-arm64/Splitsmith.app"

echo "== release $tag: upload"
(cd "$(dirname "$dmg")" && shasum -a 256 "$dmg_name" > "$dmg_name.sha256")
if [ "$replace" = 1 ]; then
  gh release upload "$tag" "$dmg" "$dmg.sha256" --clobber
else
  gh release upload "$tag" "$dmg" "$dmg.sha256"
fi

echo "release: $dmg_name is on $(gh release view "$tag" --json url --jq .url)"
echo "release: the update feed picks it up within 10 minutes: https://splitsmith.app/desktop/latest.json"
