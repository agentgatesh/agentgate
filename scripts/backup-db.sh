#!/bin/bash
# AgentGate — PostgreSQL backup via email (Resend).
#
# Runs `pg_dump` inside the compose stack, gzips the result, and sends
# it as an attachment to BACKUP_TO via the Resend API. No external
# storage cost — the user's inbox is the offsite copy.
#
# This script is intentionally DISARMED: there is no cron entry installed
# by default. Activate it only when the DB holds data worth keeping
# (first real signup / first paid transaction). See the "Activation"
# block at the bottom for the one-liner.
#
# Requirements on the VPS:
#   - /opt/agentgate/.env must define RESEND_API_KEY and BACKUP_TO
#     (BACKUP_TO = the email address that receives the dumps)
#   - docker compose service `db` running
#   - base64, gzip, curl, jq (all present on Debian/Ubuntu standard)
#
# Usage (manual):
#   /opt/agentgate/scripts/backup-db.sh
#
# Exit codes:
#   0  success
#   1  missing config (RESEND_API_KEY or BACKUP_TO)
#   2  pg_dump failed
#   3  attachment larger than MAX_ATTACH_MB
#   4  Resend API rejected the send
#
# Telemetry: one line to $LOG_FILE per run (success or failure).

set -euo pipefail

# ---------------------------------------------------------------------------
# Config (override via env if needed)
# ---------------------------------------------------------------------------

REPO_DIR="${REPO_DIR:-/opt/agentgate}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"
LOG_FILE="${LOG_FILE:-/var/log/agentgate-backup.log}"
TMP_DIR="${TMP_DIR:-/tmp}"
MAX_ATTACH_MB="${MAX_ATTACH_MB:-35}"   # Resend supports ~40 MB; keep margin
FROM_EMAIL="${FROM_EMAIL:-AgentGate Backups <noreply@agentgate.sh>}"

# ---------------------------------------------------------------------------
# Load .env (only the two keys we need, never export the whole file)
# ---------------------------------------------------------------------------

if [ -f "$ENV_FILE" ]; then
  RESEND_API_KEY="$(grep -E '^RESEND_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  BACKUP_TO="$(grep -E '^BACKUP_TO=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
fi

RESEND_API_KEY="${RESEND_API_KEY:-}"
BACKUP_TO="${BACKUP_TO:-}"

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

if [ -z "$RESEND_API_KEY" ] || [ -z "$BACKUP_TO" ]; then
  log "ERROR: RESEND_API_KEY or BACKUP_TO missing from $ENV_FILE"
  exit 1
fi

# ---------------------------------------------------------------------------
# Dump + compress
# ---------------------------------------------------------------------------

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
DUMP_PATH="$TMP_DIR/agentgate-${TIMESTAMP}.sql.gz"
PAYLOAD_PATH="$TMP_DIR/agentgate-backup-payload-${TIMESTAMP}.json"

cleanup() {
  rm -f "$DUMP_PATH" "$PAYLOAD_PATH"
}
trap cleanup EXIT

cd "$REPO_DIR"
if ! docker compose exec -T db pg_dump -U agentgate agentgate 2>/dev/null | gzip > "$DUMP_PATH"; then
  log "ERROR: pg_dump failed"
  exit 2
fi

SIZE_BYTES="$(stat -c%s "$DUMP_PATH" 2>/dev/null || stat -f%z "$DUMP_PATH")"
SIZE_MB=$((SIZE_BYTES / 1024 / 1024))

if [ "$SIZE_MB" -gt "$MAX_ATTACH_MB" ]; then
  log "ERROR: dump is ${SIZE_MB} MB, above ${MAX_ATTACH_MB} MB limit — email NOT sent"
  # We still keep the dump on disk so you can retrieve it manually.
  trap - EXIT
  rm -f "$PAYLOAD_PATH"
  log "Dump retained at $DUMP_PATH for manual transfer"
  exit 3
fi

# ---------------------------------------------------------------------------
# Build Resend payload (jq handles JSON escaping + base64 encoding)
# ---------------------------------------------------------------------------

DUMP_B64="$(base64 -w0 "$DUMP_PATH" 2>/dev/null || base64 "$DUMP_PATH" | tr -d '\n')"
SIZE_KB=$((SIZE_BYTES / 1024))

jq -n \
  --arg from "$FROM_EMAIL" \
  --arg to "$BACKUP_TO" \
  --arg subject "AgentGate DB backup — ${TIMESTAMP} (${SIZE_KB} KB)" \
  --arg html "<p>Automated AgentGate DB dump.</p><p>Generated at <code>${TIMESTAMP} UTC</code> on <code>$(hostname)</code>. Size: ${SIZE_KB} KB.</p><p>Restore with: <code>gunzip -c attachment.sql.gz | docker compose exec -T db psql -U agentgate agentgate</code></p>" \
  --arg filename "agentgate-${TIMESTAMP}.sql.gz" \
  --arg content "$DUMP_B64" \
  '{from: $from, to: [$to], subject: $subject, html: $html, attachments: [{filename: $filename, content: $content}]}' \
  > "$PAYLOAD_PATH"

# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

HTTP_STATUS="$(curl -s -o "$TMP_DIR/resend-resp.json" -w '%{http_code}' \
  -X POST https://api.resend.com/emails \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary "@$PAYLOAD_PATH")"

if [ "$HTTP_STATUS" != "200" ]; then
  log "ERROR: Resend returned HTTP $HTTP_STATUS — $(cat "$TMP_DIR/resend-resp.json" 2>/dev/null | head -c 300)"
  rm -f "$TMP_DIR/resend-resp.json"
  exit 4
fi

EMAIL_ID="$(jq -r '.id // ""' < "$TMP_DIR/resend-resp.json")"
rm -f "$TMP_DIR/resend-resp.json"
log "OK: dump ${SIZE_KB} KB emailed to $BACKUP_TO (Resend id=$EMAIL_ID)"

# ---------------------------------------------------------------------------
# Activation (DO NOT uncomment unless you really want cron).
# To enable daily backups at 03:17 UTC on the VPS:
#
#   sudo crontab -e
#   17 3 * * *  /opt/agentgate/scripts/backup-db.sh >> /var/log/agentgate-backup.log 2>&1
#
# To deactivate: remove that line from crontab -e.
# To test manually: run the script directly; check the target inbox.
# ---------------------------------------------------------------------------
