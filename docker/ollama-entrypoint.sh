#!/bin/sh
# Development-only Ollama sidecar entrypoint.
#
# On the shared VPS this container is never started — the single Lost Vowels
# daemon serves every app over the word-games-ollama network. This exists so a
# development machine with no Ollama at all can still run the stack with
# `docker compose --profile ollama up`.
#
# Start the daemon, pull the configured model into the persistent model volume,
# warm it, then hand the process back to the daemon.
set -eu

MODEL="${OLLAMA_MODEL:-llama3.2:3b}"

echo "[ollama] starting local daemon"
ollama serve &
SERVE_PID=$!

i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 120 ]; then
    echo "[ollama] daemon failed to start" >&2
    kill "$SERVE_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done

echo "[ollama] pulling model: $MODEL"
ollama pull "$MODEL"

# The first pull is large; warm the loaded weights once so the first RAG answer
# does not pay cold-start latency. A warmup failure is non-fatal — the
# healthcheck still verifies the model exists locally.
echo "[ollama] warming model"
echo "ok" | ollama run "$MODEL" >/dev/null 2>&1 || \
  echo "[ollama] warmup failed (non-fatal)" >&2

echo "[ollama] ready"
wait "$SERVE_PID"
