#!/usr/bin/env bash
# Bring-up: Terraform (registry + SAs first) -> drift image -> Terraform (everything) -> first pipeline run (champion v1).
#   ./deploy.sh                 # full bring-up (~25 min: the first pipeline run trains, registers and deploys v1)
#   ./deploy.sh release [TAG]   # another release run (challenger -> gate -> canary -> promote)
#   ./deploy.sh bad [TAG]       # the bad-model drill: sabotaged training, the gate must fail the run
#   ./deploy.sh drift-image TAG # rebuild the drift job image and point the job at it
set -euo pipefail
cd "$(dirname "$0")"
PROJECT_ID="$(grep -E '^project_id' terraform/terraform.tfvars | cut -d'"' -f2)"
REGION="${REGION:-us-central1}"
PY="${PY:-$HOME/venvs/p7/bin/python}"
MODE="${1:-full}"
tf_out() { terraform -chdir=terraform output -raw "$1"; }

run_pipeline() {  # TAG EXTRA_ARGS...
  local TAG="$1"; shift
  "$PY" pipeline/submit.py --project "$PROJECT_ID" --region "$REGION" --bucket "$(tf_out bucket)" \
    --service-account "$(tf_out pipeline_service_account)" --tag "$TAG" "$@"
}

case "$MODE" in
  release) run_pipeline "${2:-rel-$(date -u +%m%d%H%M)}"; exit $? ;;
  bad)     run_pipeline "${2:-bad-$(date -u +%m%d%H%M)}" --sabotage; exit $? ;;
  drift-image)
    TAG="${2:-v$(date -u +%m%d%H%M)}"
    gcloud builds submit --project "$PROJECT_ID" --region "$REGION" --config cloudbuild.yaml --substitutions="_TAG=${TAG},_REGION=${REGION}" . --quiet
    gcloud run jobs update drift-check --project "$PROJECT_ID" --region "$REGION" --image "$(tf_out drift_image | sed "s/:.*/:${TAG}/")" --quiet
    exit 0 ;;
esac

echo "== 1/4 terraform: APIs, registry, service accounts, bucket"
terraform -chdir=terraform init -input=false >/dev/null
terraform -chdir=terraform apply -input=false -auto-approve \
  -target=google_project_service.apis -target=google_artifact_registry_repository.apps \
  -target=google_service_account.build -target=google_project_iam_member.build_roles -target=google_storage_bucket.mlops >/dev/null
echo "== 2/4 drift-check image v1"
gcloud builds submit --project "$PROJECT_ID" --region "$REGION" --config cloudbuild.yaml --substitutions="_TAG=v1,_REGION=${REGION}" . --quiet | tail -2
echo "== 3/4 terraform: endpoint, BigQuery log dataset, drift job + schedule, SLOs, dashboard, WIF"
terraform -chdir=terraform apply -input=false -auto-approve
echo "== 4/4 first release: champion v1 (extract -> train -> evaluate -> gate -> register -> deploy 100%)"
run_pipeline "v1-$(date -u +%m%d%H%M)"
./smoke.sh
echo
echo "pipelines:  $(tf_out pipelines_url)"
echo "registry:   $(tf_out registry_url)"
echo "dashboard:  $(tf_out dashboard_url)"
echo "next:       ./deploy.sh release ; ./deploy.sh bad ; drills/drift.sh ; drills/predict.sh"
