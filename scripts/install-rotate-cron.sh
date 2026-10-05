#!/bin/sh
# Install (or remove) the daily secret-rotation check for the current user.
# The job runs every day but rotates only once SECRET_ROTATION_INTERVAL_DAYS
# (.env, default 90) have passed; see scripts/rotate-secrets-cron.sh.
# Idempotent: it rewrites its own marked block and leaves every other crontab
# line untouched.
#
#   ./scripts/install-rotate-cron.sh                     # daily check at 06:20
#   SCHEDULE='0 6 * * *' ./scripts/install-rotate-cron.sh
#   ./scripts/install-rotate-cron.sh --uninstall
#
# 06:20 sits after the 01:40 backup and after Lost Vowels (04:20), Letterbolt
# (04:50), NoAlibi (05:20) and Invoice Genie (05:50) on the shared VPS. On first install the clock starts now, so the first
# rotation happens one interval from today rather than tonight. Cron follows the
# host clock (UTC on the VPS).
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
schedule=${SCHEDULE:-'20 6 * * *'}
state_dir="$repo_dir/.secret-rotation"
marker='# scangenai: scheduled secret rotation (scripts/install-rotate-cron.sh)'
entry="$schedule $repo_dir/scripts/rotate-secrets-cron.sh"

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
remaining=$(
  printf '%s' "$current" | awk -v marker="$marker" '
    $0 == marker { skip = 1; next }
    skip == 1 { skip = 0; next }
    { print }
  '
)

if [ "${1:-}" = '--uninstall' ]; then
  printf '%s\n' "$remaining" | crontab -
  printf '%s\n' 'Removed the scheduled secret-rotation cron entry.'
  exit 0
fi

umask 077
mkdir -p "$state_dir"
chmod 700 "$state_dir"
if [ ! -f "$state_dir/last-rotated" ]; then
  date -u +%s >"$state_dir/last-rotated"
fi

{
  if [ -n "$remaining" ]; then
    printf '%s\n' "$remaining"
  fi
  printf '%s\n%s\n' "$marker" "$entry"
} | crontab -

printf 'Installed:\n  %s\n' "$entry"
printf 'Interval: SECRET_ROTATION_INTERVAL_DAYS in .env (default 90)\n'
printf 'Log: %s\n' "$state_dir/rotate.log"
