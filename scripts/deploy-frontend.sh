#!/usr/bin/env bash
#
# Build the HUD and publish it to Amplify Hosting.
#
# Amplify manual-deploy mode: we hand it a zip, so there is no GitHub OAuth
# and no build role to configure. Idempotent — safe to re-run for every
# redeploy, and it reuses the existing app and branch rather than creating
# duplicates across /clear sessions.
#
# Usage:  ./scripts/deploy-frontend.sh
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-hiveos}"
REGION="${AWS_REGION:-us-east-1}"
APP_NAME="${APP_NAME:-hiveos}"
BRANCH="${BRANCH:-main}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="$REPO_ROOT/frontend"
ZIP="$REPO_ROOT/frontend-build.zip"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# --- 1. The WebSocket URL comes from the stack, never from a paste ----------

say "Resolving the WebSocket URL from stack outputs"
WS_URL="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='WebSocketURL'].OutputValue" \
  --output text)"

if [[ -z "$WS_URL" || "$WS_URL" == "None" ]]; then
  echo "error: could not read WebSocketURL from stack '$STACK_NAME'." >&2
  echo "       Is the backend deployed? aws cloudformation describe-stacks --stack-name $STACK_NAME" >&2
  exit 1
fi
echo "    VITE_WS_URL=$WS_URL"

# --- 2. Build ---------------------------------------------------------------

say "Building the frontend"
cd "$FRONTEND"
if [[ ! -d node_modules ]]; then
  npm install
fi
VITE_WS_URL="$WS_URL" npm run build

if [[ ! -f "$FRONTEND/dist/index.html" ]]; then
  echo "error: build produced no dist/index.html" >&2
  exit 1
fi

# The URL is baked in at build time, so prove it actually landed in the bundle
# rather than trusting that the env var was read. A frontend that loads but
# never connects is the single most likely way this phase fails.
if ! grep -rq "$WS_URL" "$FRONTEND/dist/assets"; then
  echo "error: '$WS_URL' is not present in the built bundle — VITE_WS_URL did not take." >&2
  exit 1
fi
echo "    verified: the WebSocket URL is baked into the bundle"

# --- 3. Zip (files at the archive root, not under dist/) -------------------

say "Packaging dist/"
rm -f "$ZIP"
( cd "$FRONTEND/dist" && zip -qr "$ZIP" . )
echo "    $ZIP ($(du -h "$ZIP" | cut -f1))"

# --- 4. Find or create the Amplify app -------------------------------------

say "Locating the Amplify app '$APP_NAME'"
APP_ID="$(aws amplify list-apps --region "$REGION" \
  --query "apps[?name=='$APP_NAME'].appId | [0]" --output text)"

if [[ -z "$APP_ID" || "$APP_ID" == "None" ]]; then
  echo "    not found — creating it"
  # The SPA rewrite keeps a deep link or a refresh from returning a bare 404.
  APP_ID="$(aws amplify create-app \
    --name "$APP_NAME" \
    --region "$REGION" \
    --platform WEB \
    --custom-rules '[{"source":"/<*>","target":"/index.html","status":"404-200"}]' \
    --query 'app.appId' --output text)"
  echo "    created app $APP_ID"
else
  echo "    reusing app $APP_ID"
fi

# --- 5. Find or create the branch ------------------------------------------

if ! aws amplify get-branch --app-id "$APP_ID" --branch-name "$BRANCH" \
     --region "$REGION" >/dev/null 2>&1; then
  echo "    creating branch '$BRANCH'"
  aws amplify create-branch --app-id "$APP_ID" --branch-name "$BRANCH" \
    --region "$REGION" >/dev/null
else
  echo "    reusing branch '$BRANCH'"
fi

# --- 6. Upload and start ----------------------------------------------------

say "Creating a deployment slot"
DEPLOYMENT="$(aws amplify create-deployment \
  --app-id "$APP_ID" --branch-name "$BRANCH" --region "$REGION" --output json)"
UPLOAD_URL="$(printf '%s' "$DEPLOYMENT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["zipUploadUrl"])')"
JOB_ID="$(printf '%s' "$DEPLOYMENT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["jobId"])')"
echo "    job $JOB_ID"

say "Uploading the bundle"
curl -sS --fail-with-body -X PUT -H 'Content-Type: application/zip' \
  --upload-file "$ZIP" "$UPLOAD_URL"
echo "    uploaded"

say "Starting the deployment"
aws amplify start-deployment --app-id "$APP_ID" --branch-name "$BRANCH" \
  --job-id "$JOB_ID" --region "$REGION" >/dev/null

# --- 7. Wait for it to actually succeed ------------------------------------
# A started deployment is not a live site. Poll until Amplify says otherwise.

say "Waiting for the job to finish"
for _ in $(seq 1 60); do
  STATUS="$(aws amplify get-job --app-id "$APP_ID" --branch-name "$BRANCH" \
    --job-id "$JOB_ID" --region "$REGION" \
    --query 'job.summary.status' --output text)"
  echo "    status=$STATUS"
  case "$STATUS" in
    SUCCEED) break ;;
    FAILED|CANCELLED)
      echo "error: deployment finished as $STATUS" >&2
      exit 1 ;;
  esac
  sleep 5
done

if [[ "$STATUS" != "SUCCEED" ]]; then
  echo "error: timed out waiting for the deployment (last status: $STATUS)" >&2
  exit 1
fi

DEFAULT_DOMAIN="$(aws amplify get-app --app-id "$APP_ID" --region "$REGION" \
  --query 'app.defaultDomain' --output text)"
PUBLIC_URL="https://${BRANCH}.${DEFAULT_DOMAIN}"

rm -f "$ZIP"

say "Deployed"
echo "    app id:     $APP_ID"
echo "    public URL: $PUBLIC_URL"
echo
echo "Verify by opening it in a fresh incognito window — a zero exit code here"
echo "only means Amplify accepted the bundle, not that the board connects."
