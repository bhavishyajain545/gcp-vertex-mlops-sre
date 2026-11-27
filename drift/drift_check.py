"""Drift check: compare the last window of live prediction requests (Vertex request-response logging in BigQuery)
with the champion's training baseline, feature by feature, using the Population Stability Index.

    PSI = sum over bins of (live_frac - base_frac) * ln(live_frac / base_frac)
    < 0.10 stable  ·  0.10-0.25 watch  ·  > 0.25 drifted

Writes custom metrics (ml/psi_max, ml/psi_feature{feature}) to Cloud Monitoring, and with AUTO_ROLLBACK=1 moves
the endpoint's traffic back to the champion when psi_max exceeds the threshold while a challenger is serving.
Runs as a Cloud Run job (scheduled every 15 min) and by hand from drills/drift.sh.
"""
import json, math, os, sys, time

from google.api import label_pb2, metric_pb2
from google.cloud import aiplatform, bigquery, monitoring_v3, storage

PROJECT = os.environ["PROJECT_ID"]
REGION = os.environ.get("REGION", "us-central1")
BUCKET = os.environ["BUCKET"]
LOG_TABLE = os.environ["LOG_TABLE"]            # project.dataset.table written by endpoint request-response logging
ENDPOINT_NAME = os.environ.get("ENDPOINT_DISPLAY_NAME", "fraud-endpoint")
WINDOW_MIN = int(os.environ.get("WINDOW_MINUTES", "60"))
THRESHOLD = float(os.environ.get("PSI_THRESHOLD", "0.25"))
AUTO_ROLLBACK = os.environ.get("AUTO_ROLLBACK", "0") == "1"
MIN_ROWS = int(os.environ.get("MIN_ROWS", "100"))


def psi(base_frac, live_frac, eps=1e-4):
    return sum((l - b) * math.log((l + eps) / (b + eps)) for b, l in zip(base_frac, live_frac))


def live_values(features):
    sql = f"""
    SELECT request_payload FROM `{LOG_TABLE}`
    WHERE logging_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {WINDOW_MIN} MINUTE)
    """
    cols = {f: [] for f in features}
    n = 0
    for row in bigquery.Client(project=PROJECT).query(sql).result():
        for inst in row["request_payload"] or []:
            try:
                vals = json.loads(inst)
            except Exception:
                continue
            if isinstance(vals, dict):
                vals = [vals.get(f) for f in features]
            if not isinstance(vals, list) or len(vals) != len(features):
                continue
            n += 1
            for f, v in zip(features, vals):
                cols[f].append(float(v))
    return n, cols


def write_metrics(psi_max, per_feature, rows):
    client = monitoring_v3.MetricServiceClient()
    name = f"projects/{PROJECT}"
    now = time.time()
    interval = monitoring_v3.TimeInterval({"end_time": {"seconds": int(now)}})
    series = []
    def ts(metric_type, value, labels):
        s = monitoring_v3.TimeSeries()
        s.metric.type = metric_type
        for k, v in labels.items():
            s.metric.labels[k] = v
        s.resource.type = "global"
        s.resource.labels["project_id"] = PROJECT
        s.points = [monitoring_v3.Point({"interval": interval, "value": {"double_value": float(value)}})]
        return s
    series.append(ts("custom.googleapis.com/ml/psi_max", psi_max, {"endpoint": ENDPOINT_NAME}))
    series.append(ts("custom.googleapis.com/ml/logged_requests", rows, {"endpoint": ENDPOINT_NAME}))
    for f, v in sorted(per_feature.items(), key=lambda kv: -kv[1])[:10]:
        series.append(ts("custom.googleapis.com/ml/psi_feature", v, {"endpoint": ENDPOINT_NAME, "feature": f}))
    client.create_time_series(name=name, time_series=series)


def rollback_if_needed(psi_max):
    aiplatform.init(project=PROJECT, location=REGION)
    ep = aiplatform.Endpoint.list(filter=f'display_name="{ENDPOINT_NAME}"')[0]
    champ = json.loads(storage.Client().bucket(BUCKET).blob("champion.json").download_as_text())
    split = dict(ep.traffic_split or {})
    champion_id = champ.get("deployed_model_id")
    serving = [dm.id for dm in ep.list_models()]
    print(f"traffic now: {split}; champion deployed_model_id={champion_id}; serving={serving}")
    if psi_max <= THRESHOLD:
        return {"action": "none", "reason": f"psi_max {psi_max:.3f} <= {THRESHOLD}"}
    if champion_id not in serving:
        return {"action": "alert-only", "reason": "champion is not deployed on the endpoint; nothing to roll back to"}
    if split.get(champion_id, 0) == 100:
        return {"action": "none", "reason": "champion already has 100% of traffic"}
    if not AUTO_ROLLBACK:
        return {"action": "would-rollback", "reason": f"psi_max {psi_max:.3f} > {THRESHOLD}; AUTO_ROLLBACK is off"}
    ep.update(traffic_split={champion_id: 100})
    return {"action": "ROLLED BACK", "reason": f"psi_max {psi_max:.3f} > {THRESHOLD}", "traffic": {champion_id: 100}}


def main():
    base = json.loads(storage.Client().bucket(BUCKET).blob("baseline.json").download_as_text())
    features = base["features"]
    rows, cols = live_values(features)
    report = {"window_minutes": WINDOW_MIN, "logged_requests": rows, "threshold": THRESHOLD}
    if rows < MIN_ROWS:
        report.update({"psi_max": 0.0, "note": f"only {rows} logged requests in the window (< {MIN_ROWS}); nothing to judge"})
        write_metrics(0.0, {}, rows)
        print(json.dumps(report, indent=2))
        return 0
    per_feature = {}
    for f in features:
        edges = base["bins"][f]["edges"]
        if len(edges) < 2:
            continue
        vals = cols[f]
        counts = [0] * (len(edges) - 1)
        for v in vals:
            i = 0
            while i < len(edges) - 2 and v >= edges[i + 1]:
                i += 1
            counts[i] += 1
        live = [c / len(vals) for c in counts]
        per_feature[f] = round(psi(base["bins"][f]["frac"], live), 4)
    psi_max = max(per_feature.values())
    top = sorted(per_feature.items(), key=lambda kv: -kv[1])[:5]
    report.update({"psi_max": round(psi_max, 4), "top_features": top,
                   "verdict": "DRIFTED" if psi_max > THRESHOLD else ("watch" if psi_max > 0.10 else "stable")})
    write_metrics(psi_max, per_feature, rows)
    report["rollback"] = rollback_if_needed(psi_max)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
