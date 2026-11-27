#!/usr/bin/env bash
# What is deployed, what traffic it gets, and a few live predictions.
set -uo pipefail
cd "$(dirname "$0")"
PROJECT_ID="$(grep -E '^project_id' terraform/terraform.tfvars | cut -d'"' -f2)"
REGION="${REGION:-us-central1}"
EP="$(terraform -chdir=terraform output -raw endpoint_id)"
echo "== model registry"
M="$(gcloud ai models list --project "$PROJECT_ID" --region "$REGION" --filter='displayName=fraud-detector' --format='value(name.basename())' 2>/dev/null | head -1)"
[ -n "$M" ] && gcloud ai models list-version "$M" --project "$PROJECT_ID" --region "$REGION" --format='table(versionId,versionAliases.list(),labels.auc_pr,labels.recall_at_p90,versionCreateTime.date())' 2>/dev/null
echo "== endpoint $EP: deployed models + traffic split"
gcloud ai endpoints describe "$EP" --project "$PROJECT_ID" --region "$REGION" --format='yaml(deployedModels[].id,deployedModels[].displayName,deployedModels[].model,trafficSplit)' 2>/dev/null
echo "== champion.json"; gcloud storage cat "gs://$(terraform -chdir=terraform output -raw bucket)/champion.json" 2>/dev/null || echo "(none yet)"
echo "== predictions"; drills/predict.sh 5
