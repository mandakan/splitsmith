#!/usr/bin/env bash
# Check a built .deb kept electron-builder's stock maintainer scripts and got
# the splitsmith CLI link block. Exit 1 if any check finds nothing.
#   desktop/scripts/check-deb.sh dist/splitsmith-desktop_X.Y.Z_amd64.deb
set -euo pipefail
deb="${1:?usage: check-deb.sh <deb>}"
ctl="$(mktemp -d)"
trap 'rm -rf "$ctl"' EXIT
dpkg-deb -e "$deb" "$ctl"
fail=0
check() { # check <file> <pattern>
  local n
  n="$(grep -c -- "$2" "$ctl/$1" || true)"
  echo "$1: $2 x $n"
  [ "$n" -gt 0 ] || fail=1
}
check postinst chrome-sandbox
check postinst apparmor
check postinst splitsmith_cli_link
check postrm splitsmith_cli_unlink
dpkg-deb -I "$deb" | grep -E 'Depends|Maintainer|Description' || true
[ "$fail" = 0 ] || { echo "check-deb: $deb is missing a maintainer script block" >&2; exit 1; }
echo "check-deb: ok"
