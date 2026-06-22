#!/usr/bin/env bash
# Manual Phase 0 smoke test against Evolution API (health → instance → QR → sendText).
# Requires: evolution_api running, proxy credentials, EVOLUTION_API_KEY set.
#
# Usage (from repo root):
#   export EVOLUTION_API_URL=http://localhost:8080
#   export EVOLUTION_API_KEY=your-key
#   export EVOLUTION_TEST_INSTANCE_NAME=pilot-account-248
#   export EVOLUTION_TEST_RECIPIENT=989121234567
#   export PROXY_TEST_HOST=... PROXY_TEST_PORT=... PROXY_TEST_USER=... PROXY_TEST_PASS=...
#   bash scripts/evolution_phase0_manual_test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${EVOLUTION_PHASE0_LOG_DIR:-${REPO_ROOT}/logs/phase0-evolution}"
mkdir -p "${LOG_DIR}"

EVOLUTION_API_URL="${EVOLUTION_API_URL:-http://localhost:8080}"
EVOLUTION_API_URL="${EVOLUTION_API_URL%/}"
EVOLUTION_API_KEY="${EVOLUTION_API_KEY:?EVOLUTION_API_KEY is required}"
EVOLUTION_TEST_INSTANCE_NAME="${EVOLUTION_TEST_INSTANCE_NAME:?EVOLUTION_TEST_INSTANCE_NAME is required}"
EVOLUTION_TEST_RECIPIENT="${EVOLUTION_TEST_RECIPIENT:?EVOLUTION_TEST_RECIPIENT is required}"
PROXY_TEST_HOST="${PROXY_TEST_HOST:?PROXY_TEST_HOST is required}"
PROXY_TEST_PORT="${PROXY_TEST_PORT:?PROXY_TEST_PORT is required}"
PROXY_TEST_USER="${PROXY_TEST_USER:?PROXY_TEST_USER is required}"
PROXY_TEST_PASS="${PROXY_TEST_PASS:?PROXY_TEST_PASS is required}"
PROXY_TEST_PROTOCOL="${PROXY_TEST_PROTOCOL:-http}"
EVOLUTION_TEST_MESSAGE="${EVOLUTION_TEST_MESSAGE:-Phase 0 Evolution API manual test}"

# curl wrapper: no -f; prints human-readable errors on HTTP >= 400 or connection failure.
# Usage: curl_evo "label" METHOD url [extra curl args...]
# Sets global vars: CURL_EVO_BODY, CURL_EVO_CODE
curl_evo() {
  local label="$1"
  local method="$2"
  local url="$3"
  shift 3

  local response
  local curl_status
  set +e
  response="$(curl -sS -w "\n%{http_code}" -X "${method}" "${url}" \
    -H "apikey: ${EVOLUTION_API_KEY}" \
    "$@")"
  curl_status=$?
  set -e
  if [[ ${curl_status} -ne 0 ]]; then
    echo "Evolution API در دسترس نیست، ابتدا این را بررسی کنید" >&2
    echo "خطا در فراخوانی ${label}: اتصال برقرار نشد (curl exit ${curl_status})" >&2
    exit 1
  fi

  CURL_EVO_BODY="$(echo "${response}" | sed '$d')"
  CURL_EVO_CODE="$(echo "${response}" | tail -n1)"

  if [[ "${CURL_EVO_CODE}" -lt 200 || "${CURL_EVO_CODE}" -ge 300 ]]; then
    echo "خطا در فراخوانی ${label}: HTTP ${CURL_EVO_CODE} — ${CURL_EVO_BODY}" >&2
    exit 1
  fi
}

extract_qr_base64() {
  local body="$1"
  if command -v jq >/dev/null 2>&1; then
    echo "${body}" | jq -r '.qrcode.base64 // .qrcode.code // .base64 // .code // empty' 2>/dev/null || true
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "${body}" | python3 -c "
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(0)
q = d.get('qrcode') or {}
print(q.get('base64') or q.get('code') or d.get('base64') or d.get('code') or '')
"
    return
  fi
  echo ""
}

echo "=== Evolution Phase 0 manual test ==="
echo "API: ${EVOLUTION_API_URL}"
echo "Instance: ${EVOLUTION_TEST_INSTANCE_NAME}"
echo "Recipient: ${EVOLUTION_TEST_RECIPIENT}"
echo "Log dir: ${LOG_DIR}"
echo

# مرحله ۰ — بررسی در دسترس بودن Evolution API
echo "--- [0/3] GET /instance/fetchInstances ---"
local_fetch_status=0
set +e
response="$(curl -sS -w "\n%{http_code}" -X GET "${EVOLUTION_API_URL}/instance/fetchInstances" \
  -H "apikey: ${EVOLUTION_API_KEY}")"
