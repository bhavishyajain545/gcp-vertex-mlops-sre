#!/usr/bin/env bash
# Full teardown so nothing bills afterwards. The endpoint's deployed models (n1-standard-2 replicas) are the idle cost.
#   ./teardown.sh   # undeploy models -> terraform destroy -> leftovers -> audit
set -uo pipefail
cd "$(dirname "$0")"
PROJECT_ID="$(grep -E '^project_id' terraform/terraform.tfvars | cut -d'"' -f2)"
REGION="${REGION:-us-central1}"
g() { gcloud "$@" --project "$PROJECT_ID" --quiet; }

echo "== 1/4 undeploy every model from every endpoint, delete models, pipeline runs' leftovers"
for EP in $(g ai endpoints list --region "$REGION" --format='value(name.basename())' 2>/dev/null); do
  for DM in $(g ai endpoints describe "$EP" --region "$REGION" --format='value(deployedModels[].id)' 2>/dev/null | tr ';' ' '); do
    echo "  undeploy $DM from $EP"; g ai endpoints undeploy-model "$EP" --region "$REGION" --deployed-model-id "$DM" || true
  done
done
for M in $(g ai models list --region "$REGION" --format='value(name.basename())' 2>/dev/null | sort -u); do echo "  delete model $M"; g ai models delete "$M" --region "$REGION" || true; done

echo "== 2/4 terraform destroy"
terraform -chdir=terraform init -input=false >/dev/null 2>&1 || true
terraform -chdir=terraform destroy -input=false -auto-approve 2>&1 | grep -v 'Still destroying' | tail -6

echo "== 3/4 leftovers"
for EP in $(g ai endpoints list --region "$REGION" --format='value(name.basename())' 2>/dev/null); do echo "  delete endpoint $EP"; g ai endpoints delete "$EP" --region "$REGION" || true; done
for b in $(gcloud storage ls --project "$PROJECT_ID" 2>/dev/null); do echo "  delete bucket $b"; gcloud storage rm -r "$b" --project "$PROJECT_ID" --quiet || true; done
terraform -chdir=terraform destroy -input=false -auto-approve 2>&1 | tail -2

echo "== 4/4 what is left in $PROJECT_ID"
./audit-billing.sh "$PROJECT_ID"
echo "vertex:"; g ai endpoints list --region "$REGION" --format='value(name)' 2>/dev/null; g ai models list --region "$REGION" --format='value(name)' 2>/dev/null
echo "done. The WIF pool is soft-deleted for 30 days; an empty project costs nothing. Rebuild: ./reimport.sh then ./deploy.sh."
