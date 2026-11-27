"""fraud-detector: a Vertex AI Pipeline that treats a model like a release.

extract (BigQuery) -> train -> evaluate -> GATE -> register (Model Registry) -> canary deploy (traffic split) -> promote

The gate is the point: a candidate only reaches the registry if it beats an absolute floor AND the current
champion on AUC-PR. The deploy is a canary: the challenger takes `canary_percent` of the endpoint's traffic next
to the champion, a smoke test runs against the endpoint, and only then is traffic moved and the old version
undeployed. `sabotage=True` trains a deliberately bad model (shuffled labels, strongest features dropped) so the
gate can be watched failing.

Every component is a lightweight Python component; train/evaluate run on python:3.10 with numpy 1.26 and
scikit-learn 1.5.2, the same generation as the prebuilt Vertex prediction container that serves the model, so the
pickle loads there.
"""
from kfp import dsl
from kfp.dsl import Dataset, Input, Metrics, Model, Output

SKLEARN = "scikit-learn==1.5.2"
NUMPY = "numpy==1.26.4"  # the prebuilt sklearn-cpu.1-5 container runs Python 3.10 + numpy 1.x; a numpy-2 pickle (numpy._core) will not load there
TRAIN_IMAGE = "python:3.10-slim"
SERVING_IMAGE = "us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-5:latest"
FEATURES = [f"V{i}" for i in range(1, 29)] + ["Amount"]


@dsl.component(base_image="python:3.11-slim", packages_to_install=["google-cloud-bigquery[pandas]==3.27.0", "db-dtypes==1.3.1", "pyarrow==18.1.0", "pandas==2.2.3"])
def extract(project: str, sample_rows: int, train: Output[Dataset], eval: Output[Dataset]) -> dict:
    """Stratified sample of the public ULB credit-card fraud set: every fraud row plus a slice of the rest, split 80/20."""
    import pandas as pd
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    sql = f"""
    WITH base AS (
      SELECT * EXCEPT(Time), FARM_FINGERPRINT(CAST(Time AS STRING) || CAST(Amount AS STRING) || CAST(V1 AS STRING)) AS h
      FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`
    )
    SELECT * EXCEPT(h), MOD(ABS(h), 100) AS bucket FROM base
    WHERE Class = 1 OR MOD(ABS(h), 1000) < {max(1, int(sample_rows / 284))}
    """
    df = client.query(sql).to_dataframe()
    df["Class"] = df["Class"].astype(int)
    ev = df[df["bucket"] < 20].drop(columns=["bucket"])
    tr = df[df["bucket"] >= 20].drop(columns=["bucket"])
    tr.to_parquet(train.path + ".parquet", index=False)
    ev.to_parquet(eval.path + ".parquet", index=False)
    train.metadata["rows"] = int(len(tr)); eval.metadata["rows"] = int(len(ev))
    return {"train_rows": int(len(tr)), "eval_rows": int(len(ev)), "train_frauds": int(tr["Class"].sum()), "eval_frauds": int(ev["Class"].sum())}


@dsl.component(base_image=TRAIN_IMAGE, packages_to_install=[NUMPY, SKLEARN, "pandas==2.2.3", "pyarrow==18.1.0", "joblib==1.4.2"])
def train(train: Input[Dataset], sabotage: bool, model: Output[Model]) -> dict:
    """HistGradientBoosting on the fraud label, used as a scorer (predict -> fraud score in [0,1]) so the prebuilt
    sklearn container's predict() returns a probability-like score instead of a hard class."""
    import json, os, time
    import joblib
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingRegressor

    features = [f"V{i}" for i in range(1, 29)] + ["Amount"]
    df = pd.read_parquet(train.path + ".parquet")
    X, y = df[features].to_numpy(dtype="float64"), df["Class"].to_numpy(dtype="float64")
    note = "healthy"
    if sabotage:
        # the "bad release": a third of the labels shuffled and the three strongest features zeroed
        rng = np.random.default_rng(7)
        idx = rng.choice(len(y), size=len(y) // 3, replace=False)
        y[idx] = rng.permutation(y[idx])
        for f in ("V14", "V17", "V12"):
            X[:, features.index(f)] = 0.0
        note = "SABOTAGED: labels shuffled, V14/V17/V12 zeroed"
    t0 = time.time()
    est = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, max_leaf_nodes=31, l2_regularization=0.5, random_state=42)
    est.fit(X, y)
    os.makedirs(model.path, exist_ok=True)
    joblib.dump(est, os.path.join(model.path, "model.joblib"))  # the file name the prebuilt container expects
    with open(os.path.join(model.path, "features.json"), "w") as f:
        json.dump(features, f)
    model.metadata.update({"framework": "scikit-learn", "estimator": "HistGradientBoostingRegressor", "note": note, "train_seconds": round(time.time() - t0, 1)})
    return {"note": note, "train_seconds": round(time.time() - t0, 1), "rows": int(len(y))}


