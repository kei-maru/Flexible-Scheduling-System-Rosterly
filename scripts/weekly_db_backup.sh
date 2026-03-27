#!/usr/bin/env bash
set -euo pipefail

# Weekly DB backup + 6-month retention cleanup.
# Usage:
#   bash scripts/weekly_db_backup.sh
# Optional envs:
#   BACKUP_CONTAINER_NAME=rosterly_postgres
#   BACKUP_DB_USER=veludo_user
#   BACKUP_DB_NAMES=saas_db,veludo_db
#   BACKUP_DIR=/home/ubuntu/Veludo_Saas/backups
#   RETENTION_DAYS=180

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_CONTAINER_NAME="${BACKUP_CONTAINER_NAME:-}"
BACKUP_DB_USER="${BACKUP_DB_USER:-veludo_user}"
BACKUP_DB_NAMES="${BACKUP_DB_NAMES:-saas_db,veludo_db}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/runtime_data/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-180}"

resolve_container_name() {
  if [[ -n "$BACKUP_CONTAINER_NAME" ]]; then
    echo "$BACKUP_CONTAINER_NAME"
    return
  fi

  if docker ps --format '{{.Names}}' | grep -qx 'rosterly_postgres'; then
    echo 'rosterly_postgres'
    return
  fi

  if docker ps --format '{{.Names}}' | grep -qx 'veludo_postgres'; then
    echo 'veludo_postgres'
    return
  fi

  echo 'No running postgres container found (expected rosterly_postgres or veludo_postgres).' >&2
  exit 1
}

backup_database() {
  local container_name="$1"
  local db_name="$2"
  local timestamp
  local output_file

  timestamp="$(date '+%Y-%m-%d_%H%M%S')"
  output_file="${BACKUP_DIR}/${db_name}_${timestamp}.sql.gz"

  echo "[backup] start db=${db_name} -> ${output_file}"
  docker exec "${container_name}" pg_dump -U "${BACKUP_DB_USER}" "${db_name}" | gzip -9 > "${output_file}"
  echo "[backup] done db=${db_name}"
}

cleanup_old_backups() {
  local db_name="$1"
  echo "[cleanup] removing ${db_name}_*.sql.gz older than ${RETENTION_DAYS} days in ${BACKUP_DIR}"
  find "${BACKUP_DIR}" -maxdepth 1 -type f -name "${db_name}_*.sql.gz" -mtime +"${RETENTION_DAYS}" -print -delete
}

main() {
  local container_name
  local lock_dir
  local old_umask

  mkdir -p "${BACKUP_DIR}"

  lock_dir="${BACKUP_DIR}/.weekly_backup.lock"
  if ! mkdir "${lock_dir}" 2>/dev/null; then
    echo "Backup is already running (lock exists: ${lock_dir})." >&2
    exit 1
  fi
  trap "rmdir '${lock_dir}'" EXIT

  old_umask="$(umask)"
  umask 077

  container_name="$(resolve_container_name)"
  IFS=',' read -r -a db_list <<< "${BACKUP_DB_NAMES}"

  for db_name in "${db_list[@]}"; do
    db_name="$(echo "${db_name}" | xargs)"
    [[ -z "${db_name}" ]] && continue

    backup_database "${container_name}" "${db_name}"
    cleanup_old_backups "${db_name}"
  done

  umask "${old_umask}"
  echo "Weekly backup finished."
}

main "$@"
