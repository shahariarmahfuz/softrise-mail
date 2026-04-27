#!/usr/bin/env bash
# Soft Rice Mail — curl-based smoke test.
# Expects the FastAPI server to be running on http://127.0.0.1:5000.
#
# The webhook is PUBLIC (no secret required) to match the Cloudflare Email
# Worker contract, so this script never sends X-Webhook-Secret.

set -euo pipefail

BASE="${BASE:-http://127.0.0.1:5000}"
COOKIE_JAR="$(mktemp -t softrise.XXXXXX.cookies)"
trap 'rm -f "$COOKIE_JAR"' EXIT

SUFFIX=$(date +%s%N | tail -c 7)
USERNAME="curl${SUFFIX}"
PASSWORD="curl-test-pass-1!"

GREEN=$'\033[32m'
RED=$'\033[31m'
RESET=$'\033[0m'
fail=0

step() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        printf "  ${GREEN}OK${RESET}    %s\n" "$label"
    else
        printf "  ${RED}FAIL${RESET}  %s\n" "$label"
        fail=$((fail + 1))
    fi
}

req_json() {
    curl -sS -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$@"
}

require_status() {
    local expected="$1"
    shift
    local actual
    actual=$(curl -sS -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$@")
    [[ "$actual" == "$expected" ]] || {
        echo "    expected $expected, got $actual"
        return 1
    }
}

echo "Running smoke tests against $BASE"

# 1. /health
step "GET /health" require_status 200 "$BASE/health"

# 2. Register
RESP=$(req_json -X POST "$BASE/api/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USERNAME\",\"email\":\"$USERNAME@example.com\",\"password\":\"$PASSWORD\",\"name\":\"Curl Smoker\"}")
echo "$RESP" | grep -q '"username"' && \
    printf "  ${GREEN}OK${RESET}    register %s\n" "$USERNAME" || \
    { printf "  ${RED}FAIL${RESET}  register: %s\n" "$RESP"; fail=$((fail+1)); }

# 3. /api/auth/me
step "GET /api/auth/me"            require_status 200 "$BASE/api/auth/me"

# 4. Default mailbox visible
DEFAULT_EMAIL=$(req_json "$BASE/api/auth/me" | python3 -c 'import json,sys; print(json.load(sys.stdin)["default_mailbox"]["email_address"])')
[[ "$DEFAULT_EMAIL" == *"@softrise.app" ]] && \
    printf "  ${GREEN}OK${RESET}    default mailbox %s\n" "$DEFAULT_EMAIL" || \
    { printf "  ${RED}FAIL${RESET}  default mailbox '%s'\n" "$DEFAULT_EMAIL"; fail=$((fail+1)); }

# 5. Webhook with NO secret header (Cloudflare Worker contract) → must NOT 401.
#    Unknown recipient yields 202 with {"ok":true,"stored":false}.
step "POST /webhook/email (unknown recipient, no secret) -> 202" \
    require_status 202 -X POST -H 'Content-Type: application/json' \
    -d '{"from":"x@x.com","to":"nobody@softrise.app","size":0,"headers":{},"raw_email":""}' \
    "$BASE/webhook/email"

# 6. Webhook delivers email — still no X-Webhook-Secret header.
RAW="From: friend@example.com
To: $DEFAULT_EMAIL
Subject: Curl smoke test $SUFFIX

Body marker:$SUFFIX"
PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'from':'friend@example.com','to':sys.argv[1],'size':len(sys.argv[2]),'headers':{},'raw_email':sys.argv[2]}))" "$DEFAULT_EMAIL" "$RAW")
WEBHOOK_RESP=$(curl -sS -X POST "$BASE/webhook/email" \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD")
echo "$WEBHOOK_RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") and d.get("stored") else 1)' && \
    printf "  ${GREEN}OK${RESET}    webhook delivered (stored=true)\n" || \
    { printf "  ${RED}FAIL${RESET}  webhook delivery: %s\n" "$WEBHOOK_RESP"; fail=$((fail+1)); }

# 7. Inbox now has the message
INBOX=$(req_json "$BASE/api/messages?folder=inbox")
echo "$INBOX" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["total"] >= 1 else 1)' && \
    printf "  ${GREEN}OK${RESET}    inbox shows email\n" || \
    { printf "  ${RED}FAIL${RESET}  inbox empty\n"; fail=$((fail+1)); }

# 8. Search finds it
SEARCH=$(req_json "$BASE/api/messages?search=marker:$SUFFIX")
echo "$SEARCH" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["total"] >= 1 else 1)' && \
    printf "  ${GREEN}OK${RESET}    search finds email\n" || \
    { printf "  ${RED}FAIL${RESET}  search returned no results\n"; fail=$((fail+1)); }

# 9. Star / Archive / Trash / Delete
MID=$(echo "$INBOX" | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["id"])')
step "POST /api/messages/{id}/read"     require_status 200 -X POST -H 'Content-Type: application/json' -d '{"is_read":true}' "$BASE/api/messages/$MID/read"
step "POST /api/messages/{id}/star"     require_status 200 -X POST -H 'Content-Type: application/json' -d '{"is_starred":true}' "$BASE/api/messages/$MID/star"
step "POST /api/messages/{id}/archive"  require_status 200 -X POST "$BASE/api/messages/$MID/archive"
step "POST /api/messages/{id}/trash"    require_status 200 -X POST "$BASE/api/messages/$MID/trash"
step "DELETE /api/messages/{id}?force=true" require_status 200 -X DELETE "$BASE/api/messages/$MID?force=true"

# 10. Read-all
step "POST /api/messages/read-all"      require_status 200 -X POST -H 'Content-Type: application/json' -d '{"folder":"inbox"}' "$BASE/api/messages/read-all"

# 11. Mailbox APIs: create temp, list, delete, restore
TEMP_RESP=$(req_json -X POST "$BASE/api/mailboxes/temp" -H 'Content-Type: application/json' -d "{\"local_part\":\"box${SUFFIX}\"}")
TEMP_ID=$(echo "$TEMP_RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
[[ -n "$TEMP_ID" ]] && \
    printf "  ${GREEN}OK${RESET}    create temp mailbox\n" || \
    { printf "  ${RED}FAIL${RESET}  create temp mailbox: %s\n" "$TEMP_RESP"; fail=$((fail+1)); }
step "DELETE /api/mailboxes/{id}"       require_status 200 -X DELETE "$BASE/api/mailboxes/$TEMP_ID"
step "POST /api/mailboxes/{id}/restore" require_status 200 -X POST "$BASE/api/mailboxes/$TEMP_ID/restore"

# 12. Admin endpoints rejected for normal user
step "GET /api/admin/stats -> 403"      require_status 403 "$BASE/api/admin/stats"

# 13. Logout
step "POST /api/auth/logout"            require_status 200 -X POST "$BASE/api/auth/logout"

if (( fail > 0 )); then
    echo
    echo "${RED}${fail} step(s) failed.${RESET}"
    exit 1
fi
echo
echo "${GREEN}All smoke steps passed.${RESET}"