@dsl.component(base_image=TRAIN_IMAGE, packages_to_install=[NUMPY, SKLEARN, "pandas==2.2.3", "pyarrow==18.1.0", "joblib==1.4.2"])
def evaluate(model: Input[Model], eval: Input[Dataset], metrics: Output[Metrics], baseline: Output[Dataset]) -> dict:
    """AUC-PR / ROC-AUC / recall at 90% precision on the held-out split, plus the feature baseline (quantile bins)
    that the drift job compares live traffic against."""
    import json, os
    import joblib
    import numpy as np
    import pandas as pd
    from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

    features = json.load(open(os.path.join(model.path, "features.json")))
    est = joblib.load(os.path.join(model.path, "model.joblib"))
    df = pd.read_parquet(eval.path + ".parquet")
    X, y = df[features].to_numpy(dtype="float64"), df["Class"].to_numpy(dtype=int)
    score = np.clip(est.predict(X), 0, 1)
    auc_pr = float(average_precision_score(y, score))
    roc = float(roc_auc_score(y, score))
    p, r, _ = precision_recall_curve(y, score)
    recall_at_p90 = float(max([rr for pp, rr in zip(p, r) if pp >= 0.90] or [0.0]))
    out = {"auc_pr": round(auc_pr, 4), "roc_auc": round(roc, 4), "recall_at_p90": round(recall_at_p90, 4), "eval_rows": int(len(y)), "eval_frauds": int(y.sum())}
    for k, v in out.items():
        metrics.log_metric(k, v)
    # drift baseline: 10 quantile bins per feature from the evaluation split (same distribution as training)
    base = {}
    for i, f in enumerate(features):
        col = X[:, i]
        edges = np.unique(np.quantile(col, np.linspace(0, 1, 11)))
        counts, _ = np.histogram(col, bins=edges)
        base[f] = {"edges": [float(e) for e in edges], "frac": [float(c) / max(1, len(col)) for c in counts]}
    with open(baseline.path, "w") as fh:
        json.dump({"features": features, "bins": base, "rows": int(len(y))}, fh)
    return out


@dsl.component(base_image="python:3.11-slim", packages_to_install=["google-cloud-storage==2.19.0"])
def gate(metrics_in: dict, bucket: str, floor_auc_pr: float, max_regression: float) -> dict:
    """The release gate: fail the pipeline unless the candidate clears the floor and does not regress the champion.
    The champion's numbers live at gs://<bucket>/champion.json (written by promote)."""
    import json
    from google.cloud import storage

    blob = storage.Client().bucket(bucket).blob("champion.json")
    champion = json.loads(blob.download_as_text()) if blob.exists() else None
    cand = float(metrics_in["auc_pr"])
    reasons = []
    if cand < floor_auc_pr:
        reasons.append(f"auc_pr {cand:.4f} < floor {floor_auc_pr}")
    if champion and cand < float(champion["auc_pr"]) - max_regression:
        reasons.append(f"auc_pr {cand:.4f} regresses champion {champion['auc_pr']:.4f} by more than {max_regression}")
    verdict = {"candidate_auc_pr": cand, "champion_auc_pr": champion["auc_pr"] if champion else None, "champion_version": champion.get("version") if champion else None, "passed": not reasons, "reasons": reasons}
    print(json.dumps(verdict, indent=2))
    if reasons:
        raise RuntimeError("GATE FAILED: " + "; ".join(reasons))
    return verdict


@dsl.component(base_image="python:3.11-slim", packages_to_install=["google-cloud-aiplatform==1.75.0"])
def register(project: str, region: str, model: Input[Model], metrics_in: dict, model_display_name: str, serving_image: str, run_tag: str) -> dict:
    """Model Registry: a new version under one parent model, the metrics as labels, alias 'challenger'."""
    from google.cloud import aiplatform

    aiplatform.init(project=project, location=region)
    parents = aiplatform.Model.list(filter=f'display_name="{model_display_name}"')
    parent = parents[0].resource_name if parents else None
    labels = {k: str(v).replace(".", "_")[:60] for k, v in metrics_in.items() if k in ("auc_pr", "roc_auc", "recall_at_p90")}
    labels["run"] = run_tag[:60]
    m = aiplatform.Model.upload(
        display_name=model_display_name,
        parent_model=parent,
        artifact_uri=model.uri,
        serving_container_image_uri=serving_image,
        labels=labels,
        version_aliases=["challenger"],
        version_description=f"auc_pr={metrics_in['auc_pr']} roc_auc={metrics_in['roc_auc']} recall@p90={metrics_in['recall_at_p90']} ({run_tag})",
        is_default_version=False,
        sync=True,
    )
    return {"model": m.resource_name, "version_id": m.version_id, "versioned_resource_name": m.versioned_resource_name}


