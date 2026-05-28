#!/usr/bin/env bash
# Phase 4.3 — 15 verification cases
# Usage: bash D:/hermes-agent/local_api/scripts/verify_4_3.sh [BASE_URL] [TOKEN]
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

    # Run curl; capture HTTP status code and body
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
echo " Phase 4.3 API Verification (15 test cases)"
echo " Base URL: $BASE_URL"
echo "============================================"
echo ""

# ── Case 1: GET /api/system/status → 200 (with valid token) ──
check "1. System status with valid token" 200 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/api/system/status"

# ── Case 2: GET /api/system/status → 401 (no token) ──
check "2. System status without token" 401 \
    "$BASE_URL/api/system/status"

# ── Case 3: GET /api/system/status → 401 (bad token) ──
check "3. System status with bad token" 401 \
    -H "Authorization: Bearer bad-token-12345" \
    "$BASE_URL/api/system/status"

# ── Case 4: POST /api/tasks → 201 (valid task) ──
check "4. Create task" 201 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"Test task","priority":"P1","description":"A test"}' \
    "$BASE_URL/api/tasks"

# ── Case 5: GET /api/tasks → 200 ──
check "5. List tasks" 200 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/api/tasks"

# ── Case 6: POST /api/tasks → 422 (forbidden field: file_path) ──
check "6. Forbidden field: file_path" 422 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"bad","file_path":"/etc/passwd"}' \
    "$BASE_URL/api/tasks"

# ── Case 7: POST /api/tasks → 422 (forbidden field: sql) ──
check "7. Forbidden field: sql" 422 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"bad","sql":"DROP TABLE tasks"}' \
    "$BASE_URL/api/tasks"

# ── Case 8: POST /api/tasks → 422 (SQL injection in title) ──
check "8. SQL injection in title" 422 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"SELECT * FROM users"}' \
    "$BASE_URL/api/tasks"

# ── Case 9: POST /api/tasks → 422 (shell injection in title) ──
check "9. Shell injection in title" 422 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"hello; rm -rf /"}' \
    "$BASE_URL/api/tasks"

# ── Case 10: POST /api/tasks → 415 (wrong Content-Type) ──
check "10. Wrong Content-Type" 415 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: text/plain" \
    -d 'bad data' \
    "$BASE_URL/api/tasks"

# ── Case 11: POST /api/tasks → 400 (invalid JSON) ──
check "11. Invalid JSON" 400 \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{not valid json}' \
    "$BASE_URL/api/tasks"

# ── Case 12: GET /api/tasks?status=pending → 200 ──
check "12. Filter tasks by status" 200 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/api/tasks?status=pending"

# ── Case 13: GET /api/tasks?priority=P1 → 200 ──
check "13. Filter tasks by priority" 200 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/api/tasks?priority=P1"

# ── Case 14: POST /api/tasks/{id}/complete → 200 ──
TASK_ID=$(curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"title":"Complete me","priority":"P2"}' \
    "$BASE_URL/api/tasks" | sed -n 's/.*"task_id":"\([^"]*\)".*/\1/p' 2>/dev/null || echo "")
if [ -n "$TASK_ID" ]; then
    check "14. Complete task" 200 \
    -X POST \
    -H "Authorization: Bearer $TOKEN" \
        "$BASE_URL/api/tasks/$TASK_ID/complete"
else
    echo "FAIL: 14. Complete task — could not create test task"
    FAIL=$((FAIL + 1))
    TOTAL=$((TOTAL + 1))
fi

# ── Case 15: GET /api/tasks/{bad_id} → 404 ──
check "15. Get nonexistent task" 404 \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE_URL/api/tasks/task_nonexistent_99999"

echo ""
echo "============================================"
echo " Results: $PASS / $TOTAL passed"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
    echo "FAILURES: $FAIL"
    exit 1
else
    echo "ALL PASSES"
    exit 0
fi