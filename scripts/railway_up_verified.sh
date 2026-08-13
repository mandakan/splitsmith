#!/usr/bin/env bash
# Deploy a Railway service and treat the deployment record, not the log
# stream, as the success signal.
#
# `railway up --ci` exits 1 when it loses the build-log stream ("Failed to
# stream build logs") even though the build/deploy continues server-side and
# usually succeeds (issue #863). On a non-zero exit this script extracts the
# deployment id from the Build Logs URL the CLI printed and polls
# `railway deployment list` until that deployment reaches a terminal status;
# only a genuinely failed deployment keeps the job red.
#
# Usage: railway_up_verified.sh <service>
# Auth: RAILWAY_TOKEN (env-scoped project token), same as railway up.
set -uo pipefail

service="${1:?usage: railway_up_verified.sh <service>}"
poll_interval="${RAILWAY_VERIFY_POLL_INTERVAL:-10}"
timeout_seconds="${RAILWAY_VERIFY_TIMEOUT:-600}"

up_log="$(mktemp)"
trap 'rm -f "$up_log"' EXIT

railway up --service "$service" --ci 2>&1 | tee "$up_log"
up_exit="${PIPESTATUS[0]}"

if [ "$up_exit" -eq 0 ]; then
  exit 0
fi

deployment_id="$(grep -oE 'id=[0-9a-f-]{36}' "$up_log" | head -n1 | cut -d= -f2)"
if [ -z "$deployment_id" ]; then
  echo "railway up exited $up_exit before a deployment was created; failing" >&2
  exit "$up_exit"
fi

echo "railway up exited $up_exit (log-stream flake?); polling deployment $deployment_id instead"

deadline=$((SECONDS + timeout_seconds))
while [ "$SECONDS" -lt "$deadline" ]; do
  status="$(railway deployment list --service "$service" --json --limit 20 \
    | jq -r --arg id "$deployment_id" '.[] | select(.id == $id) | .status' \
    || true)"
  echo "deployment $deployment_id status: ${status:-<not found>}"
  case "$status" in
    SUCCESS | SLEEPING)
      # SLEEPING is the post-success state of scale-to-zero services.
      echo "Deployment $deployment_id succeeded server-side; ignoring railway up exit $up_exit"
      exit 0
      ;;
    FAILED | CRASHED | REMOVED | SKIPPED)
      echo "Deployment $deployment_id ended in status $status" >&2
      exit 1
      ;;
  esac
  sleep "$poll_interval"
done

echo "Timed out after ${timeout_seconds}s waiting for deployment $deployment_id to reach a terminal status" >&2
exit 1
