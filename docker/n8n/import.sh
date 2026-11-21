#!/bin/sh
# Unattended n8n bootstrap (docs/design/n8n.md). Runs in a one-shot container from the n8n image,
# with the same database and N8N_ENCRYPTION_KEY as the n8n server, before the server starts.
#   1. render credentials with fixed IDs from environment variables into a private temp dir
#   2. render the workflows (placeholders for the API URL, the web app URL and the agency inbox)
#   3. import both (imports upsert by ID, so running this again is safe)
#   4. publish every workflow, so the server registers the production webhooks when it starts
set -eu

: "${N8N_WEBHOOK_TOKEN:?N8N_WEBHOOK_TOKEN is required}"
: "${N8N_INTEGRATION_TOKEN:?N8N_INTEGRATION_TOKEN is required}"
API_URL="${POLICYPILOT_API_URL:-http://api:8000}"
INBOX="${AGENCY_INBOX:-agents@policypilot.test}"
APP_URL="${APP_BASE_URL:-http://localhost:8080}"
SMTP_HOST="${SMTP_HOST:-mailpit}"
SMTP_PORT="${SMTP_PORT:-1025}"
WORKFLOW_DIR="${WORKFLOW_DIR:-/workflows}"

umask 077
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

json_string() {
  # Escape a value for a JSON string literal (backslash and double quote; tokens are URL-safe).
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

cat > "$work/credentials.json" <<EOF
[
  {
    "id": "pp-smtp-mailpit",
    "name": "PolicyPilot SMTP",
    "type": "smtp",
    "data": {
      "user": "",
      "password": "",
      "host": "$(json_string "$SMTP_HOST")",
      "port": $SMTP_PORT,
      "secure": false,
      "disableStartTls": true
    }
  },
  {
    "id": "pp-webhook-auth",
    "name": "PolicyPilot event webhook token",
    "type": "httpHeaderAuth",
    "data": { "name": "X-PolicyPilot-Token", "value": "$(json_string "$N8N_WEBHOOK_TOKEN")" }
  },
  {
    "id": "pp-api-token",
    "name": "PolicyPilot integrations API token",
    "type": "httpHeaderAuth",
    "data": { "name": "Authorization", "value": "Bearer $(json_string "$N8N_INTEGRATION_TOKEN")" }
  }
]
EOF

mkdir "$work/workflows"
ids=""
for file in "$WORKFLOW_DIR"/*.json; do
  sed -e "s#__POLICYPILOT_API_URL__#${API_URL}#g" -e "s#__AGENCY_INBOX__#${INBOX}#g" \
    -e "s#__APP_BASE_URL__#${APP_URL}#g" "$file" > "$work/workflows/$(basename "$file")"
  id="$(sed -n 's/^  "id": "\(pp-[a-z0-9-]*\)",$/\1/p' "$file")"
  [ -n "$id" ] || { echo "no top-level pp-* id in $file" >&2; exit 1; }
  ids="$ids $id"
done

n8n import:credentials --input="$work/credentials.json"
n8n import:workflow --separate --input="$work/workflows"
for id in $ids; do
  n8n publish:workflow --id="$id"
done
echo "n8n bootstrap complete:$ids"
