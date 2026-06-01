#!/usr/bin/env bash
# Staging magic-link login helper.
#
# Staging runs SPLITSMITH_EMAIL_BACKEND=console, so it sends no real email --
# the magic link is written to the serve logs instead. This script requests a
# link, pulls it back out of the Railway staging serve logs, and opens it
# (which sets your session cookie). Production sends real email via Lettermint,
# so this is staging-only.
#
# Usage:  scripts/staging-login.sh [email]
#   Defaults to m@thias.se (the allowlisted account). Signups on staging are
#   closed, so only allowlisted addresses get a link; anything else logs
#   nothing and the script reports no link found.
#
# Requires the Railway CLI logged in (`railway login`) and the project linked
# once: `railway link --project e77bded4-ddb2-430c-816f-156f2b6fe36a`.
set -euo pipefail

EMAIL="${1:-m@thias.se}"
HOST="https://my.staging.splitsmith.app"

echo "Requesting magic link for $EMAIL on staging ..."
curl -fsS -X POST "$HOST/api/v1/auth/begin" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\"}" >/dev/null

# Give Railway's log pipeline a moment to flush the line.
sleep 3

echo "Fetching the link from the staging serve logs ..."
LINK="$(railway logs -d --lines 200 -s serve -e staging 2>/dev/null \
  | grep "MAGIC_LINK $EMAIL " \
  | tail -1 \
  | grep -oE 'https://[^ ]+/auth/callback\?token=[^ ]+' || true)"

if [ -z "$LINK" ]; then
  echo "No link found in the logs yet. Re-run in a few seconds, or check:"
  echo "  railway logs -d -s serve -e staging | grep MAGIC_LINK"
  exit 1
fi

echo "Link: $LINK"
# Open in the default browser (macOS 'open'; falls back to xdg-open on Linux).
if command -v open >/dev/null 2>&1; then open "$LINK";
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$LINK"; fi
