#!/bin/bash
# Codex entrypoint that delegates to the shared awake helper.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/../awake/awake.sh" "$@"
