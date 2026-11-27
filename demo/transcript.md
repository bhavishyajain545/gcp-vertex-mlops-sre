# Demo transcript: demo

## 00:00  Architecture

This project treats a machine-learning model like a release. A fraud detector trained on the public credit-card data set: a Vertex AI pipeline extracts from BigQuery, trains, evaluates, and then an objective gate decides whether the candidate may exist at all; it must clear an absolute floor on A U C P R and it must not regress the current champion. If it passes, it becomes a version in the Model Registry and goes out as a canary: ten percent of one endpoint's traffic next to the champion, a smoke test through the endpoint, then promotion. After the release the model keeps being judged. Every prediction request is logged to BigQuery; a scheduled job computes the population stability index of live traffic against the champion's training baseline and rolls traffic back to the champion on its own when a challenger drifts. Prediction SLOs sit on the endpoint's own metrics. Everything is Terraform and the pipeline is submitted keylessly from CI.

## 00:59  Pipeline

The pipeline in the Vertex console. Six components, each a small Python container: extract, train, evaluate, the quality gate, register, and canary deploy with promotion. This run is a healthy release: the gate compared the candidate against the champion and let it through, the new version was deployed at ten percent beside the old one, smoke-tested through the endpoint, promoted to one hundred percent, and the old version undeployed. Each step's inputs, outputs and metrics are recorded as lineage.

## 01:29  Predictions

Live predictions through the endpoint: real rows from the public set, including fraud rows, scored by the serving version with the truth label next to each score. The response names the deployed model and the model version, so every prediction is attributable to a registry entry, and every request lands in the BigQuery log.

## 01:49  Release

The same run from the command line. The gate's verdict is explicit: the candidate's A U C P R, the champion's, and a pass. The promote step then writes champion dot json, which the next gate will read, and the baseline of feature distributions that the drift job will compare against. Nothing in this path is a human clicking approve; the numbers are the approval.

## 02:12  Registry and endpoint

The Model Registry holds every version under one parent model, with the evaluation metrics as labels and the aliases champion and challenger. The endpoint page shows what is actually serving: one endpoint, the deployed version, its traffic share and its replicas. A version in the registry is an artifact; a deployed model is a release. Keeping those apart is the point.

## 02:37  Bad model drill

The bad-model drill. I submit the same pipeline with sabotage switched on: a third of the labels shuffled and the three strongest features zeroed. Training still succeeds, evaluation still produces numbers, and the gate refuses it with the reason spelled out: the A U C P R collapsed below the floor and far below the champion. The run is red, the registry has no new version, the endpoint is untouched. That is a worse model that never reached production, caught by a rule, not by luck.

## 03:10  Drift drill

The drift drill. A challenger is serving ten percent of traffic. I send three hundred requests whose feature distribution is nothing like the training data, amounts forty times higher, the first four components shifted, and then run the drift job. It reads the last hour of logged requests, bins them with the champion's baseline, and the population stability index jumps far above the threshold on exactly those features. Because a challenger was serving, the job moves one hundred percent of traffic back to the champion and the alert fires for a human.

## 03:42  Dashboard and SLOs

Observability for a model, the SRE way. The dashboard shows predictions and errors per deployed model, p ninety-five model latency, replicas, and the PSI series the drift job writes. Two SLOs on the endpoint's own metrics: ninety-nine point five percent of predictions succeed and ninety-five percent finish under three hundred milliseconds, with burn-rate alerts. A model that is accurate but slow or erroring is still an incident.

## 04:10  Keyless CI

Delivery is keyless: a push to the pipeline folder compiles the definition and submits a release run as the pipeline service account through Workload Identity Federation. Run workflow with sabotage set to true ships the bad model on purpose, so the gate can be exercised from the same place as a real release.

## 04:28  Wrap-up

Everything shown is in the repository: the pipeline with its six components and the gate, the drift job with PSI and the rollback, Terraform for the endpoint, logging, SLOs, alerts, dashboard, identities and federation, the drills and the keyless workflow. The idle cost is the endpoint's replicas, and the teardown script removes it all. Thanks for watching.
