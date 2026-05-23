#!/usr/bin/env bash
# Mirror the GitHub Actions `lint + format + unit tests` job locally so
# nothing that fails CI ever reaches origin. Add the SPA's
# typecheck + build on top -- the CI workflow doesn't run them today
# but they're cheap and catch the kinds of TS regressions that would
# only surface in a `npm run build` failure during deploy.
#
# Usage: bash scripts/preflight.sh
#
# Exits non-zero on first failure so you can wire this up as a git
# pre-push hook if you want.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "==> ruff check src tests"
uv run ruff check src tests

echo "==> black --check src tests"
uv run black --check src tests

echo "==> pytest -q"
uv run pytest -q

# SPA: not in CI today, but cheap insurance. Skip cleanly if the
# ui_static workspace isn't installed.
if [ -d src/splitsmith/ui_static/node_modules ]; then
  echo "==> tsc -b --noEmit (SPA)"
  (cd src/splitsmith/ui_static && npx tsc -b --noEmit)
  echo "==> vite build (SPA)"
  (cd src/splitsmith/ui_static && npm run build --silent >/dev/null)
else
  echo "==> SPA checks skipped (no node_modules in src/splitsmith/ui_static)"
fi

echo "==> all preflight checks passed"
