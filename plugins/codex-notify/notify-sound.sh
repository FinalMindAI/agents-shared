#!/bin/bash
# Codex entrypoint that delegates to the shared notify helper.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/../notify/notify-sound.sh" "$@"
