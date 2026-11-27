#!/usr/bin/env bash
# Drill: feature drift in production traffic -> PSI alert -> automated rollback to the champion.
#   drills/drift.sh           send 300 skewed requests, then run the drift job (AUTO_ROLLBACK=1) and show its report
#   drills/drift.sh report    only run the job and show the report
# Precondition for a visible rollback: a challenger is serving traffic next to the champion (./deploy.sh release
# with --no-promote, or run the drill right after a canary deploy).
set -uo pipefail
cd "$(dirname "$0")/.."
PROJECT_ID="$(grep -E '^project_id' terraform/terraform.tfvars | cut -d'"' -f2)"
REGION="${REGION:-us-central1}"
EP="$(terraform -chdir=terraform output -raw endpoint_id)"
if [ "${1:-}" != "report" ]; then
  echo "== $(date +%T) sending 300 skewed requests (Amount x40, V1-V4 shifted) through the endpoint"
  for i in $(seq 1 30); do drills/predict.sh 10 1 >/dev/null 2>&1 & [ $((i % 6)) -eq 0 ] && wait; done; wait
  echo "== $(date +%T) sent; the request/response log lands in BigQuery within ~1 min"
  sleep 70
fi
echo "== $(date +%T) traffic split before:"; gcloud ai endpoints describe "$EP" --project "$PROJECT_ID" --region "$REGION" --format='value(trafficSplit)'
echo "== running drift-check (PSI vs the champion's training baseline, AUTO_ROLLBACK=1)"
gcloud run jobs execute drift-check --project "$PROJECT_ID" --region "$REGION" --wait --quiet 2>&1 | tail -1
EXEC="$(gcloud run jobs executions list --job drift-check --project "$PROJECT_ID" --region "$REGION" --limit 1 --format='value(name)')"
gcloud logging read "resource.type=cloud_run_job AND labels.\"run.googleapis.com/execution_name\"=\"$EXEC\"" --project "$PROJECT_ID" --limit 60 --format='value(textPayload)' 2>/dev/null | tac | grep -v '^$'
echo "== $(date +%T) traffic split after:"; gcloud ai endpoints describe "$EP" --project "$PROJECT_ID" --region "$REGION" --format='value(trafficSplit)'
