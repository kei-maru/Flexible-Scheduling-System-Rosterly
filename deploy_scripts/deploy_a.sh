#!/usr/bin/env bash
set -euo pipefail

# One-click release for Server A (1G machine, System A only)
# Tuned for current online container naming.

PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/Veludo_Saas}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.veludo.yml}"

CONTAINER_A="${CONTAINER_A:-veludo_system_a}"
ALT_CONTAINER_A="${ALT_CONTAINER_A:-veludo_system_a}"

echo "[A] cd ${PROJECT_DIR}"
cd "${PROJECT_DIR}"

echo "[A] pull latest code"
git pull --ff-only

echo "[A] start/recreate System A only via ${COMPOSE_FILE}"
docker compose -f "${COMPOSE_FILE}" up -d --build system_a

echo "[A] run migrations"
docker compose -f "${COMPOSE_FILE}" exec -T system_a python /app/system_a_veludo/manage.py migrate

echo "[A] collect static (best effort)"
docker compose -f "${COMPOSE_FILE}" exec -T system_a python /app/system_a_veludo/manage.py collectstatic --noinput || true

echo "[A] service status"
docker compose -f "${COMPOSE_FILE}" ps

echo "[A] container status"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E "${CONTAINER_A}|${ALT_CONTAINER_A}" || true

echo "[A] recent logs"
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_A}"; then
  docker logs --tail 120 "${CONTAINER_A}" || true
else
  docker compose -f "${COMPOSE_FILE}" logs --tail 120 system_a || true
fi

echo "[A] done"
