#!/usr/bin/env bash
set -euo pipefail

# Recreate all quovy AWS SSO profiles idempotently, then log in.
# Skip-if-present: existing sso-session / profile blocks are left untouched, so
# manual edits (e.g. the [default] block) survive. --dry-run prints without writing.

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

CONFIG="${AWS_CONFIG_FILE:-$HOME/.aws/config}"

SSO_SESSION="quovy"
SSO_START_URL="https://d-90660237e7.awsapps.com/start"
SSO_REGION="us-east-1"
SSO_SCOPES="sso:account:access"

ACCOUNT_ID="988779896744"
REGION="us-east-1"
OUTPUT="json"

# profile-name role-name
PROFILES=(
  "quovy-admin AdministratorAccess"
  "quovy-readonly ReadOnlyAccess"
)

log() { printf '%s\n' "$*"; }

# True when [<header>] already exists as a section in the config.
section_exists() {
  local header="$1"
  [[ -f "$CONFIG" ]] && grep -qxF "[$header]" "$CONFIG"
}

append_block() {
  local block="$1"
  if $DRY_RUN; then
    log "would append:"
    printf '%s\n' "$block"
    return
  fi
  mkdir -p "$(dirname "$CONFIG")"
  # blank line separator only when the file already has content
  [[ -s "$CONFIG" ]] && printf '\n' >>"$CONFIG"
  printf '%s\n' "$block" >>"$CONFIG"
}

# sso-session block (aws configure set can't write [sso-session] sections)
if section_exists "sso-session $SSO_SESSION"; then
  log "✓ sso-session $SSO_SESSION already present — skipping"
else
  log "+ adding sso-session $SSO_SESSION"
  append_block "[sso-session $SSO_SESSION]
sso_start_url = $SSO_START_URL
sso_region = $SSO_REGION
sso_registration_scopes = $SSO_SCOPES"
fi

for entry in "${PROFILES[@]}"; do
  read -r name role <<<"$entry"
  if section_exists "profile $name"; then
    log "✓ profile $name already present — skipping"
  else
    log "+ adding profile $name ($role)"
    append_block "[profile $name]
sso_session = $SSO_SESSION
sso_account_id = $ACCOUNT_ID
sso_role_name = $role
region = $REGION
output = $OUTPUT"
  fi
done

if $DRY_RUN; then
  log "(dry-run) would run: aws sso login --sso-session $SSO_SESSION"
  exit 0
fi

log "→ aws sso login --sso-session $SSO_SESSION"
aws sso login --sso-session "$SSO_SESSION"
