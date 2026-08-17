#!/bin/sh
# Cron entry point for the nightly database backup. Wraps scripts/backup-db.sh
# with the three things an unattended run needs and an interactive one does not:
# a usable PATH, a single-run lock, and a log. Install it with
# scripts/install-backup-cron.sh; run it by hand to rehearse what cron will do.
#
# Exits non-zero when the backup fails, so cron reports (and mails) a failed run
# rather than failing silently. An overlapping run exits 0 — a skipped duplicate
# is not an error.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$repo_dir/backups"}
log_file=${BACKUP_LOG:-"$backup_dir/backup.log"}
lock_dir="$backup_dir/.backup.lock"
log_max_bytes=${BACKUP_LOG_MAX_BYTES:-262144}

# cron runs with a near-empty PATH. Docker lives in /usr/local/bin on Linux and
# Docker Desktop, and in /opt/homebrew/bin on Apple silicon.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PATH

umask 077
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

log() {
  printf '%s\n' "$1" | while IFS= read -r line; do
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$line" >>"$log_file"
  done
}

# One line a day, so rotation is a formality — until a broken run starts logging
# a Docker error every night. Keep one generation and move on.
if [ -f "$log_file" ]; then
  size=$(wc -c <"$log_file" | tr -d ' ')
  if [ "$size" -gt "$log_max_bytes" ]; then
    mv "$log_file" "$log_file.1"
  fi
fi

# A run killed outright leaves the lock behind; without this, every later run
# would skip forever. No backup legitimately takes six hours.
if [ -d "$lock_dir" ] && [ -n "$(find "$lock_dir" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
  log 'removing stale lock (older than 6h)'
  rmdir "$lock_dir" 2>/dev/null || true
fi

# mkdir is atomic on every POSIX filesystem: whoever creates it owns the run.
if ! mkdir "$lock_dir" 2>/dev/null; then
  log 'skipped: another backup is still running'
  exit 0
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT HUP INT TERM

log 'starting'
status=0
result=$("$repo_dir/scripts/backup-db.sh" "$backup_dir" 2>&1) || status=$?

if [ "$status" -eq 0 ]; then
  log "completed"
  log "$result"
else
  log "FAILED (exit $status)"
  log "$result"
fi
exit "$status"
