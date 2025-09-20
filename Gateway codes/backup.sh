#!/bin/bash
#
# backup.sh
# Production-ready script for creating compressed, timestamped, online backups
# of the SETU Gateway's SQLite database. Designed for cron execution.

# --- Robust Scripting Configuration ---
# set -e: Exit immediately if a command exits with a non-zero status.
# set -u: Treat unset variables as an error when substituting.
# set -o pipefail: The return value of a pipeline is the status of the last command
#                  to exit with a non-zero status, or zero if no command failed.
set -euo pipefail

# --- Configuration via Environment Variables (with sensible defaults) ---
# This allows overriding paths in the cron job or for testing without modifying the script.
PROJECT_DIR="${PROJECT_DIR:-/home/pi/setu_gateway_project}"
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_DIR}/backups}"
DB_PATH="${PROJECT_DIR}/setu_gateway.db"

# --- Logging Helper ---
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# --- Main Execution ---
log "Starting database backup process..."

# 1. Pre-flight checks for robustness
if [ ! -f "$DB_PATH" ]; then
    log "ERROR: Source database file not found at ${DB_PATH}. Aborting."
    exit 1
fi

if ! mkdir -p "$BACKUP_DIR"; then
    log "ERROR: Could not create backup directory at ${BACKUP_DIR}. Aborting."
    exit 1
fi

# 2. Define timestamped backup filename
TIMESTAMP=$(date +"%Y-%m-%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/backup-${TIMESTAMP}.db"
COMPRESSED_FILE="${BACKUP_FILE}.gz"

log "Backup target: ${COMPRESSED_FILE}"

# 3. Perform the online backup using sqlite3's .backup command
# This is the safest way to copy a live SQLite database.
log "Performing online backup..."
if ! sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"; then
    log "ERROR: sqlite3 .backup command failed."
    exit 1
fi
log "Database successfully copied to temporary file: ${BACKUP_FILE}"

# 4. Compress the backup file to save space
log "Compressing backup file..."
if ! gzip "$BACKUP_FILE"; then
    log "ERROR: gzip compression failed."
    # Clean up the uncompressed file if compression fails
    rm -f "$BACKUP_FILE"
    exit 1
fi

# 5. Clean up old backups (e.g., keep the last 14 days)
log "Cleaning up old backups (older than 14 days)..."
find "$BACKUP_DIR" -name "backup-*.db.gz" -type f -mtime +14 -delete

log "Backup process completed successfully."
exit 0