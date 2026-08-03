#!/usr/bin/env bash
# =====================================================================
# Kiro Telemetry Dashboard — idempotent deploy.
# Order: backend stack -> real Lambda code -> frontend stack ->
#        Cognito callback update -> Athena reconciliation (user_project +
#        v_user_activity_enriched) -> frontend upload -> smoke test.
# Safe to re-run; every step converges. Options:
#   --rotate-secret   generate a new origin-verify secret on this run
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=config/parameters.env
[[ -f $ENV_FILE ]] || { echo "ERROR: copy config/parameters.example.env to $ENV_FILE and fill it in"; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${AWS_REGION:?}" "${DATABASE:?}" "${WORKGROUP:?}" "${LOG_BUCKET:?}"
: "${ATHENA_RESULTS_PREFIX:?}" "${MAPPING_PREFIX:?}" "${COGNITO_DOMAIN_PREFIX:?}" "${ADMIN_EMAIL:?}"
export AWS_DEFAULT_REGION="$AWS_REGION"

BACKEND_STACK=kiro-dashboard-backend
FRONTEND_STACK=kiro-dashboard-frontend
SECRET_PARAM=/kiro-dashboard/origin-verify-secret

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- secret
# Persisted in SSM SecureString so re-runs reuse it (rotation on demand).
if [[ "${1:-}" == "--rotate-secret" ]] || ! ORIGIN_SECRET=$(aws ssm get-parameter --name "$SECRET_PARAM" \
      --with-decryption --query Parameter.Value --output text 2>/dev/null); then
  log "Generating origin-verify secret"
  ORIGIN_SECRET=$(openssl rand -hex 32)
  aws ssm put-parameter --name "$SECRET_PARAM" --type SecureString \
    --value "$ORIGIN_SECRET" --overwrite >/dev/null
fi

# ---------------------------------------------------------------- backend
# Detect SSE-KMS on the log bucket; the query role then needs that key.
KMS_KEY_ARN=$(aws s3api get-bucket-encryption --bucket "$LOG_BUCKET" \
  --query "ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.KMSMasterKeyID" \
  --output text 2>/dev/null || true)
[[ "$KMS_KEY_ARN" == "None" || "$KMS_KEY_ARN" != arn:aws:kms:* ]] && KMS_KEY_ARN=""

log "Deploying backend stack ($BACKEND_STACK)"
aws cloudformation deploy \
  --stack-name "$BACKEND_STACK" \
  --template-file cloudformation/20_backend.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    DatabaseName="$DATABASE" \
    WorkgroupName="$WORKGROUP" \
    LogBucketName="$LOG_BUCKET" \
    AthenaResultsPrefix="$ATHENA_RESULTS_PREFIX" \
    OriginVerifySecret="$ORIGIN_SECRET" \
    CognitoDomainPrefix="$COGNITO_DOMAIN_PREFIX" \
    AlarmEmail="${ALARM_EMAIL:-}" \
    LogBucketKmsKeyArn="$KMS_KEY_ARN" \
    AllowedSignupDomains="${ALLOWED_SIGNUP_DOMAINS:-}"

bout() { aws cloudformation describe-stacks --stack-name "$BACKEND_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
USER_POOL_ID=$(bout UserPoolId)
CLIENT_ID=$(bout UserPoolClientId)
COGNITO_DOMAIN=$(bout CognitoDomain)
API_ENDPOINT=$(bout ApiEndpoint)
API_FUNCTION=$(bout ApiFunctionName)

# ---------------------------------------------------------------- lambda code
log "Uploading Lambda code ($API_FUNCTION)"
( cd lambda/api && zip -q -X -r /tmp/kiro-dashboard-api.zip handler.py queries.py )
aws lambda update-function-code --function-name "$API_FUNCTION" \
  --zip-file fileb:///tmp/kiro-dashboard-api.zip --publish >/dev/null
rm -f /tmp/kiro-dashboard-api.zip

if aws lambda get-function --function-name kiro-dashboard-presignup >/dev/null 2>&1; then
  log "Uploading presignup Lambda code"
  ( cd lambda/presignup && zip -q -X -r /tmp/kiro-dashboard-presignup.zip presignup.py )
  aws lambda update-function-code --function-name kiro-dashboard-presignup \
    --zip-file fileb:///tmp/kiro-dashboard-presignup.zip --publish >/dev/null
  rm -f /tmp/kiro-dashboard-presignup.zip
fi

log "Uploading scanner Lambda code"
( cd lambda/scanner && zip -q -X -r /tmp/kiro-dashboard-scanner.zip scanner.py )
aws lambda update-function-code --function-name kiro-dashboard-scanner \
  --zip-file fileb:///tmp/kiro-dashboard-scanner.zip --publish >/dev/null
rm -f /tmp/kiro-dashboard-scanner.zip

# Function lives in the optional identity-sync stack (02_identity_sync.yaml);
# skip when that stack isn't deployed.
if aws lambda get-function --function-name kiro-user-mapping-sync >/dev/null 2>&1; then
  log "Uploading user-mapping-sync Lambda code"
  ( cd lambda/user_mapping_sync && zip -q -X -r /tmp/kiro-user-mapping-sync.zip user_mapping_sync.py )
  aws lambda update-function-code --function-name kiro-user-mapping-sync \
    --zip-file fileb:///tmp/kiro-user-mapping-sync.zip --publish >/dev/null
  rm -f /tmp/kiro-user-mapping-sync.zip
fi

# ---------------------------------------------------------------- frontend stack
log "Deploying frontend stack ($FRONTEND_STACK)"
aws cloudformation deploy \
  --stack-name "$FRONTEND_STACK" \
  --template-file cloudformation/30_frontend.yaml \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    ApiEndpointDomain="$API_ENDPOINT" \
    OriginVerifySecret="$ORIGIN_SECRET"

fout() { aws cloudformation describe-stacks --stack-name "$FRONTEND_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
DIST_ID=$(fout DistributionId)
DIST_DOMAIN=$(fout DistributionDomain)
SITE_BUCKET=$(fout SiteBucketName)
SITE_URL="https://$DIST_DOMAIN"

# ---------------------------------------------------------------- cognito callbacks
log "Pointing Cognito client callbacks at $SITE_URL"
aws cognito-idp update-user-pool-client \
  --user-pool-id "$USER_POOL_ID" --client-id "$CLIENT_ID" \
  --callback-urls "$SITE_URL/" --logout-urls "$SITE_URL/" \
  --allowed-o-auth-flows code --allowed-o-auth-scopes openid email \
  --allowed-o-auth-flows-user-pool-client \
  --supported-identity-providers COGNITO \
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH ALLOW_USER_SRP_AUTH \
  --prevent-user-existence-errors ENABLED \
  --access-token-validity 60 --id-token-validity 60 --token-validity-units \
    AccessToken=minutes,IdToken=minutes,RefreshToken=hours \
  --refresh-token-validity 8 >/dev/null

# ---------------------------------------------------------------- admin user
if ! aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" \
      --username "$ADMIN_EMAIL" >/dev/null 2>&1; then
  log "Creating admin user $ADMIN_EMAIL (temporary password sent by email)"
  aws cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" \
    --username "$ADMIN_EMAIL" \
    --user-attributes Name=email,Value="$ADMIN_EMAIL" Name=email_verified,Value=true \
    --desired-delivery-mediums EMAIL >/dev/null
fi

# ---------------------------------------------------------------- admins group
if ! aws cognito-idp admin-list-groups-for-user --user-pool-id "$USER_POOL_ID" \
      --username "$ADMIN_EMAIL" --query "Groups[?GroupName=='admins']" --output text 2>/dev/null | grep -q admins; then
  log "Adding $ADMIN_EMAIL to the admins group"
  aws cognito-idp admin-add-user-to-group --user-pool-id "$USER_POOL_ID" \
    --username "$ADMIN_EMAIL" --group-name admins
fi

# ---------------------------------------------------------------- athena reconciliation
log "Reconciling Athena dependencies (user_project + v_user_activity_enriched)"
# The live account may use `user_mapping` (hand-built) or `user_identity`
# (repo-deployed); detect which exists.
IDENTITY_TABLE=user_identity
if aws glue get-table --database-name "$DATABASE" --name user_mapping >/dev/null 2>&1; then
  IDENTITY_TABLE=user_mapping
fi
echo "    identity table: $IDENTITY_TABLE"

run_athena() {
  local sql=$1
  local qid
  qid=$(aws athena start-query-execution --work-group "$WORKGROUP" \
    --query-execution-context Database="$DATABASE" \
    --query-string "$sql" --query QueryExecutionId --output text)
  while :; do
    local state
    state=$(aws athena get-query-execution --query-execution-id "$qid" \
      --query QueryExecution.Status.State --output text)
    case $state in
      SUCCEEDED) return 0 ;;
      FAILED|CANCELLED)
        aws athena get-query-execution --query-execution-id "$qid" \
          --query QueryExecution.Status.StateChangeReason --output text >&2
        return 1 ;;
      *) sleep 1 ;;
    esac
  done
}

