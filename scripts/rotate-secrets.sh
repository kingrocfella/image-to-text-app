#!/usr/bin/env bash
# Rotate the credentials this app owns, against its running Compose database.
# Ported from NoAlibi (which ported it from Letterbolt). Never sources .env, never puts a secret in process
# arguments, never prints a secret value.
#
#   ./scripts/rotate-secrets.sh           # refuses inside the overlap window
#   ./scripts/rotate-secrets.sh --force   # rotate anyway (see below)
#
# Rotates POSTGRES_PASSWORD, SECRET_KEY and (when the console is enabled)
# ADMIN_DASHBOARD_TOKEN. The old SECRET_KEY moves to SECRET_KEY_PREVIOUS, which
# the API still accepts for verification, so nobody is signed out: every token
# refresh re-signs with the new key. The key from two rotations ago is dropped,
# so rotating again before REFRESH_TOKEN_EXPIRE_DAYS have passed would sign
# out anyone who has not opened the app since the previous rotation; that needs
# --force. External credentials (SMTP, OAuth) are copied byte-for-byte.
set +x
set -euo pipefail
umask 077

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_path="$repo_dir/.env"
lock_dir="$repo_dir/.rotate-secrets"
state_dir="$repo_dir/.secret-rotation"
compose=(docker compose --env-file "$env_path" -f "$repo_dir/docker-compose.yml" --project-directory "$repo_dir")
phase=untouched

fail() { echo "rotate-secrets: $*" >&2; exit 1; }

