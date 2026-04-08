#!/usr/bin/env bash
set -euo pipefail

# Post-release verification script
# Checks A/B container health and key application endpoints.

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/Veludo_Saas}"

# Online container names (primary)
CONTAINER_A="${CONTAINER_A:-veludo_system_a}"
CONTAINER_B_API="${CONTAINER_B_API:-veludo-system-b}"
CONTAINER_B_WORKER="${CONTAINER_B_WORKER:-veludo_worker}"
CONTAINER_B_DB="${CONTAINER_B_DB:-saas_postgres}"

# Alternate names (fallback)
ALT_CONTAINER_B_API="${ALT_CONTAINER_B_API:-rosterly_api}"
ALT_CONTAINER_B_WORKER="${ALT_CONTAINER_B_WORKER:-rosterly_worker}"
ALT_CONTAINER_B_DB="${ALT_CONTAINER_B_DB:-rosterly_postgres}"

A_HEALTH_URL="${A_HEALTH_URL:-http://127.0.0.1:8000/accounts/login/}"
B_HEALTH_URL="${B_HEALTH_URL:-http://127.0.0.1:8001/sso/authorize}"

cd "${PROJECT_DIR}"

echo "[VERIFY] container status"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E "${CONTAINER_A}|${CONTAINER_B_API}|${ALT_CONTAINER_B_API}|${CONTAINER_B_WORKER}|${ALT_CONTAINER_B_WORKER}|${CONTAINER_B_DB}|${ALT_CONTAINER_B_DB}" || true

echo "[VERIFY] HTTP checks"
set +e
curl -sS -I -m 8 "${A_HEALTH_URL}" | head -n 1
A_RC=$?
curl -sS -I -m 8 "${B_HEALTH_URL}" | head -n 1
B_RC=$?
set -e

if [[ ${A_RC} -ne 0 ]]; then
  echo "[VERIFY] WARN: A health check failed (${A_HEALTH_URL})"
fi
if [[ ${B_RC} -ne 0 ]]; then
  echo "[VERIFY] WARN: B health check failed (${B_HEALTH_URL})"
fi

echo "[VERIFY] django checks (best effort)"
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_A}"; then
  docker exec "${CONTAINER_A}" python /app/system_a_veludo/manage.py check || true
fi
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_B_API}"; then
  docker exec "${CONTAINER_B_API}" python /app/system_b_saas/manage.py check || true
elif docker ps --format '{{.Names}}' | grep -qx "${ALT_CONTAINER_B_API}"; then
  docker exec "${ALT_CONTAINER_B_API}" python /app/system_b_saas/manage.py check || true
fi

echo "[VERIFY] tail logs"
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_A}"; then
  docker logs --tail 80 "${CONTAINER_A}" || true
fi
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_B_API}"; then
  docker logs --tail 80 "${CONTAINER_B_API}" || true
elif docker ps --format '{{.Names}}' | grep -qx "${ALT_CONTAINER_B_API}"; then
  docker logs --tail 80 "${ALT_CONTAINER_B_API}" || true
fi

echo "[VERIFY] done"
