"""Compile and submit the release pipeline to Vertex AI Pipelines.

    python pipeline/submit.py --project P --region R --bucket B [--sabotage] [--no-wait] [--tag ci-abc123]

Prints the run's console URL; with --wait it follows the run and exits non-zero if it fails (the gate failing is a
failure by design: that is the bad-model drill).
"""
import argparse, os, sys, time
from pathlib import Path

from google.cloud import aiplatform
from kfp import compiler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import fraud_pipeline  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--project", required=True)
ap.add_argument("--region", default="us-central1")
ap.add_argument("--bucket", required=True, help="pipeline root bucket name (no gs://)")
ap.add_argument("--service-account", default=None)
ap.add_argument("--sabotage", action="store_true")
ap.add_argument("--no-promote", action="store_true")
ap.add_argument("--canary", type=int, default=10)
ap.add_argument("--sample-rows", type=int, default=60000)
ap.add_argument("--tag", default=os.environ.get("RUN_TAG", "manual"))
ap.add_argument("--no-wait", action="store_true")
a = ap.parse_args()

out = Path(__file__).resolve().parent / "pipeline.json"
compiler.Compiler().compile(fraud_pipeline, str(out))
aiplatform.init(project=a.project, location=a.region, staging_bucket=f"gs://{a.bucket}")
name = f"fraud-{'bad' if a.sabotage else 'rel'}-{a.tag}-{time.strftime('%H%M%S', time.gmtime())}".lower().replace("_", "-")[:120]
job = aiplatform.PipelineJob(
    display_name=name, job_id=name, template_path=str(out), pipeline_root=f"gs://{a.bucket}/pipeline-root",
    parameter_values={"project": a.project, "region": a.region, "bucket": a.bucket, "sabotage": a.sabotage,
                      "auto_promote": not a.no_promote, "canary_percent": a.canary, "sample_rows": a.sample_rows, "run_tag": a.tag},
    enable_caching=False,
)
job.submit(service_account=a.service_account)
print(f"run:     {name}")
print(f"console: https://console.cloud.google.com/vertex-ai/locations/{a.region}/pipelines/runs/{name}?project={a.project}")
if a.no_wait:
    sys.exit(0)
t0 = time.time()
job.wait()
state = job.state.name
print(f"state:   {state} after {time.time() - t0:.0f}s")
sys.exit(0 if state == "PIPELINE_STATE_SUCCEEDED" else 1)