# Substitute placeholders, strip comments, split into statements (one per line).
# v2 tables need the S3 layout parameters too.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PROMPT_PREFIX=${PROMPT_PREFIX:-kiro/prompt-log}
ACTIVITY_PREFIX=${ACTIVITY_PREFIX:-kiro/user-activity-metrics}
PROJECTION_START=${PROJECTION_START:-2025/01/01/00}
REGION=${REGION:-$AWS_REGION}
export DATABASE LOG_BUCKET MAPPING_PREFIX IDENTITY_TABLE \
       ACCOUNT_ID PROMPT_PREFIX ACTIVITY_PREFIX PROJECTION_START REGION
python3 - <<'PYEOF' > /tmp/kiro-enriched-statements.txt
import os
vars_ = ('DATABASE', 'LOG_BUCKET', 'MAPPING_PREFIX', 'IDENTITY_TABLE',
         'ACCOUNT_ID', 'PROMPT_PREFIX', 'ACTIVITY_PREFIX', 'PROJECTION_START', 'REGION')
for path in ('sql/10_enriched_dependencies.sql', 'sql/20_v2_tables.sql'):
    sql = open(path).read()
    for var in vars_:
        sql = sql.replace('${%s}' % var, os.environ[var])
    sql = '\n'.join(l for l in sql.splitlines() if not l.strip().startswith('--'))
    for stmt in sql.split(';'):
        stmt = ' '.join(stmt.split())          # collapse to a single line
        if stmt:
            print(stmt)
