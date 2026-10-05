#!/bin/sh
# Cron entry point for scheduled secret rotation. Cron cannot express "every N
# days", so this runs daily and does nothing until SECRET_ROTATION_INTERVAL_DAYS
# (.env, default 90) have passed since the last successful rotation. A day
# the host was down therefore delays a rotation by a day instead of skipping it.
#
# When due it takes a verified backup first (a rotation changes the database
# password, so there must be a restorable copy), then runs
# scripts/rotate-secrets.sh, which rotates the credentials and runs `make up`.
# The clock only resets on success, so a failed rotation is retried next day.
#
#   ./scripts/rotate-secrets-cron.sh           # rotate if due
#   ./scripts/rotate-secrets-cron.sh --force   # rotate now
#
# Install with scripts/install-rotate-cron.sh. Exits non-zero on failure so
# cron reports (and mails) it. Never prints or logs secret values.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_path="$repo_dir/.env"
state_dir=${ROTATE_STATE_DIR:-"$repo_dir/.secret-rotation"}
stamp="$state_dir/last-rotated"
log_file="$state_dir/rotate.log"
lock_dir="$state_dir/.lock"
log_max_bytes=262144

# cron runs with a near-empty PATH. Docker lives in /usr/local/bin on Linux and
# Docker Desktop, and in /opt/homebrew/bin on Apple silicon.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PATH

case "${1:-}" in
  '' | --force) ;;
  *)
    printf '%s\n' "usage: $0 [--force]" >&2
    exit 64
    ;;
esac

umask 077
mkdir -p "$state_dir"
chmod 700 "$state_dir"

log() {
  printf '%s\n' "$1" | while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$line" >>"$log_file"
  done
}

if [ -f "$log_file" ] && [ "$(wc -c <"$log_file" | tr -d ' ')" -gt "$log_max_bytes" ]; then
  mv "$log_file" "$log_file.1"
fi

# Read just this one setting; .env is never sourced (it holds secrets, and
# sourcing would execute anything written into it).
interval_days=$(
  awk 'index($0, "SECRET_ROTATION_INTERVAL_DAYS=") == 1 {
         value = substr($0, 31); sub(/[ \t]*(#.*)?\r?$/, "", value); print value
       }' "$env_path" 2>/dev/null | tail -n 1
)
interval_days=${interval_days:-90}
case "$interval_days" in
  '' | *[!0-9]* | 0)
    log "FAILED: SECRET_ROTATION_INTERVAL_DAYS must be a whole number of days, got '$interval_days'"
    exit 2
    ;;
esac

now=$(date -u +%s)
last=0
if [ -f "$stamp" ]; then
  last=$(tr -d ' \n' <"$stamp")
  case "$last" in '' | *[!0-9]*) last=0 ;; esac
fi
if [ "${1:-}" != '--force' ] && [ $((now - last)) -lt $((interval_days * 86400)) ]; then
  exit 0
fi

# A run killed outright leaves the lock behind; no rotation takes six hours.
if [ -d "$lock_dir" ] && [ -n "$(find "$lock_dir" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
  log 'removing stale lock (older than 6h)'
  rmdir "$lock_dir" 2>/dev/null || true
fi
if ! mkdir "$lock_dir" 2>/dev/null; then
  log 'skipped: another rotation is still running'
  exit 0
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT HUP INT TERM

log "rotation due (interval ${interval_days} days); taking a backup first"
status=0
result=$(make -C "$repo_dir" backup 2>&1) || status=$?
if [ "$status" -ne 0 ]; then
  log "FAILED: backup failed (exit $status); secrets were not rotated"
  log "$result"
  exit "$status"
fi
log "$result"

result=$("$repo_dir/scripts/rotate-secrets.sh" 2>&1) || status=$?
if [ "$status" -ne 0 ]; then
  log "FAILED: rotation failed (exit $status); will retry on the next daily run"
  log "$result"
  exit "$status"
fi
printf '%s\n' "$now" >"$stamp"
log "$result"
log "completed; next rotation due in ${interval_days} days"