# Only read the simple scalar settings used here. Other lines (including
# quoted/multiline external credentials) are copied byte-for-byte by awk below.
read_setting() {
  awk -v key="$1" '
    index($0, key "=") == 1 {
      count++; value = substr($0, length(key) + 2)
      sub(/\r$/, "", value)
      sub(/^[ \t]+/, "", value); sub(/[ \t]+$/, "", value)
      # A dollar followed by a digit/punctuation is literal in Compose.
      # Reject interpolation, $$ escaping and shell substitution, not all $.
      quote = substr(value, 1, 1)
      if (quote == "\047" || quote == "\042") {
        value = substr(value, 2)
        end = index(value, quote)
        if (!end || substr(value, end + 1) !~ /^[ \t]*(#.*)?$/) exit 2
        value = substr(value, 1, end - 1)
        if (quote == "\042" && value ~ /\\|\$[A-Za-z_{$(]/) exit 2
      } else {
        sub(/[ \t]+#.*/, "", value); sub(/[ \t]+$/, "", value)
        if (value ~ /[\\\047\042]|\$[A-Za-z_{$(]/) exit 2
      }
      result = value
    }
    END { if (count != 1) exit 2; print result }
  ' "$env_path"
}

# psql encrypts the password client-side before issuing ALTER ROLE. Supplying
# it on stdin to \password avoids plaintext SQL/passwords in server logs/argv.
# The official postgres image permits local socket access as its bootstrap role.
change_password() {
  printf '%s\n%s\n' "$1" "$1" |
    "${compose[@]}" exec -T --user postgres postgres \
      psql -X -w -v ON_ERROR_STOP=1 -U "$db_user" -d "$db_name" \
      -c "\\password \"$db_user\"" >/dev/null 2>&1
}

# Use TCP (not the trusted local socket) to prove the password actually works.
# read consumes one line; the password never appears in docker's arguments.
verify_password() {
  printf '%s\n' "$1" |
    "${compose[@]}" exec -T --user postgres postgres sh -c '
      IFS= read -r PGPASSWORD
      export PGPASSWORD PGCONNECT_TIMEOUT=10
      # Loopback and the local socket are trust in the official image, so a
      # check over 127.0.0.1 accepts any password. Use the network address
      # of this container, which the image guards with scram-sha-256, and fail
      # closed if it cannot be found. ROTATE_VERIFY_HOST is a test override.
      host=${ROTATE_VERIFY_HOST:-$(hostname -i 2>/dev/null)}
      host=${host%% *}
      [ -n "$host" ] || exit 1
      exec psql -X -w -h "$host" -U "$1" -d "$2" -v ON_ERROR_STOP=1 -Atc "SELECT 1"
    ' sh "$db_user" "$db_name" >/dev/null 2>&1
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  # A signal can arrive immediately after the atomic rename, before the next
  # assignment. Once pending is gone, .env already owns the new credentials.
  if [[ "$phase" == changing && ! -f "$lock_dir/pending" ]]; then phase=committed; fi
  if [[ "$phase" == changing ]]; then
    # Includes an ambiguous failure after ALTER ROLE reached the server.
    if change_password "$old_password" && verify_password "$old_password"; then
      echo "rotate-secrets: restored the original PostgreSQL password; .env is unchanged." >&2
    else
      echo "rotate-secrets: PostgreSQL rollback failed. Keep .env and .rotate-secrets/pending private; see docs/operations.md for recovery. Rotation remains locked." >&2
      exit 1
    fi
  elif [[ "$phase" == committed && "$status" != 0 ]]; then
    echo "rotate-secrets: new credentials are saved and PostgreSQL was updated. Fix the startup error, then run make up; do not restore the old .env." >&2
  fi
  rm -f "$lock_dir/pending"
  rmdir "$lock_dir"
  exit "$status"
}

force=false
case "${1:-}" in
  '') ;;
  --force) force=true ;;
  *) fail "usage: make rotate-secrets [FORCE=1]" ;;
esac
[[ $# -le 1 ]] || fail "usage: make rotate-secrets [FORCE=1]"
for dependency in openssl docker awk make; do
  command -v "$dependency" >/dev/null || fail "$dependency is required"
done
[[ -f "$env_path" && ! -L "$env_path" ]] || fail ".env must be a regular file"
mkdir -m 700 "$lock_dir" 2>/dev/null || fail "rotation is already running, or recovery is needed (see docs/operations.md)"
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

"$repo_dir/scripts/check-env.sh" >/dev/null
for key in POSTGRES_USER POSTGRES_DB POSTGRES_PASSWORD SECRET_KEY SECRET_KEY_PREVIOUS \
  ADMIN_DASHBOARD_TOKEN REFRESH_TOKEN_EXPIRE_DAYS; do
  read_setting "$key" >/dev/null || fail "$key must be one simple, single-line literal"
done
db_user=$(read_setting POSTGRES_USER)
db_name=$(read_setting POSTGRES_DB)
old_password=$(read_setting POSTGRES_PASSWORD)
old_secret_key=$(read_setting SECRET_KEY)
admin_token=$(read_setting ADMIN_DASHBOARD_TOKEN)
refresh_days=$(read_setting REFRESH_TOKEN_EXPIRE_DAYS)
[[ "$db_user" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ && "$db_name" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] ||
  fail "unsupported PostgreSQL role or database name"
[[ -n "$old_password" && -n "$old_secret_key" ]] || fail "POSTGRES_PASSWORD and SECRET_KEY must be set"
[[ "$refresh_days" =~ ^[0-9]+$ ]] || fail "REFRESH_TOKEN_EXPIRE_DAYS must be a whole number"

# The overlap guard: SECRET_KEY_PREVIOUS is about to be replaced, so tokens it
# signed must have expired. The previous rotation is when it became "previous".
if [[ "$force" != true && -f "$state_dir/last-rotated" ]]; then
  last=$(tr -d ' \n' <"$state_dir/last-rotated")
  if [[ "$last" =~ ^[0-9]+$ ]]; then
    wait_until=$((last + refresh_days * 86400))
    now=$(date -u +%s)
    if (( now < wait_until )); then
      fail "the previous rotation was too recent: refresh tokens signed before it are still valid and would stop working. Wait $(( (wait_until - now + 86399) / 86400 )) more day(s), or rerun with FORCE=1 to sign those sessions out."
    fi
  fi
fi

"${compose[@]}" config --quiet >/dev/null 2>&1 || fail "Compose configuration is invalid"
"${compose[@]}" exec -T --user postgres postgres \
  psql -X -w -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 -Atc 'SELECT 1' \
  >/dev/null 2>&1 || fail "the Compose PostgreSQL service must be running and allow local socket administration"
verify_password "$old_password" || fail "the current .env password cannot authenticate to PostgreSQL over TCP"

new_password=$(openssl rand -hex 32)
new_secret_key=$(openssl rand -hex 32)
new_admin=''
if [[ -n "$admin_token" ]]; then new_admin=$(openssl rand -hex 32); fi
for secret in "$new_password" "$new_secret_key" ${new_admin:+"$new_admin"}; do
  [[ "$secret" =~ ^[a-f0-9]{64}$ ]] || fail "OpenSSL did not return a 64-character hexadecimal secret"
done

# Pass replacement values on stdin, not awk -v (which would expose them in ps).
# Prefix records let awk read both inputs without creating another env file.
{
  printf '%s\n' "$new_password" "$new_secret_key" "$old_secret_key" "$new_admin"
  cat "$env_path"
} | awk '
  NR == 1 { values["POSTGRES_PASSWORD"] = $0; next }
  NR == 2 { values["SECRET_KEY"] = $0; next }
  NR == 3 { values["SECRET_KEY_PREVIOUS"] = $0; next }
  NR == 4 { if (length($0)) values["ADMIN_DASHBOARD_TOKEN"] = $0; next }
  {
    key = $0; sub(/=.*/, "", key)
    if (key in values) print key "=" values[key]
    else print
  }
' > "$lock_dir/pending"
chmod 600 "$lock_dir/pending"

echo "rotate-secrets: updating PostgreSQL and verifying the new password..."
phase=changing
change_password "$new_password" || fail "could not change the PostgreSQL password"
verify_password "$new_password" || fail "new PostgreSQL password failed authentication"
mv "$lock_dir/pending" "$env_path"
phase=committed
mkdir -p "$state_dir"
chmod 700 "$state_dir"
date -u +%s > "$state_dir/last-rotated"

echo "rotate-secrets: rotated POSTGRES_PASSWORD, SECRET_KEY${new_admin:+, ADMIN_DASHBOARD_TOKEN} (64 characters each); the old SECRET_KEY is now SECRET_KEY_PREVIOUS."
echo "rotate-secrets: rebuilding and restarting the stack with make up..."
make -C "$repo_dir" up
api_id=$("${compose[@]}" ps -q web)
[[ -n "$api_id" ]] || fail "the API container did not start"
healthy=false
for ((attempt = 0; attempt < 60; attempt++)); do
  api_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$api_id")
  if [[ "$api_health" == healthy ]]; then healthy=true; break; fi
  [[ "$api_health" != unhealthy ]] || fail "the API failed its health check"
  sleep 2
done
[[ "$healthy" == true ]] || fail "the API did not become healthy within 120 seconds"
echo "rotate-secrets: complete. Updated credentials are in .env (mode 0600)."
