#!/usr/bin/env bash
# Restore a known-good demo board, then prove it is actually clean.
#
#   ./scripts/reset-demo.sh                 # standard board, budget 5000
#   TOKEN_BUDGET=60 ./scripts/reset-demo.sh # near-spent, for the ceiling beat
#   SKIP_WARM=1 ./scripts/reset-demo.sh     # skip the Lambda pre-warm
#
# Run this between every take. `seed.sh` owns the DynamoDB demo state and is
# called from here rather than duplicated — two definitions of "a clean board"
# would drift apart. What this adds around it is the four things that bit us
# during Phase 4 and 5 verification and are invisible until they are on camera:
#
#   1. stale CONN# rows  — a crashed tab leaves a row behind, and the member
#                          count is on screen for the whole recording
#   2. in-flight SQS     — a task from the last take lands mid-take and moves a
#                          slot nobody touched
#   3. cold Lambdas      — the first claim of a take pays ~1.5s of cold start
#   4. no verification   — seed.sh exiting 0 says the CLI worked, not that the
#                          board a browser renders is clean
#
# The final check reads the board back the way a judge's browser does: a real
# WebSocket, a real `hello`, a real `state_snapshot`.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
TABLE="${TABLE_NAME:-hiveos-state}"
TEAM="${TEAM_ID:-alpha}"
STACK="${STACK_NAME:-hiveos}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaulted here, not in seed.sh. seed.sh is the generic seeder and keeps its
# 1,000,000 production-shaped default; this is the demo-facing script, and at
# 1,000,000 a ~58-token task moves the meter 0.006% — invisible on camera,
# which is the one thing the recording cannot afford. Exporting it also means
# the operator cannot get this wrong by running the script bare at 2am.
export TOKEN_BUDGET="${TOKEN_BUDGET:-5000}"

stack_output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

echo "== HiveOS demo reset =="

# --- 1. stale connection rows ------------------------------------------------
# Membership is per-connection (CONN# row each). A browser that crashed or a
# laptop that slept never fired $disconnect, so the row outlives the client and
# inflates the member count on every screen.
stale=0
while read -r sk; do
  [ -z "$sk" ] && continue
  aws dynamodb delete-item --region "$REGION" --table-name "$TABLE" \
    --key "{\"PK\":{\"S\":\"TEAM#${TEAM}\"},\"SK\":{\"S\":\"${sk}\"}}" >/dev/null
  stale=$((stale + 1))
done < <(aws dynamodb query --region "$REGION" --table-name "$TABLE" \
           --key-condition-expression "PK = :pk AND begins_with(SK, :s)" \
           --expression-attribute-values \
             "{\":pk\":{\"S\":\"TEAM#${TEAM}\"},\":s\":{\"S\":\"CONN#\"}}" \
           --query 'Items[].SK.S' --output text 2>/dev/null | tr '\t' '\n')
echo "  CONN#*           cleared ($stale)"

if [ "$stale" -gt 0 ]; then
  echo "  ⚠  $stale connection row(s) were live — close stray browser tabs before"
  echo "     recording, or they will show up in the member count."
fi

# --- 2. drain anything still queued in SQS -----------------------------------
# A task dispatched in the last take can still be in flight. It would arrive
# mid-recording and move a slot nobody claimed. PurgeQueue is rate-limited to
# once per 60s, so a refusal here is expected on a quick re-run and is not fatal.
QUEUE_URL="$(stack_output QueueUrl)"
if aws sqs purge-queue --queue-url "$QUEUE_URL" --region "$REGION" 2>/dev/null; then
  echo "  SQS              purged"
else
  depth="$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --region "$REGION" \
            --attribute-names ApproximateNumberOfMessages \
            --query 'Attributes.ApproximateNumberOfMessages' --output text)"
  echo "  SQS              purge rate-limited (60s) — depth is $depth"
fi

# --- 3. the board itself -----------------------------------------------------
"$HERE/seed.sh"

# --- 4. pre-warm both Lambdas ------------------------------------------------
# A cold Router adds visible latency to the first claim of a take, which reads
# as the product being slow. The Agent Runner is warmed with an empty Records
# list: the handler's loop does nothing, so this costs one invocation and has
# no side effect on the board.
if [ "${SKIP_WARM:-}" != "1" ]; then
  aws lambda invoke --function-name "$(stack_output AgentRunnerFunctionName)" \
    --region "$REGION" --payload '{"Records":[]}' --cli-binary-format raw-in-base64-out \
    /dev/null >/dev/null
  echo "  agent-runner     warm"
  # The Router is warmed by the verification below, which connects for real.
fi

# --- 5. verify the way a browser sees it -------------------------------------
# seed.sh exiting 0 proves the CLI calls worked. It does not prove that the
# board a judge opens is clean. This connects over the real WebSocket, sends the
# real `hello`, and asserts on the real `state_snapshot` — and warms the Router
# on the way through.
WS_URL="$(stack_output WebSocketURL)" python3 - <<'PY'
import asyncio, json, os, sys, time

try:
    import websockets
except ImportError:
    print("  snapshot         SKIPPED — pip install websockets to verify")
    sys.exit(0)


async def main():
    url = os.environ["WS_URL"]
    started = time.monotonic()
    async with websockets.connect(f"{url}?user_id=reset-probe") as ws:
        await ws.send(json.dumps({"action": "hello"}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if frame.get("event") == "state_snapshot":
                break
    elapsed = (time.monotonic() - started) * 1000

    slots = sorted((a["slot_id"], a["status"]) for a in frame["agents"])
    problems = []
    if any(status != "IDLE" for _, status in slots):
        problems.append(f"slots not IDLE: {slots}")
    if frame.get("tokens_used", 0) != 0:
        problems.append(f"tokens_used={frame['tokens_used']}")
    if frame.get("queue"):
        problems.append(f"queue not empty: {frame['queue']}")
    if frame.get("memory"):
        problems.append(f"memory not cleared: {len(frame['memory'])} fact(s)")

    # The probe's own connection is the only member that should be present.
    members = [m.get("user_id") for m in frame.get("members", [])]
    if [m for m in members if m != "reset-probe"]:
        problems.append(f"other clients connected: {members}")

    print(f"  router           warm ({elapsed:.0f} ms to first snapshot)")
    if problems:
        print("  snapshot         DIRTY — " + "; ".join(problems))
        sys.exit(1)
    print(
        f"  snapshot         clean — both slots IDLE, "
        f"0/{frame['token_budget']} tokens, queue and memory empty"
    )


asyncio.run(main())
PY

echo "Board ready."
