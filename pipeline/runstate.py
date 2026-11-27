"""Show a pipeline run: state, per-task state/duration, and the error if it failed.
    python pipeline/runstate.py <run-name> [--project P] [--region R] [--json]
(There is no gcloud command for pipeline jobs; this is the SDK equivalent.)"""
import argparse, json, sys
from datetime import datetime, timezone

from google.cloud import aiplatform

ap = argparse.ArgumentParser()
ap.add_argument("name")
ap.add_argument("--project", default="bj-vertex-mlops")
ap.add_argument("--region", default="us-central1")
ap.add_argument("--json", action="store_true")
a = ap.parse_args()

aiplatform.init(project=a.project, location=a.region)
job = aiplatform.PipelineJob.get(a.name)
g = job.gca_resource
state = g.state.name.replace("PIPELINE_STATE_", "")
rows = []
for t in sorted(g.job_detail.task_details, key=lambda t: (t.start_time.timestamp() if t.start_time else 1e18)):
    if t.task_name == g.display_name or t.task_name == "":  # the root
        continue
    st = t.state.name.replace("State.", "")
    dur = ""
    if t.start_time and t.end_time:
        dur = f"{(t.end_time - t.start_time).total_seconds():.0f}s"
    elif t.start_time:
        dur = f"{(datetime.now(timezone.utc) - t.start_time).total_seconds():.0f}s…"
    err = t.error.message[:300] if t.error and t.error.message else ""
    rows.append({"task": t.task_name, "state": st, "duration": dur, "error": err})
out = {"run": g.display_name, "state": state, "tasks": rows}
if g.error and g.error.message:
    out["error"] = g.error.message[:600]
if a.json:
    print(json.dumps(out, indent=2))
else:
    print(f"{g.display_name}: {state}")
    for r in rows:
        print(f"  {r['task']:<28} {r['state']:<10} {r['duration']}")
        if r["error"]:
            print(f"      {r['error']}")
    if "error" in out:
        print("error:", out["error"])
sys.exit(0 if state == "SUCCEEDED" else (2 if state == "RUNNING" else 1))
