#!/usr/bin/env bash
# Phase 4.4 — Sync API Verification (8 test cases)
# Usage: bash D:/hermes-agent/local_api/scripts/verify_4_4.sh [BASE_URL] [TOKEN]
#
# Prerequisites: the API must be running at BASE_URL (default http://127.0.0.1:8100)

set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8100}"
TOKEN="${2:-test-token-hermes-local-4.3}"

PASS=0
FAIL=0
TOTAL=0

check() {
    local test_name="$1"
    local expected_code="$2"
    shift 2
    TOTAL=$((TOTAL + 1))

    set +e
    RESPONSE=$(curl -s -w "\n%{http_code}" "$@" 2>/dev/null)
    CURL_EXIT=$?
    set -e

    if [ $CURL_EXIT -ne 0 ] && [ "$expected_code" != "000" ]; then
        echo "FAIL: $test_name — curl error (exit=$CURL_EXIT)"
        FAIL=$((FAIL + 1))
        return
    fi

    HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [ "$HTTP_CODE" = "$expected_code" ]; then
        echo "PASS: $test_name (HTTP $HTTP_CODE)"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $test_name — expected HTTP $expected_code, got $HTTP_CODE"
        echo "  Response: $(echo "$BODY" | head -c 200)"
        FAIL=$((FAIL + 1))
    fi
}

echo "============================================"
echo " Phase 4.4 Sync API Verification (8 cases) "
echo " Base URL: $BASE_URL"
echo "============================================"
echo ""

AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"

# Create a task first (needed for FOREIGN KEY constraint)
TASK_RESP=$(curl -s -H "$AUTH" -H "$CT" -d '{"title":"Verify task 4.4"}' -X POST "$BASE_URL/api/tasks")
TASK_ID=$(echo "$TASK_RESP" | sed -n 's/.*"task_id":"\([^"]*\)".*/\1/p')
echo "Using task_id=$TASK_ID"
echo ""

# ── Case 1: Create sync state (apple_calendar) ──
check "1. Create sync state (apple_calendar)" 201 \
    -H "$AUTH" -H "$CT" \
    -d "{\"task_id\":\"$TASK_ID\",\"sync_target\":\"apple_calendar\"}" \
    "$BASE_URL/api/sync/state"

# ── Case 2: Create sync state (apple_reminder) ──
check "2. Create sync state (apple_reminder)" 201 \
    -H "$AUTH" -H "$CT" \
    -d "{\"task_id\":\"$TASK_ID\",\"sync_target\":\"apple_reminder\"}" \
    "$BASE_URL/api/sync/state"

# ── Case 3: List sync states ──
check "3. List sync states" 200 \
    -H "$AUTH" \
    "$BASE_URL/api/sync/state"

# ── Case 4: Get sync state by key ──
check "4. Get sync state by key" 200 \
    -H "$AUTH" \
    "$BASE_URL/api/sync/state/by-key?task_id=$TASK_ID&sync_target=apple_calendar"

# ── Case 5: Update sync state status ──
# Get sync_id for apple_calendar
SYNC_LIST=$(curl -s -H "$AUTH" "$BASE_URL/api/sync/state?task_id=$TASK_ID&sync_target=apple_calendar")
SYNC_ID=$(echo "$SYNC_LIST" | sed -n 's/.*"sync_id":"\([^"]*\)".*/\1/p' | head -1)

if [ -n "$SYNC_ID" ]; then
    check "5. Update sync state" 200 \
        -X PATCH \
        -H "$AUTH" -H "$CT" \
        -d '{"sync_status":"synced","external_id":"ext_verify"}' \
        "$BASE_URL/api/sync/state/$SYNC_ID"
else
    echo "FAIL: 5. Update sync state — could not get sync_id"
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
fi

# ── Case 6: Create sync log ──
if [ -n "$SYNC_ID" ]; then
    check "6. Create sync log" 201 \
        -H "$AUTH" -H "$CT" \
        -d "{\"sync_id\":\"$SYNC_ID\",\"sync_target\":\"apple_calendar\",\"sync_result\":\"success\"}" \
        "$BASE_URL/api/sync/logs"
else
    echo "FAIL: 6. Create sync log — no sync_id available"
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
fi

# ── Case 7: List sync logs ──
check "7. List sync logs" 200 \
    -H "$AUTH" \
    "$BASE_URL/api/sync/logs"

# ── Case 8: Delete sync state ──
if [ -n "$SYNC_ID" ]; then
    check "8. Delete sync state" 204 \
        -X DELETE \
        -H "$AUTH" \
        "$BASE_URL/api/sync/state/$SYNC_ID"
else
    echo "FAIL: 8. Delete sync state — no sync_id available"
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
fi

echo ""
echo "============================================"
echo " Results: $PASS / $TOTAL passed"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
    echo "FAILURES: $FAIL"
    exit 1
else
    echo "ALL PASSED"
    exit 0
fi
