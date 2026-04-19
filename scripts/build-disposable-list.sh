#!/bin/bash
# Merge four public disposable-email-domain lists into a single sorted
# deduped file. Invoked at Docker build time (no cron).
#
# Usage: build-disposable-list.sh <output-file>
#
# If the merged result is smaller than MIN_DOMAINS (likely a network
# error during build), we leave the output file absent — the runtime
# service falls back to a ~45-domain hardcoded list and still works.
set -uo pipefail

OUTPUT_FILE="${1:-/app/data/disposable-domains.txt}"
TEMP_FILE="${OUTPUT_FILE}.tmp"
MIN_DOMAINS=5000   # well below any healthy build, well above the fallback
TIMEOUT=20

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "[disposable-list] fetching sources..."

S1=$(curl -sfL --max-time $TIMEOUT \
  "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/main/disposable_email_blocklist.conf" 2>/dev/null || echo "")
S2=$(curl -sfL --max-time $TIMEOUT \
  "https://raw.githubusercontent.com/FGRibreau/mailchecker/master/list.txt" 2>/dev/null || echo "")
S3=$(curl -sfL --max-time $TIMEOUT \
  "https://raw.githubusercontent.com/amieiro/disposable-email-domains/master/denyDomains.txt" 2>/dev/null || echo "")
S4=$(curl -sfL --max-time $TIMEOUT \
  "https://raw.githubusercontent.com/wesbos/burner-email-providers/master/emails.txt" 2>/dev/null || echo "")

{
  printf '%s\n' "$S1" "$S2" "$S3" "$S4"
  # Domains we've seen in the wild that aren't always in the public lists.
  echo "tempmail.com"
  echo "throwaway.email"
  echo "dolofan.com"
} | grep -v '^#' | grep -v '^[[:space:]]*$' | tr '[:upper:]' '[:lower:]' | sort -u \
  | grep -Ev '^(example\.(com|net|org)|test\.com|test|localhost|invalid|example)$' \
  > "$TEMP_FILE"

COUNT=$(wc -l < "$TEMP_FILE" | tr -d ' ')
echo "[disposable-list] unique domains: $COUNT"

if [ "$COUNT" -lt "$MIN_DOMAINS" ]; then
  echo "[disposable-list] below threshold ($MIN_DOMAINS) — keeping image without file, runtime will use fallback"
  rm -f "$TEMP_FILE"
  exit 0
fi

mv "$TEMP_FILE" "$OUTPUT_FILE"
echo "[disposable-list] wrote $OUTPUT_FILE"
