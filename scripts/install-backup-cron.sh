#!/bin/sh
# Install (or remove) the daily database-backup crontab entry for the current
# user. Idempotent: it rewrites its own marked block and leaves every other
# crontab line untouched, so running it twice is the same as running it once.
#
#   ./scripts/install-backup-cron.sh                     # daily at 01:40
#   SCHEDULE='0 2 * * *' ./scripts/install-backup-cron.sh
#   ./scripts/install-backup-cron.sh --uninstall
#
# Every app on the shared VPS backs up at a different time so they do not all
# contend for disk and CPU in the same minute. Cron follows the host clock; on a
# UTC server that is 01:40 UTC.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
schedule=${SCHEDULE:-'40 1 * * *'}
marker='# scangenai: daily database backup (scripts/install-backup-cron.sh)'
entry="$schedule $repo_dir/scripts/backup-db-cron.sh"

case "${1:-}" in
  '' | --uninstall) ;;
  *)
    printf '%s\n' "usage: $0 [--uninstall]" >&2
    exit 64
    ;;
esac

command -v crontab >/dev/null 2>&1 || {
  printf '%s\n' 'crontab is not available on this host.' >&2
  exit 78
}

# `crontab -l` exits 1 when no crontab exists yet; that is a valid empty start.
current=$(crontab -l 2>/dev/null || true)

# Drop any previous installation: the marker line and the entry beneath it. Every
# other line — including other projects' jobs — is preserved verbatim.
remaining=$(
  printf '%s' "$current" | awk -v marker="$marker" '
    $0 == marker { skip = 1; next }
    skip == 1 { skip = 0; next }
    { print }
  '
)

if [ "${1:-}" = '--uninstall' ]; then
  printf '%s\n' "$remaining" | crontab -
  printf '%s\n' 'Removed the daily backup cron entry.'
  exit 0
fi

{
  if [ -n "$remaining" ]; then
    printf '%s\n' "$remaining"
  fi
  printf '%s\n%s\n' "$marker" "$entry"
} | crontab -

printf 'Installed:\n  %s\n' "$entry"
printf 'Log: %s\n' "$repo_dir/backups/backup.log"
