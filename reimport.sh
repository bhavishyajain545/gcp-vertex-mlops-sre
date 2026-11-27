#!/usr/bin/env bash
# Rebuilding after ./teardown.sh: the WIF pool/provider survive soft-deleted and 409 on create. Undelete + import.
set -uo pipefail
cd "$(dirname "$0")"
PROJECT_ID="$(grep -E '^project_id' terraform/terraform.tfvars | cut -d'"' -f2)"
gcloud iam workload-identity-pools undelete github --location=global --project "$PROJECT_ID" --quiet 2>&1 | tail -1 || true
gcloud iam workload-identity-pools providers undelete github-oidc --workload-identity-pool=github --location=global --project "$PROJECT_ID" --quiet 2>&1 | tail -1 || true
terraform -chdir=terraform init -input=false >/dev/null
imp() { terraform -chdir=terraform state show "$1" >/dev/null 2>&1 && { echo "   $1 already in state"; return; }
        terraform -chdir=terraform import -input=false "$1" "$2" >/dev/null 2>&1 && echo "   imported $1" || echo "   (skip) $1"; }
imp google_iam_workload_identity_pool.github "projects/${PROJECT_ID}/locations/global/workloadIdentityPools/github"
imp google_iam_workload_identity_pool_provider.github "projects/${PROJECT_ID}/locations/global/workloadIdentityPools/github/providers/github-oidc"
echo "done; now ./deploy.sh"
