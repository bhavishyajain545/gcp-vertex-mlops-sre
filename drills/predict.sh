#!/usr/bin/env bash
# Live predictions against the endpoint: N random legitimate-looking rows + one fraud-like row from the public set.
#   drills/predict.sh [n=5] [skew=0]    skew>0 multiplies Amount and shifts V-features: the drift drill's traffic
set -uo pipefail
cd "$(dirname "$0")/.."
PROJECT_ID="$(grep -E '^project_id' terraform/terraform.tfvars | cut -d'"' -f2)"
REGION="${REGION:-us-central1}"
N="${1:-5}"; SKEW="${2:-0}"
EP="$(terraform -chdir=terraform output -raw endpoint_id)"
TOKEN="$(gcloud auth print-access-token)"
BODY="$(bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --format=json --max_rows="$N" \
  "SELECT V1,V2,V3,V4,V5,V6,V7,V8,V9,V10,V11,V12,V13,V14,V15,V16,V17,V18,V19,V20,V21,V22,V23,V24,V25,V26,V27,V28,Amount,Class
   FROM \`bigquery-public-data.ml_datasets.ulb_fraud_detection\` WHERE RAND() < 0.01 ORDER BY Class DESC, RAND() LIMIT $N" 2>/dev/null \
  | python3 -c "
import json,sys
rows=json.load(sys.stdin); skew=float('$SKEW')
inst=[]; labels=[]
for r in rows:
    v=[float(r[f'V{i}'])+ (skew*2.0 if i in (1,2,3,4) else 0.0) for i in range(1,29)]
    v.append(float(r['Amount'])*(1+skew*40))
    inst.append(v); labels.append(int(r['Class']))
print(json.dumps({'instances':inst,'_labels':labels}))")"
LABELS="$(echo "$BODY" | python3 -c 'import sys,json; print(json.load(sys.stdin)["_labels"])')"
REQ="$(echo "$BODY" | python3 -c 'import sys,json; d=json.load(sys.stdin); d.pop("_labels"); print(json.dumps(d))')"
t0=$(date +%s%N)
RESP="$(curl -s -X POST "https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/endpoints/${EP}:predict" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$REQ")"
ms=$(( ($(date +%s%N) - t0) / 1000000 ))
echo "$RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin); labels=$LABELS
print(f'deployed_model_id={d.get(\"deployedModelId\")}  model={d.get(\"model\",\"\").split(\"/\")[-1]} v{d.get(\"modelVersionId\")}  latency={$ms} ms')
for s,l in zip(d.get('predictions',[]), labels):
    print(f'  fraud score {float(s):.3f}  -> {\"FRAUD\" if float(s)>=0.5 else \"ok   \"}   (truth: {\"fraud\" if l else \"legit\"})')
" 2>/dev/null || echo "$RESP" | head -c 400
