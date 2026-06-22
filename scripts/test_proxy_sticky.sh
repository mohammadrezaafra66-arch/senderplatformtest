#!/usr/bin/env bash
# Test proxy sticky session: sample egress IP every N minutes and verify it stays constant.
# Usage:
#   PROXY_HOST=... PROXY_PORT=... PROXY_USER=... PROXY_PASS=... ./scripts/test_proxy_sticky.sh
#   ./scripts/test_proxy_sticky.sh --host HOST --port PORT --user USER --pass PASS [--interval 300] [--iterations 12]
#
# Never commit real credentials. Read from env or CLI only.

set -euo pipefail

INTERVAL_SECONDS=300
ITERATIONS=12
LOG_FILE=""

usage() {
  cat <<'EOF'
Usage: test_proxy_sticky.sh [options]

Options:
  --host HOST          Proxy hostname (or PROXY_HOST)
  --port PORT          Proxy port (or PROXY_PORT)
  --user USER          Proxy username (or PROXY_USER)
  --pass PASS          Proxy password (or PROXY_PASS)
  --interval SECONDS   Seconds between samples (default: 300 = 5 minutes)
  --iterations N       Number of samples (default: 12 => ~1 hour at 5 min)
  --log FILE           Log file path (default: proxy_sticky_test_<timestamp>.log)
  -h, --help           Show this help

Example:
  PROXY_HOST=gate.example.com PROXY_PORT=12321 PROXY_USER=u PROXY_PASS=p \\
    ./scripts/test_proxy_sticky.sh --iterations 12
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) PROXY_HOST="${2:-}"; shift 2 ;;
    --port) PROXY_PORT="${2:-}"; shift 2 ;;
    --user) PROXY_USER="${2:-}"; shift 2 ;;
    --pass) PROXY_PASS="${2:-}"; shift 2 ;;
    --interval) INTERVAL_SECONDS="${2:-}"; shift 2 ;;
    --iterations) ITERATIONS="${2:-}"; shift 2 ;;
    --log) LOG_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

PROXY_HOST="${PROXY_HOST:-}"
PROXY_PORT="${PROXY_PORT:-}"
PROXY_USER="${PROXY_USER:-}"
PROXY_PASS="${PROXY_PASS:-}"

if [[ -z "$PROXY_HOST" || -z "$PROXY_PORT" || -z "$PROXY_USER" || -z "$PROXY_PASS" ]]; then
  echo "Error: proxy host, port, user, and pass are required (env or --host/--port/--user/--pass)." >&2
  usage
  exit 1
fi

if ! [[ "$PROXY_PORT" =~ ^[0-9]+$ ]] || ! [[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || ! [[ "$ITERATIONS" =~ ^[0-9]+$ ]]; then
  echo "Error: port, interval, and iterations must be positive integers." >&2
  exit 1
fi

if [[ "$ITERATIONS" -lt 1 ]]; then
  echo "Error: iterations must be at least 1." >&2
  exit 1
fi

if [[ -z "$LOG_FILE" ]]; then
  LOG_FILE="proxy_sticky_test_$(date +%Y%m%d_%H%M%S).log"
fi

PROXY_URL="http://${PROXY_USER}:${PROXY_PASS}@${PROXY_HOST}:${PROXY_PORT}"
IP_CHECK_URL="${IP_CHECK_URL:-https://api.ipify.org}"

declare -a SEEN_IPS=()

echo "Proxy sticky test started at $(date -Iseconds)" | tee "$LOG_FILE"
echo "Host: ${PROXY_HOST}:${PROXY_PORT} | Interval: ${INTERVAL_SECONDS}s | Iterations: ${ITERATIONS}" | tee -a "$LOG_FILE"
echo "Log file: ${LOG_FILE}" | tee -a "$LOG_FILE"
echo "---" | tee -a "$LOG_FILE"

sample_ip() {
  curl -fsS --max-time 30 -x "$PROXY_URL" "$IP_CHECK_URL" | tr -d '[:space:]'
}

for ((i = 1; i <= ITERATIONS; i++)); do
  ts="$(date -Iseconds)"
  if ! ip="$(sample_ip)"; then
    echo "${ts} sample=${i}/${ITERATIONS} ERROR=proxy_or_curl_failed" | tee -a "$LOG_FILE"
    echo "FAIL: could not fetch IP via proxy (sample ${i}/${ITERATIONS})." >&2
    exit 2
  fi

  SEEN_IPS+=("$ip")
  echo "${ts} sample=${i}/${ITERATIONS} ip=${ip}" | tee -a "$LOG_FILE"

  if [[ "$i" -lt "$ITERATIONS" ]]; then
    sleep "$INTERVAL_SECONDS"
  fi
done

echo "---" | tee -a "$LOG_FILE"
unique_ips="$(printf '%s\n' "${SEEN_IPS[@]}" | sort -u)"
unique_count="$(printf '%s\n' "${SEEN_IPS[@]}" | sort -u | wc -l | tr -d ' ')"

if [[ "$unique_count" -eq 1 ]]; then
  echo "SUCCESS: all ${ITERATIONS} samples returned the same IP: ${SEEN_IPS[0]}" | tee -a "$LOG_FILE"
  exit 0
fi

echo "FAIL: IP changed during test (${unique_count} distinct IPs):" | tee -a "$LOG_FILE"
printf '%s\n' "$unique_ips" | tee -a "$LOG_FILE"
exit 1
