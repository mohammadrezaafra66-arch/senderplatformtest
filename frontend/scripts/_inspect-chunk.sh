#!/bin/sh
for f in /app/.next/static/chunks/05h0qrld8zffp.js /app/.next/static/chunks/0xqumox_k3l04.js; do
  echo "=== $f ==="
  grep -o '.{0,80}account\.platform.{0,120}' "$f" 2>/dev/null | head -3
  grep -o '.{0,40}campaign_status_label.{0,120}' "$f" 2>/dev/null | head -3
done