@dsl.component(base_image="python:3.11-slim", packages_to_install=["google-cloud-aiplatform==1.75.0", "google-cloud-storage==2.19.0"])
def canary_deploy_and_promote(project: str, region: str, endpoint_display_name: str, registered: dict, metrics_in: dict,
                              baseline: Input[Dataset], bucket: str, canary_percent: int, machine_type: str,
                              auto_promote: bool, smoke_requests: int) -> dict:
    """Deploy the challenger next to the champion with `canary_percent` of traffic, smoke-test the endpoint, then
    move 100% to the challenger, undeploy the previous version, make it the champion and publish its baseline."""
    import json, statistics, time
    from google.cloud import aiplatform, storage

    aiplatform.init(project=project, location=region)
    ep = aiplatform.Endpoint.list(filter=f'display_name="{endpoint_display_name}"')[0]
    model = aiplatform.Model(registered["versioned_resource_name"])
    before = {dm.id: dm.model_version_id for dm in ep.list_models()}
    traffic = ep.traffic_split or {}
    print("deployed before:", before, "traffic:", traffic)

    ep.deploy(model=model, deployed_model_display_name=f"v{model.version_id}", machine_type=machine_type,
              min_replica_count=1, max_replica_count=2, traffic_percentage=canary_percent if before else 100, sync=True)
    ep = aiplatform.Endpoint(ep.resource_name)
    new_id = [dm.id for dm in ep.list_models() if dm.model_version_id == model.version_id][0]
    print("deployed canary:", new_id, "traffic:", ep.traffic_split)

    # smoke: the endpoint must answer, fast, with scores in range
    features = json.load(open(baseline.path))["features"]
    inst = [[0.0] * (len(features) - 1) + [50.0]] * 5
    lat = []
    for _ in range(max(1, smoke_requests // 5)):
        t0 = time.time()
        pred = ep.predict(instances=inst)
        lat.append((time.time() - t0) * 1000)
        assert len(pred.predictions) == 5 and all(-0.5 <= float(p) <= 1.5 for p in pred.predictions), pred.predictions
    smoke = {"requests": len(lat) * 5, "p50_ms": round(statistics.median(lat), 1), "max_ms": round(max(lat), 1)}
    print("smoke:", smoke)

    result = {"deployed_model_id": new_id, "version_id": model.version_id, "smoke": smoke, "promoted": False, "traffic": dict(ep.traffic_split)}
    if auto_promote:
        ep.update(traffic_split={new_id: 100})
        for dm_id in before:
            if dm_id != new_id:
                ep.undeploy(deployed_model_id=dm_id, sync=True)
        # aliases: the new one becomes champion
        reg = aiplatform.ModelRegistry(model=registered["model"])
        try:
            reg.remove_version_aliases(target_aliases=["challenger"], version=model.version_id)
        except Exception as exc:  # alias bookkeeping must not fail a promotion
            print("alias cleanup:", exc)
        reg.add_version_aliases(new_aliases=["champion"], version=model.version_id)
        champ = {"version": model.version_id, "deployed_model_id": new_id, "auc_pr": metrics_in["auc_pr"], "roc_auc": metrics_in["roc_auc"], "recall_at_p90": metrics_in["recall_at_p90"], "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        b = storage.Client().bucket(bucket)
        b.blob("champion.json").upload_from_string(json.dumps(champ, indent=2), content_type="application/json")
        b.blob("baseline.json").upload_from_filename(baseline.path, content_type="application/json")
        result.update({"promoted": True, "traffic": {new_id: 100}, "champion": champ})
    print(json.dumps(result, indent=2))
    return result


@dsl.pipeline(name="fraud-detector-release", description="train -> evaluate -> gate -> register -> canary -> promote")
def fraud_pipeline(project: str, region: str, bucket: str, endpoint_display_name: str = "fraud-endpoint",
                   model_display_name: str = "fraud-detector", sample_rows: int = 60000, sabotage: bool = False,
                   floor_auc_pr: float = 0.70, max_regression: float = 0.01, canary_percent: int = 10,
                   auto_promote: bool = True, machine_type: str = "n1-standard-2", smoke_requests: int = 25,
                   run_tag: str = "manual"):
    ex = extract(project=project, sample_rows=sample_rows).set_display_name("extract from BigQuery")
    tr = train(train=ex.outputs["train"], sabotage=sabotage).set_display_name("train")
    ev = evaluate(model=tr.outputs["model"], eval=ex.outputs["eval"]).set_display_name("evaluate")
    g = gate(metrics_in=ev.outputs["Output"], bucket=bucket, floor_auc_pr=floor_auc_pr, max_regression=max_regression).set_display_name("quality gate")
    rg = register(project=project, region=region, model=tr.outputs["model"], metrics_in=ev.outputs["Output"], model_display_name=model_display_name,
                  serving_image=SERVING_IMAGE, run_tag=run_tag).set_display_name("register version").after(g)
    canary_deploy_and_promote(project=project, region=region, endpoint_display_name=endpoint_display_name, registered=rg.output,
                              metrics_in=ev.outputs["Output"], baseline=ev.outputs["baseline"], bucket=bucket, canary_percent=canary_percent,
                              machine_type=machine_type, auto_promote=auto_promote, smoke_requests=smoke_requests).set_display_name("canary deploy + promote")


if __name__ == "__main__":
    from kfp import compiler
    compiler.Compiler().compile(fraud_pipeline, "pipeline.json")
    print("compiled pipeline.json")
