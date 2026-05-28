#!/usr/bin/env bash
#
# Local hosted-mode smoke test.
#
# Builds the hosted image, boots the docker-compose stack (Postgres +
# MinIO + the splitsmith API), and asserts the things the normal pytest
# suite CANNOT catch because CI has no Postgres:
#
#   - the container boots and /api/health returns 200
#   - `alembic upgrade head` actually ran (the API is unhealthy otherwise)
#   - the procrastinate schema landed its tables + functions  <-- the
#     asyncpg multi-statement migration bug that shipped in #437
#   - our tenant tables (users / recent_projects / compute_jobs) exist
#
# This exists because that migration bug passed CI green: the only
# Postgres coverage is `pytest -m docker`, which CI skips, and the
# SQLite lane no-ops the Postgres-only migration.
#
# Usage:
#   scripts/smoke_hosted.sh           # fast: skips the ~450 MB model bake
#   SMOKE_BAKE=1 scripts/smoke_hosted.sh   # also bake + assert models present
#
# ALWAYS tears the stack down and prunes what it built on exit (success
# OR failure) -- a sparse colima/Lima disk doesn't shrink on its own, so
# leaving images + volumes around is how the host fills up.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"
API_BASE="http://localhost:5174"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-120}"
BAKE="${SMOKE_BAKE:-0}"

cd "${REPO_ROOT}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '\033[36m==> %s\033[0m\n' "$*"; }

cleanup() {
    local rc=$?
    info "tearing down stack + reclaiming build artifacts"
    # --rmi local drops the image compose built here; -v drops the
    # Postgres/MinIO volumes so the next run starts clean.
    docker compose -f "${COMPOSE_FILE}" down -v --rmi local >/dev/null 2>&1 || true
    # Build cache from this run only -- safe, regenerable.
    docker builder prune -f >/dev/null 2>&1 || true
    if [ "${rc}" -eq 0 ]; then
        green "SMOKE PASSED"
    else
        red "SMOKE FAILED (exit ${rc})"
    fi
    exit "${rc}"
}
trap cleanup EXIT

psql_q() {
    docker compose -f "${COMPOSE_FILE}" exec -T postgres \
        psql -U splitsmith -d splitsmith -At -c "$1"
}

assert() {
    # assert <description> <actual> <expected>
    if [ "$2" != "$3" ]; then
        red "FAIL: $1 (got '$2', expected '$3')"
        exit 1
    fi
    green "ok: $1"
}

assert_ge() {
    # assert_ge <description> <actual> <min>
    if [ "$2" -lt "$3" ]; then
        red "FAIL: $1 (got '$2', expected >= '$3')"
        exit 1
    fi
    green "ok: $1 ($2)"
}

info "building hosted image (BAKE_MODELS=${BAKE})"
docker build --build-arg "BAKE_MODELS=${BAKE}" -t splitsmith-splitsmith "${REPO_ROOT}"

info "starting compose stack"
docker compose -f "${COMPOSE_FILE}" up -d --no-build

info "waiting for /api/health (timeout ${HEALTH_TIMEOUT_S}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
healthy=0
while [ "$(date +%s)" -lt "${deadline}" ]; do
    if curl -fsS "${API_BASE}/api/health" >/dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 2
done
if [ "${healthy}" -ne 1 ]; then
    red "API never became healthy -- recent logs:"
    docker compose -f "${COMPOSE_FILE}" logs --tail 40 splitsmith || true
    exit 1
fi
green "ok: /api/health responding"

info "asserting migrations applied"
# The procrastinate schema (the asyncpg multi-statement bug) -- 4 tables + funcs.
proc_tables=$(psql_q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'procrastinate_%'")
assert_ge "procrastinate_* tables present" "${proc_tables}" 4
proc_funcs=$(psql_q "SELECT count(*) FROM pg_proc WHERE proname LIKE 'procrastinate_%'")
assert_ge "procrastinate functions present" "${proc_funcs}" 1
# Our tenant tables from the earlier migrations.
for t in users recent_projects compute_jobs; do
    exists=$(psql_q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='${t}'")
    assert "table ${t} exists" "${exists}" 1
done

info "asserting a Postgres-backed endpoint round-trips"
recent=$(curl -fsS "${API_BASE}/api/me/recent-projects")
assert "recent-projects empty list" "${recent}" '{"projects":[]}'

if [ "${BAKE}" = "1" ]; then
    info "asserting baked models present"
    missing=$(docker compose -f "${COMPOSE_FILE}" exec -T splitsmith \
        splitsmith fetch-models --list 2>/dev/null | grep -c missing || true)
    assert "no missing model artifacts" "${missing}" 0
fi
