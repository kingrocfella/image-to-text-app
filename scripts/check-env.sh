#!/bin/sh
# Enforce AGENTS.md §1: one canonical .env, mode 0600, containing exactly one
# entry for every key the code reads — and app/config.py as the only reader.
# Never prints values.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_path="$repo_dir/.env"
template_path="$repo_dir/scripts/init-env.sh"
config_path="$repo_dir/app/config.py"

fail() { echo "check-env: $*" >&2; exit 1; }

# 1. No other env file in this repository or the sibling mobile repository.
search_dirs=$repo_dir
if [ -d "$repo_dir/../image-text-react" ]; then
  search_dirs="$search_dirs $(CDPATH= cd -- "$repo_dir/../image-text-react" && pwd)"
fi
# shellcheck disable=SC2086
extra=$(find $search_dirs \( -name node_modules -o -name .git -o -name .venv \
  -o -name venv -o -name .expo -o -name .claude \) -prune -o \
  \( -name '.env' -o -name '.env.*' \) -print 2>/dev/null \
  | grep -vxF "$env_path" || true)
if [ -n "$extra" ]; then
  echo "check-env: only image-to-text-app/.env may exist; remove:" >&2
  echo "$extra" | sed 's/^/  /' >&2
  exit 1
fi

# 2. The file exists and is private.
[ -f "$env_path" ] || fail ".env is missing; run 'make init-env'"
# GNU stat first (Linux), then BSD stat (macOS): on Linux `stat -f` means
# --file-system and would print filesystem info instead of the mode.
mode=$(stat -c '%a' "$env_path" 2>/dev/null || stat -f '%Lp' "$env_path")
[ "$mode" = "600" ] || fail ".env permissions are $mode; expected 600"

# 3. app/config.py is the only place that reads the environment.
readers=$(grep -rlE 'os\.(getenv|environ)|load_dotenv' "$repo_dir/app" \
  --include='*.py' | grep -vxF "$config_path" || true)
if [ -n "$readers" ]; then
  echo "check-env: read settings through app.config, not os.getenv; found in:" >&2
  echo "$readers" | sed "s|^$repo_dir/|  |" >&2
  exit 1
fi

# 4. Every key the code reads, plus the keys Compose and the Dockerfile read,
#    appears exactly once in .env and in the init-env template.
config_keys=$(grep -Eo '"[A-Z][A-Z0-9_]*"' "$config_path" | tr -d '"' | sort -u)
# Keys read by Compose, the scripts, or the test suite rather than app/.
deployment_keys='API_HOST_PORT POSTGRES_HOST_PORT REDIS_HOST_PORT QDRANT_HOST_PORT
OLLAMA_HOST_PORT OLLAMA_KEEP_ALIVE WORKER_THREADS TEST_DATABASE_URL
SECRET_ROTATION_INTERVAL_DAYS'
missing=''
for key in $config_keys $deployment_keys; do
  [ "$(grep -c "^${key}=" "$env_path" || true)" -eq 1 ] \
    || missing="$missing\n  .env: $key"
  [ "$(grep -c "^${key}=" "$template_path" || true)" -eq 1 ] \
    || missing="$missing\n  scripts/init-env.sh: $key"
done
if [ -n "$missing" ]; then
  printf 'check-env: each key needs exactly one entry; missing or duplicated:%b\n' \
    "$missing" >&2
  exit 1
fi

# 5. Nothing in .env that nothing reads.
known=" $(echo $config_keys $deployment_keys) "
unknown=''
for key in $(grep -Eo '^[A-Za-z_][A-Za-z0-9_]*=' "$env_path" | tr -d '='); do
  case "$known" in *" $key "*) ;; *) unknown="$unknown\n  $key" ;; esac
done
if [ -n "$unknown" ]; then
  printf 'check-env: .env has keys nothing reads; remove them:%b\n' "$unknown" >&2
  exit 1
fi

if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*[[:space:]]+=' "$env_path"; then
  fail ".env has whitespace before an equals sign"
fi

echo "check-env: clean — .env is complete, mode 0600, and the only env file"