local_fetch_status=$?
set -e
if [[ ${local_fetch_status} -ne 0 ]]; then
  echo "Evolution API در دسترس نیست، ابتدا این را بررسی کنید" >&2
  echo "Evolution API is not reachable — check docker compose, EVOLUTION_API_URL, and EVOLUTION_API_KEY first." >&2
  echo "خطا در فراخوانی fetchInstances: اتصال برقرار نشد (curl exit ${local_fetch_status})" >&2
  exit 1
fi
CURL_EVO_BODY="$(echo "${response}" | sed '$d')"
CURL_EVO_CODE="$(echo "${response}" | tail -n1)"
if [[ "${CURL_EVO_CODE}" -lt 200 || "${CURL_EVO_CODE}" -ge 300 ]]; then
  echo "Evolution API در دسترس نیست، ابتدا این را بررسی کنید" >&2
  echo "Evolution API is not reachable — fetchInstances returned HTTP ${CURL_EVO_CODE}" >&2
  echo "${CURL_EVO_BODY}" >&2
  exit 1
fi
echo "${CURL_EVO_BODY}" | head -c 500
echo
if [[ -n "${CURL_EVO_BODY}" && "${#CURL_EVO_BODY}" -gt 500 ]]; then
  echo "... (truncated)"
fi
echo

# ۱) ساخت instance با proxy اختصاصی (از متغیرهای PROXY_TEST_*)
echo "--- [1/3] POST /instance/create (with proxy) ---"
CREATE_PAYLOAD="$(cat <<EOF
{
  "instanceName": "${EVOLUTION_TEST_INSTANCE_NAME}",
  "qrcode": true,
  "integration": "WHATSAPP-BAILEYS",
  "proxy": {
    "enabled": true,
    "host": "${PROXY_TEST_HOST}",
    "port": "${PROXY_TEST_PORT}",
    "protocol": "${PROXY_TEST_PROTOCOL}",
    "username": "${PROXY_TEST_USER}",
    "password": "${PROXY_TEST_PASS}"
  }
}
EOF
)"
curl_evo "instance/create" POST "${EVOLUTION_API_URL}/instance/create" \
  -H "Content-Type: application/json" \
  -d "${CREATE_PAYLOAD}"
echo "${CURL_EVO_BODY}"
echo

# ۲) دریافت QR / وضعیت اتصال
echo "--- [2/3] GET /instance/connect/${EVOLUTION_TEST_INSTANCE_NAME} ---"
curl_evo "instance/connect" GET "${EVOLUTION_API_URL}/instance/connect/${EVOLUTION_TEST_INSTANCE_NAME}"
CONNECT_BODY="${CURL_EVO_BODY}"
QR_B64="$(extract_qr_base64 "${CONNECT_BODY}")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
QR_FILE="${LOG_DIR}/qr_${EVOLUTION_TEST_INSTANCE_NAME}_${TIMESTAMP}.txt"

if [[ -n "${QR_B64}" ]]; then
  printf '%s' "${QR_B64}" > "${QR_FILE}"
  echo "QR ذخیره شد در فایل ${QR_FILE} — آن را به یک تصویر تبدیل کن یا با ابزار base64-to-png دستی نمایش بده."
else
  printf '%s\n' "${CONNECT_BODY}" > "${QR_FILE}.response.json"
  echo "فیلد base64 در پاسخ یافت نشد؛ پاسخ کامل در ${QR_FILE}.response.json ذخیره شد."
fi
echo

# تأیید تعاملی قبل از ارسال پیام
read -r -p "آیا QR را با موبایل اسکن کردی و اتصال برقرار شد؟ (y/n): " confirmed
if [[ "${confirmed}" != "y" && "${confirmed}" != "Y" ]]; then
  echo "ارسال پیام لغو شد — ابتدا QR را اسکن کن و دوباره اسکریپت را از مرحلهٔ connect اجرا کن."
  exit 0
fi

# ۳) ارسال پیام تست (پس از اسکن QR و اتصال موفق)
echo "--- [3/3] POST /message/sendText/${EVOLUTION_TEST_INSTANCE_NAME} ---"
SEND_PAYLOAD="$(cat <<EOF
{
  "number": "${EVOLUTION_TEST_RECIPIENT}",
  "text": "${EVOLUTION_TEST_MESSAGE}"
}
EOF
)"
curl_evo "message/sendText" POST "${EVOLUTION_API_URL}/message/sendText/${EVOLUTION_TEST_INSTANCE_NAME}" \
  -H "Content-Type: application/json" \
  -d "${SEND_PAYLOAD}"
echo "${CURL_EVO_BODY}"
echo
echo "Done."
