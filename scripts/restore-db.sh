#!/bin/sh
# Restore a custom-format dump into the Compose database. Destructive: the
# restore is --clean, so every object in the dump replaces what is there now.
#
#   ./scripts/restore-db.sh /absolute/path/backup.dump --confirm
#
# pg_restore runs inside the database container over its local socket, so this
# works with no PostgreSQL client on the host and regardless of whether the
# password in .env matches the one in the data volume.
set -eu

if [ "$#" -ne 2 ] || [ "$2" != '--confirm' ]; then
  printf '%s\n' 'Usage: restore-db.sh /absolute/path/backup.dump --confirm' >&2
  exit 2
fi

backup=$1
case "$backup" in
  /*) ;;
  *) printf '%s\n' 'Backup path must be absolute.' >&2; exit 2 ;;
esac
if [ ! -f "$backup" ]; then
  printf '%s\n' 'Backup file does not exist.' >&2
  exit 2
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Validate before touching anything: a truncated or wrong-format file fails
# here rather than half-way through a --clean restore.
docker compose --project-directory "$repo_dir" exec -T postgres pg_restore --list <"$backup" >/dev/null

# Stop writers before the clean restore. The database container stays up.
running=''
for service in web worker; do
  if docker compose --project-directory "$repo_dir" ps --status running --services |
      grep -qx "$service"; then
    running="$running $service"
  fi
done
if [ -n "$running" ]; then
  # shellcheck disable=SC2086
  docker compose --project-directory "$repo_dir" stop $running >/dev/null
fi
cleanup() {
  if [ -n "$running" ]; then
    # shellcheck disable=SC2086
    docker compose --project-directory "$repo_dir" start $running >/dev/null
  fi
}
trap cleanup EXIT HUP INT TERM

docker compose --project-directory "$repo_dir" exec -T postgres sh -lc \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges --exit-on-error' \
  <"$backup"
