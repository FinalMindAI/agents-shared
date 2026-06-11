#!/bin/bash
# Default configuration for the Codex notify helper.
# To override, create ~/.codex/notify-config.sh with your values.

# macOS sound name (see /System/Library/Sounds/)
NOTIFY_SOUND="${NOTIFY_SOUND:-Glass}"

# Notification group ID (controls grouping/replacement in Notification Center)
NOTIFY_GROUP="${NOTIFY_GROUP:-codex-cli}"

# Terminal app bundle ID to activate on click (auto-detected if empty)
NOTIFY_TERMINAL_APP="${NOTIFY_TERMINAL_APP:-}"

# Maximum subtitle length when showing the first user message
NOTIFY_SUBTITLE_MAX="${NOTIFY_SUBTITLE_MAX:-72}"
