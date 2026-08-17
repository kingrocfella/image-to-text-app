#!/bin/sh
# Create one validated, mode-0600 custom-format dump of the Compose database,
# then prune the backup directory down to the newest BACKUP_RETENTION dumps
# (default 2). Pruning runs only after the new dump has been written and
# verified, so a failed backup never destroys the copies that already exist.
#
#   ./scripts/backup-db.sh [backup_dir]
#   BACKUP_RETENTION=5 ./scripts/backup-db.sh
#
# pg_dump runs *inside* the database container over its local socket, so this
# needs no PostgreSQL client on the host and keeps working even when the
# password in .env has drifted from the one baked into the data volume.
#
# Local files are not disaster recovery: copy each dump off-host and apply
# retention there too.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${1:-"$repo_dir/backups"}
retention=${BACKUP_RETENTION:-2}
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
output="$backup_dir/scangenai-$timestamp.dump"
temporary="$output.incomplete"

case "$retention" in
  '' | *[!0-9]*)
    printf '%s\n' "BACKUP_RETENTION must be a whole number, got '$retention'." >&2
    exit 2
    ;;
  0)
    printf '%s\n' 'BACKUP_RETENTION must be at least 1.' >&2
    exit 2
    ;;
esac

umask 077
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
trap 'rm -f "$temporary"' EXIT HUP INT TERM

docker compose --project-directory "$repo_dir" exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  >"$temporary"
# A backup that cannot be listed is not a backup.
docker compose --project-directory "$repo_dir" exec -T postgres pg_restore --list <"$temporary" >/dev/null
mv "$temporary" "$output"
trap - EXIT HUP INT TERM
chmod 600 "$output"

# ---------------------------------------------------------------------------
# Qdrant vector collections.
#
# The PDF RAG embeddings are user data: losing them loses the user's indexed
# documents, and account deletion is written to fail closed against this store
# precisely because it is authoritative. So a "database backup" that captured
# only PostgreSQL would be misleading.
#
# A Qdrant failure does NOT discard the PostgreSQL dump just taken — it is kept
# and the script exits non-zero at the end, so cron reports an incomplete run
# rather than silently dropping half the backup.
# ---------------------------------------------------------------------------
qdrant_status=0
qdrant_url=${QDRANT_BACKUP_URL:-http://127.0.0.1:${QDRANT_HOST_PORT:-6333}}
qdrant_out="$backup_dir/scangenai-qdrant-$timestamp.snapshot"

if snapshot_name=$(curl -fsS -X POST "$qdrant_url/snapshots" 2>/dev/null |
    sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -n 1) && [ -n "$snapshot_name" ]; then
  if curl -fsS "$qdrant_url/snapshots/$snapshot_name" -o "$qdrant_out.incomplete" 2>/dev/null; then
    mv "$qdrant_out.incomplete" "$qdrant_out"
    chmod 600 "$qdrant_out"
    # Drop the server-side copy: it lives in the same volume this protects.
    curl -fsS -X DELETE "$qdrant_url/snapshots/$snapshot_name" >/dev/null 2>&1 || true
    printf '%s\n' "$qdrant_out"
  else
    rm -f -- "$qdrant_out.incomplete"
    printf '%s\n' 'WARNING: Qdrant snapshot could not be downloaded; PostgreSQL dump kept.' >&2
    qdrant_status=1
  fi
else
  printf '%s\n' 'WARNING: Qdrant snapshot could not be created; PostgreSQL dump kept.' >&2
  qdrant_status=1
fi

find "$backup_dir" -maxdepth 1 -type f -name 'scangenai-qdrant-*.snapshot' |
  sort -r |
  {
    seen=0
    while IFS= read -r snap; do
      seen=$((seen + 1))
      if [ "$seen" -gt "$retention" ]; then
        rm -f -- "$snap"
        printf 'pruned %s\n' "$snap" >&2
      fi
    done
  }

# Dump names carry a UTC basic-format timestamp, so a reverse lexicographic sort
# is newest-first regardless of mtime — which a copy, rsync, or restore rewrites.
# Only files matching the generated name are ever considered for deletion.
find "$backup_dir" -maxdepth 1 -type f -name 'scangenai-*.dump' |
  sort -r |
  {
    seen=0
    while IFS= read -r dump; do
      seen=$((seen + 1))
      if [ "$seen" -gt "$retention" ]; then
        rm -f -- "$dump"
        printf 'pruned %s\n' "$dump" >&2
      fi
    done
  }

# A run killed with SIGKILL leaves its temp file behind (no trap fires). Anything
# still `.incomplete` a day later cannot belong to a live run.
find "$backup_dir" -maxdepth 1 -type f -name 'scangenai-*.dump.incomplete' -mmin +1440 \
  -exec rm -f -- {} +

printf '%s\n' "$output"
exit "${qdrant_status:-0}"
