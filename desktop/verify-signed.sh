#!/usr/bin/env bash
# Post-sign checks on a built .app. Fails on the first problem.
set -euo pipefail
APP="${1:?path to .app}"
codesign --verify --deep --strict --verbose=2 "$APP"
spctl --assess --type execute --verbose=2 "$APP"
xcrun stapler validate "$APP"
codesign -d --entitlements - "$APP" 2>&1 | grep -q allow-unsigned-executable-memory
echo "signed, notarized, stapled: $APP"
