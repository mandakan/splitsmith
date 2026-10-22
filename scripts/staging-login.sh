#!/usr/bin/env bash
# Staging magic-link login helper.
#
# Staging runs SPLITSMITH_EMAIL_BACKEND=console, so it sends no real email --
# the magic link is written to the serve logs instead. This mints a link with
# scripts/staging_login_link.py (which reads it back from Cloud Logging) and
# opens it, which sets your session cookie. Production sends real email via
# Lettermint, so this is staging-only.
#
# Usage:  scripts/staging-login.sh [email]
#   Defaults to m@thias.se (the allowlisted account). Signups on staging are
#   closed, so only allowlisted addresses get a link.
#
# Requires an authenticated gcloud CLI (`gcloud auth login`).
set -euo pipefail

LINK="$(python3 "$(dirname "$0")/staging_login_link.py" "${1:-m@thias.se}")"
echo "Opening $LINK"
open "$LINK" 2>/dev/null || xdg-open "$LINK" 2>/dev/null || echo "Open it in a browser: $LINK"
