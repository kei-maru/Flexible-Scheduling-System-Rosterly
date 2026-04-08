#!/usr/bin/env bash
set -euo pipefail

# One-click release for Server B (System B + DB + Redis + Worker)
# Tuned for current online container naming.

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/Veludo_Saas}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.rosterly.yml}"

# Your online container names (primary)
CONTAINER_B_API="${CONTAINER_B_API:-veludo-system-b}"
CONTAINER_B_WORKER="${CONTAINER_B_WORKER:-veludo_worker}"
CONTAINER_B_DB="${CONTAINER_B_DB:-saas_postgres}"

# Alternate names in repo compose files (fallback)
ALT_CONTAINER_B_API="${ALT_CONTAINER_B_API:-rosterly_api}"
ALT_CONTAINER_B_WORKER="${ALT_CONTAINER_B_WORKER:-rosterly_worker}"
ALT_CONTAINER_B_DB="${ALT_CONTAINER_B_DB:-rosterly_postgres}"

echo "[B] cd ${PROJECT_DIR}"
cd "${PROJECT_DIR}"

echo "[B] pull latest code"
git pull --ff-only

echo "[B] start/recreate services via ${COMPOSE_FILE}"
docker compose -f "${COMPOSE_FILE}" up -d --build

echo "[B] run migrations"
docker compose -f "${COMPOSE_FILE}" exec -T rosterly-core python /app/system_b_saas/manage.py migrate

echo "[B] collect static (best effort)"
docker compose -f "${COMPOSE_FILE}" exec -T rosterly-core python /app/system_b_saas/manage.py collectstatic --noinput || true

echo "[B] service status"
docker compose -f "${COMPOSE_FILE}" ps

echo "[B] container status (name-based check)"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E "${CONTAINER_B_API}|${ALT_CONTAINER_B_API}|${CONTAINER_B_WORKER}|${ALT_CONTAINER_B_WORKER}|${CONTAINER_B_DB}|${ALT_CONTAINER_B_DB}" || true

echo "[B] recent logs"
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_B_API}"; then
  docker logs --tail 120 "${CONTAINER_B_API}" || true
elif docker ps --format '{{.Names}}' | grep -qx "${ALT_CONTAINER_B_API}"; then
  docker logs --tail 120 "${ALT_CONTAINER_B_API}" || true
else
  docker compose -f "${COMPOSE_FILE}" logs --tail 120 rosterly-core || true
fi

echo "[B] done"