PYEOF
while IFS= read -r stmt; do
  echo "    athena: ${stmt:0:60}..."
  run_athena "$stmt"
done < /tmp/kiro-enriched-statements.txt

# Seed an empty user-project mapping if none exists (view works, all UNMAPPED).
if ! aws s3 ls "s3://$LOG_BUCKET/$MAPPING_PREFIX/user-project/" | grep -q csv; then
  log "Seeding empty user-project mapping (edit mappings/user-project.csv and re-upload)"
  printf 'userid,team,project,cost_center\n' > /tmp/user-project.csv
  aws s3 cp /tmp/user-project.csv "s3://$LOG_BUCKET/$MAPPING_PREFIX/user-project/user-project.csv" >/dev/null
fi

# ---------------------------------------------------------------- frontend upload
log "Generating frontend/config.js and uploading site to s3://$SITE_BUCKET"
cat > frontend/config.js <<EOF
/* Generated by deploy.sh $(date -u +%Y-%m-%dT%H:%M:%SZ) — do not edit. */
const CONFIG = {
  cognitoDomain: '$COGNITO_DOMAIN',
  clientId: '$CLIENT_ID',
  redirectUri: '$SITE_URL/',
  logoutUri: '$SITE_URL/',
};
EOF
aws s3 sync frontend/ "s3://$SITE_BUCKET/" \
  --exclude 'config.example.js' --exclude 'intro-audio/*' --delete --cache-control 'no-cache' >/dev/null
# narration MP3s live under intro-audio/ (generated by scripts/generate_narration.py)
# — excluded from --delete so a frontend sync never wipes them
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths '/*' >/dev/null

# ---------------------------------------------------------------- smoke test
log "Smoke test"
code=$(curl -s -o /dev/null -w '%{http_code}' "$SITE_URL/")
echo "    GET $SITE_URL/          -> $code (expect 200)"
api_direct=$(curl -s -o /dev/null -w '%{http_code}' "https://$API_ENDPOINT/api/overview")
echo "    direct execute-api call -> $api_direct (expect 401/403: blocked without CloudFront+JWT)"
api_cf=$(curl -s -o /dev/null -w '%{http_code}' "$SITE_URL/api/overview")
echo "    unauthd via CloudFront  -> $api_cf (expect 401: JWT required)"

log "Done. Dashboard: $SITE_URL  (sign in as $ADMIN_EMAIL)"
