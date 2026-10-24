#!/usr/bin/env bash
# Open a built DMG the way a user does and install its app into DEST:
#   desktop/check-dmg.sh dist/Splitsmith-X.Y.Z-arm64.dmg "$RUNNER_TEMP/installed"
# Checks the image's checksum, the volume layout (the app plus the
# /Applications link the window shows), that the bundle is arm64 and carries
# the version in the DMG's name, then copies the app out with ditto (what a
# Finder drag does: symlinks, modes and xattrs survive) and checks the copy
# has every file. smoke.sh and launch-check.sh then run DEST/Splitsmith.app,
# so a DMG that packs a broken bundle fails here and not on a user's Mac.
set -euo pipefail
DMG="${1:?path to .dmg}"
DEST="${2:?directory to install Splitsmith.app into}"
die() { echo "check-dmg: $*" >&2; exit 1; }

name="$(basename "$DMG")"
[[ "$name" =~ ^Splitsmith-([0-9]+\.[0-9]+\.[0-9]+)-arm64\.dmg$ ]] || die "unexpected DMG name: $name"
version="${BASH_REMATCH[1]}"

hdiutil verify -quiet "$DMG" || die "hdiutil verify failed: $DMG"
mnt="$(mktemp -d)"
detach() { hdiutil detach -quiet "$mnt" 2>/dev/null || hdiutil detach -force -quiet "$mnt" 2>/dev/null || true; }
trap detach EXIT
hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$DMG"

app="$mnt/Splitsmith.app"
[ -d "$app" ] || die "no Splitsmith.app on the volume"
[ -L "$mnt/Applications" ] && [ "$(readlink "$mnt/Applications")" = /Applications ] \
  || die "the volume has no /Applications link"

plist="$app/Contents/Info.plist"
shown="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")"
[ "$shown" = "$version" ] || die "the bundle says version $shown, the DMG $version"
exe="$app/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")"
[ "$(lipo -archs "$exe")" = arm64 ] || die "$exe is $(lipo -archs "$exe"), not arm64"
lipo -archs "$app/Contents/Resources/python/bin/python3.12" | grep -qx arm64 || die "the bundled Python is not arm64"

mkdir -p "$DEST"
rm -rf "$DEST/Splitsmith.app"
ditto "$app" "$DEST/Splitsmith.app"
listing() { (cd "$1" && find . | LC_ALL=C sort); }
diff <(listing "$app") <(listing "$DEST/Splitsmith.app") >/dev/null || die "the copied app differs from the volume's"
echo "dmg ok: $name ($(du -sh "$DEST/Splitsmith.app" | cut -f1) installed at $DEST/Splitsmith.app)"
