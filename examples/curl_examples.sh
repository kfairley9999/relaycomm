#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${RELAYCOMM_BASE_URL:-http://localhost:8080}"
ACCOUNT_SID="${RELAYCOMM_ACCOUNT_SID:-AC00000000000000000000000000000001}"
AUTH_TOKEN="${RELAYCOMM_AUTH_TOKEN:-dev_auth_token_change_me}"

echo "1) Health check"
curl -sS "${BASE_URL}/health" | python3 -m json.tool

echo "2) Send an SMS (Twilio form-encoded REST API)"
curl -sS -X POST "${BASE_URL}/2010-04-01/Accounts/${ACCOUNT_SID}/Messages.json" \
  -u "${ACCOUNT_SID}:${AUTH_TOKEN}" \
  --data-urlencode "From=+15550100001" \
  --data-urlencode "To=+15550109999" \
  --data-urlencode "Body=Hello from Relaycomm curl example" | python3 -m json.tool

echo "3) List Messages"
curl -sS "${BASE_URL}/2010-04-01/Accounts/${ACCOUNT_SID}/Messages.json" \
  -u "${ACCOUNT_SID}:${AUTH_TOKEN}" | python3 -m json.tool

echo "4) Place a Voice Call with inline TwiML"
curl -sS -X POST "${BASE_URL}/2010-04-01/Accounts/${ACCOUNT_SID}/Calls.json" \
  -u "${ACCOUNT_SID}:${AUTH_TOKEN}" \
  --data-urlencode "From=+15550100001" \
  --data-urlencode "To=+15550109999" \
  --data-urlencode 'Twiml=<Response><Say>Hello Kevin!</Say><Record maxLength="10"/><Hangup/></Response>' | python3 -m json.tool
