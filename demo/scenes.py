"""Demo video for Project 7: Vertex AI MLOps with SRE discipline.

Prerequisites:
  * platform deployed (`./deploy.sh`), champion v1 serving, drills verified by hand
  * Chrome started with --remote-debugging-port=9222 and signed in to the Cloud console
  * DEMO_EXTRA_PATH with gcloud and ffmpeg; WSL venv ~/venvs/p7; optional DEMO_CI_URL (a finished Actions run)

  python -u demo/scenes.py

Rule: nothing slow runs inside a scene. The release run and the bad-model run are submitted at the start and
finish in the background (~12 min each); drills run between scenes; every scene only types and pastes captured
output over the narration. Waiting between scenes is cut by the recorder.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

from recorder import Demo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = "bj-vertex-mlops"
REGION = "us-central1"
CDP_URL = os.environ.get("DEMO_CDP_URL", "http://127.0.0.1:9222")
CI_URL = os.environ.get("DEMO_CI_URL", "")

for p in os.environ.get("DEMO_EXTRA_PATH", "").split(os.pathsep):
    if p:
        os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]
os.chdir(ROOT)


def wsl(cmd: str, timeout: int = 300) -> str:
    root = "/mnt/" + ROOT.drive[0].lower() + ROOT.as_posix()[2:]
    full = f"source ~/.gcp-env.sh; cd {root}; {cmd}"
    try:
        r = subprocess.run(["wsl", "-e", "bash", "-c", full], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return "(timed out)"


def cdp_available() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(CDP_URL + "/json/version", timeout=2):
            return True
    except Exception:
        return False


async def type_cmd(page, cmd: str, type_delay: int = 7, chunk: int = 4) -> None:
    await page.evaluate("(c) => window.__prompt(c)", "")
    for i in range(0, len(cmd), chunk):
        await page.evaluate("(c) => window.__type(c)", cmd[i:i + chunk])
        await asyncio.sleep(type_delay * chunk / 1000)


def capture_steps(steps) -> list[tuple[str, str]]:
    return [(cmd, (producer() if callable(producer) else producer)) for cmd, producer in steps]


async def precapture(steps) -> list[tuple[str, str]]:
    return await asyncio.to_thread(capture_steps, steps)


async def replay(page, steps, spread: float, type_delay: int = 5, hold: float = 2.0) -> None:
    n = max(len(steps), 1)
    pause = max(0.4, min(3.0, spread / n / 2))
    for cmd, out in steps:
        await type_cmd(page, cmd, type_delay)
        await asyncio.sleep(pause)
        await page.evaluate("(o) => window.__output(o)", out)
        await asyncio.sleep(pause)
    await asyncio.sleep(hold)


async def console_ready(tab, text: str, reload: bool = True) -> None:
    try:
        if reload:
            await tab.reload(wait_until="commit")
        await tab.wait_for_selector(f"text={text}", timeout=60000)
        await asyncio.sleep(2.5)
    except Exception as exc:
        print(f"  ! console tab not ready ({text}): {type(exc).__name__}")


def trim(s: str, n: int = 1800) -> str:
    return s if len(s) <= n else s[:n] + "\n…"


def gcloud(args: str, timeout: int = 120) -> str:
    return wsl(f"gcloud {args} --project {PROJECT} 2>&1", timeout=timeout)


def run_state(name: str) -> str:
    return gcloud(f"ai pipeline-jobs describe {name} --region {REGION} --format='value(state)'")


def task_details(name: str) -> str:
    """One line per pipeline task: name, state, duration."""
    return gcloud(f"ai pipeline-jobs describe {name} --region {REGION} --format='table[no-heading](jobDetail.taskDetails[].taskName,jobDetail.taskDetails[].state)' ")


NARRATION = {
    "arch": (
        "This project treats a machine-learning model like a release. A fraud detector trained on the public credit-card data set: a Vertex AI pipeline extracts from BigQuery, trains, "
        "evaluates, and then an objective gate decides whether the candidate may exist at all; it must clear an absolute floor on A U C P R and it must not regress the current champion. "
        "If it passes, it becomes a version in the Model Registry and goes out as a canary: ten percent of one endpoint's traffic next to the champion, a smoke test through the endpoint, then promotion. "
        "After the release the model keeps being judged. Every prediction request is logged to BigQuery; a scheduled job computes the population stability index of live traffic against the "
        "champion's training baseline and rolls traffic back to the champion on its own when a challenger drifts. Prediction SLOs sit on the endpoint's own metrics. Everything is Terraform and the pipeline is submitted keylessly from CI."
    ),
    "pipeline": (
        "The pipeline in the Vertex console. Six components, each a small Python container: extract, train, evaluate, the quality gate, register, and canary deploy with promotion. "
        "This run is a healthy release: the gate compared the candidate against the champion and let it through, the new version was deployed at ten percent beside the old one, "
        "smoke-tested through the endpoint, promoted to one hundred percent, and the old version undeployed. Each step's inputs, outputs and metrics are recorded as lineage."
    ),
    "release": (
        "The same run from the command line. The gate's verdict is explicit: the candidate's A U C P R, the champion's, and a pass. The promote step then writes champion dot json, "
        "which the next gate will read, and the baseline of feature distributions that the drift job will compare against. Nothing in this path is a human clicking approve; "
        "the numbers are the approval."
    ),
    "registry": (
        "The Model Registry holds every version under one parent model, with the evaluation metrics as labels and the aliases champion and challenger. The endpoint page shows what is actually serving: "
        "one endpoint, the deployed version, its traffic share and its replicas. A version in the registry is an artifact; a deployed model is a release. Keeping those apart is the point."
    ),
    "predict": (
        "Live predictions through the endpoint: real rows from the public set, including fraud rows, scored by the serving version with the truth label next to each score. "
        "The response names the deployed model and the model version, so every prediction is attributable to a registry entry, and every request lands in the BigQuery log."
    ),
    "bad": (
        "The bad-model drill. I submit the same pipeline with sabotage switched on: a third of the labels shuffled and the three strongest features zeroed. Training still succeeds, evaluation still produces numbers, "
        "and the gate refuses it with the reason spelled out: the A U C P R collapsed below the floor and far below the champion. The run is red, the registry has no new version, the endpoint is untouched. "
        "That is a worse model that never reached production, caught by a rule, not by luck."
    ),
    "drift": (
        "The drift drill. A challenger is serving ten percent of traffic. I send three hundred requests whose feature distribution is nothing like the training data, amounts forty times higher, "
        "the first four components shifted, and then run the drift job. It reads the last hour of logged requests, bins them with the champion's baseline, and the population stability index "
        "jumps far above the threshold on exactly those features. Because a challenger was serving, the job moves one hundred percent of traffic back to the champion and the alert fires for a human."
    ),
    "observe": (
        "Observability for a model, the SRE way. The dashboard shows predictions and errors per deployed model, p ninety-five model latency, replicas, and the PSI series the drift job writes. "
        "Two SLOs on the endpoint's own metrics: ninety-nine point five percent of predictions succeed and ninety-five percent finish under three hundred milliseconds, with burn-rate alerts. "
        "A model that is accurate but slow or erroring is still an incident."
    ),
    "ci": (
        "Delivery is keyless: a push to the pipeline folder compiles the definition and submits a release run as the pipeline service account through Workload Identity Federation. "
        "Run workflow with sabotage set to true ships the bad model on purpose, so the gate can be exercised from the same place as a real release."
    ),
    "wrap": (
        "Everything shown is in the repository: the pipeline with its six components and the gate, the drift job with PSI and the rollback, Terraform for the endpoint, logging, SLOs, "
        "alerts, dashboard, identities and federation, the drills and the keyless workflow. The idle cost is the endpoint's replicas, and the teardown script removes it all. Thanks for watching."
    ),
}


async def main() -> None:
    use_cdp = cdp_available()
    demo = Demo(out_dir=HERE, cdp_url=CDP_URL if use_cdp else None, max_silence=5.0, tail=4.0)
    await demo.prepare(*NARRATION.values())
    alen = {k: (await demo._tts(v))[1] for k, v in NARRATION.items()}

    t0 = time.time()
    outs = json.loads(wsl("terraform -chdir=terraform output -json"))
    o = {k: v["value"] for k, v in outs.items()}
    stamp = time.strftime("%H%M", time.gmtime())
    pipelines_url = o["pipelines_url"]
    registry_url = o["registry_url"]
    endpoints_url = o["endpoints_url"]
    dash_url = o["dashboard_url"]
    slo_url = o["slo_url"]
    print(f"pre-computed in {time.time() - t0:.0f}s")

    # --- two real pipeline runs start now and finish in the background (~12 min each) ---
    runs: dict = {}

    def submit(kind: str):
        extra = "--sabotage" if kind == "bad" else "--no-promote"
        out = wsl(f"./deploy.sh {'bad' if kind == 'bad' else 'release'} demo{stamp}-{kind} --no-wait 2>&1 | tail -3", timeout=600)
        m = re.search(r"run:\s+(\S+)", out)
        runs[kind] = {"log": out, "name": m.group(1) if m else ""}
    # the healthy release first (it must be judged against the champion while the bad run also judges against v1)
    submit("rel")
    bad_task = asyncio.ensure_future(asyncio.to_thread(submit, "bad"))

    page = await demo.start()
    pipe_tab = reg_tab = ep_tab = dash_tab = slo_tab = ci_tab = None
    if use_cdp:
        pipe_tab = await demo.new_tab(pipelines_url)
        reg_tab = await demo.new_tab(registry_url)
        ep_tab = await demo.new_tab(endpoints_url)
        dash_tab = await demo.new_tab(dash_url)
        slo_tab = await demo.new_tab(slo_url)
        if CI_URL:
            ci_tab = await demo.new_tab(CI_URL)
        await demo.capture(page)

    # ---- 1 architecture (rendered BEFORE the scene) ----
    await page.bring_to_front()
    await page.goto((ROOT / "docs" / "architecture.html").as_uri(), wait_until="commit")
    await page.wait_for_selector(".canvas", timeout=15000)
    for _ in range(6):
        width = await page.evaluate(
            "() => { if (!document.getElementById('demo-still')) { const st = document.createElement('style'); st.id = 'demo-still'; st.textContent = '*{animation:none!important}'; document.head.appendChild(st); }"
            " document.body.style.zoom = '1.33'; document.body.style.margin = '0';"
            " return document.querySelector('.canvas').getBoundingClientRect().width; }")
        if width > 1800:
            break
        await asyncio.sleep(0.5)
    print(f"  architecture canvas width after zoom: {width:.0f}px")
    await asyncio.sleep(1.5)

    async def s1():
        pass
    await demo.scene("Architecture", NARRATION["arch"], s1)

    # ---- 2 the pipeline in the console (DAG of the healthy run, in progress) ----
    rel = runs.get("rel", {}).get("name", "")
    rel_url = f"https://console.cloud.google.com/vertex-ai/locations/{REGION}/pipelines/runs/{rel}?project={PROJECT}" if rel else pipelines_url
    if use_cdp:
        await pipe_tab.goto(rel_url, wait_until="commit", timeout=120000)
        await console_ready(pipe_tab, "extract", reload=False)

        async def s2():
            await demo.capture(pipe_tab)
        await demo.scene("Pipeline", NARRATION["pipeline"], s2, hold=1.5)
        await demo.capture(page)

    # ---- 3 predictions (the champion v1 serves while the runs continue) ----
    steps3 = await precapture([
        ("drills/predict.sh 6", lambda: wsl("drills/predict.sh 6 2>&1", timeout=120)),
        ("gcloud ai endpoints describe fraud-endpoint --format='yaml(deployedModels[].displayName,trafficSplit)'",
         lambda: gcloud(f"ai endpoints describe fraud-endpoint --region {REGION} --format='yaml(deployedModels[].displayName,deployedModels[].id,trafficSplit)'")),
        ("bq query 'SELECT COUNT(*) logged, MIN(logging_time) since FROM mlops.request_response_logging'",
         lambda: wsl(f"bq query --project_id={PROJECT} --use_legacy_sql=false --format=pretty 'SELECT COUNT(*) AS logged_requests, MIN(logging_time) AS since FROM `{PROJECT}.mlops.request_response_logging`' 2>&1 | tail -6")),
    ])
    await demo.terminal("Live predictions through the endpoint, every one of them logged", [])
    await asyncio.sleep(0.8)

    async def s3():
        await replay(page, steps3, spread=alen["predict"] * 0.6)
    await demo.scene("Predictions", NARRATION["predict"], s3, hold=1.0)

    # ---- wait for the healthy release to finish (canary at 10%, not promoted) ----
    for _ in range(80):
        st = await asyncio.to_thread(run_state, rel)
        if "SUCCEEDED" in st or "FAILED" in st or "CANCELLED" in st:
            break
        await asyncio.sleep(15)
    print(f"  release run {rel}: {st}")

    # ---- 4 the release from the command line ----
    steps4 = await precapture([
        (f"./deploy.sh release demo{stamp}-rel --no-promote", trim(runs["rel"]["log"], 600)),
        (f"gcloud ai pipeline-jobs describe {rel} --format='table(jobDetail.taskDetails[].taskName,jobDetail.taskDetails[].state)'",
         lambda: gcloud(f"ai pipeline-jobs describe {rel} --region {REGION} --format='table(jobDetail.taskDetails[].taskName,jobDetail.taskDetails[].state)'")),
        ("gsutil cat gs://$BUCKET/champion.json", lambda: wsl(f"gcloud storage cat gs://{o['bucket']}/champion.json 2>&1")),
        ("gcloud ai endpoints describe fraud-endpoint --format='value(trafficSplit)'   # challenger at 10% beside the champion",
         lambda: gcloud(f"ai endpoints describe fraud-endpoint --region {REGION} --format='yaml(deployedModels[].displayName,trafficSplit)'")),
    ])
    await demo.terminal("A release: extract → train → evaluate → GATE → register → canary 10%", [])
    await asyncio.sleep(0.8)

    async def s4():
        await replay(page, steps4, spread=alen["release"] * 0.6)
    await demo.scene("Release", NARRATION["release"], s4, hold=1.0)

    # ---- 5 registry + endpoint pages ----
    if use_cdp:
        await console_ready(reg_tab, "fraud-detector")
        await console_ready(ep_tab, "fraud-endpoint")

        async def s5():
            await demo.capture(reg_tab)
            await asyncio.sleep(alen["registry"] * 0.5)
            await demo.capture(ep_tab)
        await demo.scene("Registry and endpoint", NARRATION["registry"], s5, hold=1.5)
        await demo.capture(page)

    # ---- 6 bad model: wait for the sabotaged run to fail at the gate ----
    await bad_task
    bad = runs.get("bad", {}).get("name", "")
    for _ in range(80):
        st = await asyncio.to_thread(run_state, bad)
        if "SUCCEEDED" in st or "FAILED" in st or "CANCELLED" in st:
            break
        await asyncio.sleep(15)
    print(f"  bad run {bad}: {st}")
    steps6 = await precapture([
        (f"./deploy.sh bad demo{stamp}-bad", trim(runs["bad"]["log"], 500)),
        (f"gcloud ai pipeline-jobs describe {bad} --format='table(jobDetail.taskDetails[].taskName,jobDetail.taskDetails[].state)'",
         lambda: gcloud(f"ai pipeline-jobs describe {bad} --region {REGION} --format='table(jobDetail.taskDetails[].taskName,jobDetail.taskDetails[].state)'")),
        (f"gcloud ai pipeline-jobs describe {bad} --format='value(error.message)' | fold -w 150", lambda: gcloud(f"ai pipeline-jobs describe {bad} --region {REGION} --format='value(error.message)' | fold -s -w 150 | head -12")),
        ("gcloud ai models list --filter=displayName=fraud-detector --format='table(versionId,versionAliases.list(),labels.auc_pr)'   # no new version",
         lambda: gcloud(f"ai models list --region {REGION} --filter='displayName=fraud-detector' --format='table(versionId,versionAliases.list(),labels.auc_pr,createTime.date())'")),
    ])
    await demo.terminal("Drill 1: a sabotaged model meets the gate", [])
    await asyncio.sleep(0.8)

    async def s6():
        await replay(page, steps6, spread=alen["bad"] * 0.6)
    await demo.scene("Bad model drill", NARRATION["bad"], s6, hold=1.0)

    # ---- 7 drift drill (challenger at 10% from the release run) ----
    steps7 = await precapture([
        ("drills/drift.sh", lambda: trim(wsl("drills/drift.sh 2>&1", timeout=600), 2400)),
    ])
    await demo.terminal("Drill 2: skewed traffic → PSI → automated rollback to the champion", [])
    await asyncio.sleep(0.8)

    async def s7():
        await replay(page, steps7, spread=4.0, hold=3.0)
    await demo.scene("Drift drill", NARRATION["drift"], s7, hold=1.5)

    # ---- 8 dashboard + SLOs ----
    if use_cdp:
        await console_ready(dash_tab, "Predictions/s")
        await console_ready(slo_tab, "predictions")

        async def s8():
            await demo.capture(dash_tab)
            await asyncio.sleep(alen["observe"] * 0.6)
            await demo.capture(slo_tab)
        await demo.scene("Dashboard and SLOs", NARRATION["observe"], s8, hold=1.5)
        await demo.capture(page)

    # ---- 9 CI ----
    if use_cdp and ci_tab is not None:
        await console_ready(ci_tab, "release", reload=False)

        async def s9():
            await demo.capture(ci_tab)
        await demo.scene("Keyless CI", NARRATION["ci"], s9, hold=1.0)
        await demo.capture(page)
    else:
        steps9 = await precapture([("sed -n '1,14p' .github/workflows/release.yml", lambda: wsl("sed -n '1,14p' .github/workflows/release.yml"))])
        await demo.terminal("Keyless CI: a push submits the release pipeline", [])
        await asyncio.sleep(0.8)

        async def s9():
            await replay(page, steps9, spread=alen["ci"] * 0.5)
        await demo.scene("Keyless CI", NARRATION["ci"], s9, hold=1.0)

    # ---- 10 wrap ----
    steps10 = await precapture([
        ("terraform -chdir=terraform state list | wc -l", lambda: wsl("terraform -chdir=terraform state list | wc -l")),
        ("terraform -chdir=terraform output", lambda: wsl("terraform -chdir=terraform output -no-color")),
        ("# when done: ./teardown.sh", "undeploy models → terraform destroy → endpoint / bucket leftovers → billing audit"),
    ])
    await demo.terminal("Infrastructure as code + cleanup", [])
    await asyncio.sleep(0.8)

    async def s10():
        await replay(page, steps10, spread=alen["wrap"] * 0.5)
    await demo.scene("Wrap-up", NARRATION["wrap"], s10, hold=1.5)

    await demo.finish("demo")


if __name__ == "__main__":
    asyncio.run(main())
