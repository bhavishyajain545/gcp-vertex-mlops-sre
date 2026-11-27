# Rebuild from zero

Terraform owns every cloud resource except the deployed models (the pipeline creates those); `deploy.sh` builds the
drift image, applies everything and runs the first release so a champion exists. ~25 minutes to a serving endpoint
(the first pipeline run alone is ~12 min: each component is a container that installs its packages).

## 0. Prerequisites

| Need | Notes |
|---|---|
| Billing-linked GCP account | A billing account allows 5 linked projects by default; unlink a finished one first. |
| `gcloud` >= 590, `terraform` >= 1.6, Python 3.11/3.12 | `python -m venv ~/venvs/p7 && pip install -r pipeline/requirements.txt` (the scripts use `$PY`, default `~/venvs/p7/bin/python`). |
| A GitHub repo at `github_repo` (tfvars) | CI auth is bound to exactly this `owner/repo` by the WIF attribute condition. |

Organisation constraints (domain-restricted sharing): override per project, then wait a minute before the first apply.

```bash
export PROJECT_ID=bj-vertex-mlops
gcloud services enable orgpolicy.googleapis.com --project "$PROJECT_ID"
cat > /tmp/drs.yaml <<EOF
name: projects/$PROJECT_ID/policies/iam.allowedPolicyMemberDomains
spec:
  rules:
  - allowAll: true
EOF
gcloud org-policies set-policy /tmp/drs.yaml --project "$PROJECT_ID" --billing-project "$PROJECT_ID"
```

## 1. Project

```bash
gcloud projects create "$PROJECT_ID" --name "Vertex AI MLOps SRE" --organization <ORG_ID>
gcloud billing projects link "$PROJECT_ID" --billing-account <BILLING_ACCOUNT_ID>
gcloud auth application-default set-quota-project "$PROJECT_ID"
```

## 2. Configure

```bash
git clone https://github.com/bhavishyajain545/gcp-vertex-mlops-sre && cd gcp-vertex-mlops-sre
$EDITOR terraform/terraform.tfvars          # project_id, github_repo, alert_email
```

If you change `project_id`, also change it in `.github/workflows/release.yml` (env block: PROJECT_ID, BUCKET).

## 3. Bring-up

```bash
./deploy.sh     # 1 terraform (APIs, registry, SAs, bucket)  2 drift image  3 terraform (everything)  4 first release run -> champion v1
```

## 4. CI secret (once)

```bash
terraform -chdir=terraform output -raw workload_identity_provider   # -> repo secret GCP_WORKLOAD_IDENTITY_PROVIDER
```

## 5. Drills

```bash
./deploy.sh release                 # challenger -> gate -> canary 10% -> smoke -> promote
./deploy.sh bad                     # sabotaged model: the gate fails the run
./deploy.sh release --no-promote    # leave a challenger at 10% for the drift drill
drills/drift.sh                     # 300 skewed requests -> PSI -> automated rollback to the champion
drills/predict.sh 5
```

## 6. Demo

`demo/scenes.py` drives a signed-in Chrome (`--remote-debugging-port=9222`) and WSL; narration via Google Cloud TTS;
`python -u demo/scenes.py` writes `demo/demo.mp4`, captions and transcript. Every command is captured before its
scene and replayed over the narration.

## 7. Tear down (stop all cost)

```bash
./teardown.sh   # undeploy + delete models -> terraform destroy -> endpoint/bucket leftovers -> audit
```

Rebuilding later: `./reimport.sh` first (the WIF pool/provider survive soft-deleted and 409 on create).

## Gotchas met while building

- The prebuilt sklearn prediction container calls `predict()`, which returns hard classes for a classifier. Training a
  gradient-boosting **regressor** on the 0/1 label gives a score in [0,1] with the same container and no custom
  prediction routine.
- A KFP component that returns a value *and* writes artifacts has its return under `task.outputs["Output"]`;
  `task.output` raises "multiple outputs".
- The gate reads the champion's metrics from GCS (`champion.json`), not from the registry: labels cannot hold dots and
  the registry has no notion of "current champion" beyond an alias. The promote step writes that file atomically
  with the alias change.
- Deployed models are not Terraform resources on purpose: the pipeline deploys and undeploys them. `teardown.sh`
  undeploys everything before `terraform destroy`, or the endpoint cannot be deleted.
- Request/response logging needs the Vertex service agent to have `bigquery.dataEditor` on the dataset; the table is
  created on the first logged request, so the drift job treats a missing table as "no traffic yet".
