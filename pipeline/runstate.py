"""Show a pipeline run: state, per-task state/duration, and the error if it failed.
    python pipeline/runstate.py <run-name> [--project P] [--region R] [--json]
(There is no gcloud command for pipeline jobs; this is the SDK equivalent.)"""
import argparse, json, subprocess, sys
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
    if st == "FAILED":
        # the generic "replica exited with status 1" hides the component's own message: pull it from the job's logs
        try:
            job_id = t.executor_detail.container_detail.main_job.split("/")[-1]
            log = subprocess.run(["gcloud", "logging", "read", f"resource.type=ml_job AND resource.labels.job_id={job_id}",
                                  "--project", a.project, "--limit", "300", "--format", "value(textPayload,jsonPayload.message)"], capture_output=True, text=True, timeout=90).stdout
            keep = [l.strip() for l in log.splitlines()
                    if (l.lstrip().startswith(("RuntimeError:", "AssertionError:", "ValueError:", "KeyError:")) or '"passed"' in l or "candidate_auc_pr" in l or "champion_auc_pr" in l)
                    and "raise " not in l and "stringValue" not in l]
            if keep:
                err = " | ".join(dict.fromkeys(reversed(keep[:8])))[:700]
        except Exception:
            pass
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
