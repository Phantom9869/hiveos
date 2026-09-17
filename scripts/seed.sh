#!/usr/bin/env bash
# Seed / reset HiveOS demo state in DynamoDB.
# Idempotent — safe to re-run between demo takes.
#
#   ./scripts/seed.sh            # seed with defaults
#   TOKEN_BUDGET=500000 ./scripts/seed.sh
#
# Schema authority: CONTRACT.md

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
TABLE="${TABLE_NAME:-hiveos-state}"
TEAM="${TEAM_ID:-alpha}"
TOKEN_BUDGET="${TOKEN_BUDGET:-1000000}"
NOW="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

ddb() { aws dynamodb "$@" --region "$REGION" --table-name "$TABLE" >/dev/null; }

echo "Seeding team '$TEAM' in $TABLE ($REGION), budget=$TOKEN_BUDGET"

# --- team metadata -----------------------------------------------------------
ddb put-item --item "{
  \"PK\":{\"S\":\"TEAM#${TEAM}\"},
  \"SK\":{\"S\":\"METADATA\"},
  \"name\":{\"S\":\"Team Alpha\"},
  \"token_budget\":{\"N\":\"${TOKEN_BUDGET}\"},
  \"tokens_used\":{\"N\":\"0\"},
  \"created_at\":{\"S\":\"${NOW}\"}
}"
echo "  METADATA         tokens_used reset to 0"

# --- agent slots (IDLE) ------------------------------------------------------
for slot in coder researcher; do
  ddb put-item --item "{
    \"PK\":{\"S\":\"TEAM#${TEAM}\"},
    \"SK\":{\"S\":\"AGENT#${slot}\"},
    \"status\":{\"S\":\"IDLE\"},
    \"current_user\":{\"NULL\":true},
    \"slot_id\":{\"S\":\"${slot}\"}
  }"
  echo "  AGENT#${slot}$(printf '%*s' $((11-${#slot})) '')IDLE"
done

# --- clear queue and memory --------------------------------------------------
# Deletes every QUEUE# and MEMORY# row so a demo starts from a known state.
for prefix in QUEUE MEMORY; do
  count=0
  while read -r sk; do
    [ -z "$sk" ] && continue
    ddb delete-item --key "{\"PK\":{\"S\":\"TEAM#${TEAM}\"},\"SK\":{\"S\":\"${sk}\"}}"
    count=$((count+1))
  done < <(aws dynamodb query --region "$REGION" --table-name "$TABLE" \
             --key-condition-expression "PK = :pk AND begins_with(SK, :p)" \
             --expression-attribute-values "{\":pk\":{\"S\":\"TEAM#${TEAM}\"},\":p\":{\"S\":\"${prefix}#\"}}" \
             --query 'Items[].SK.S' --output text 2>/dev/null | tr '\t' '\n')
  echo "  ${prefix}#* cleared ($count)"
done

echo "Done."
