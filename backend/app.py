"""ESG Intelligence backend (v5.4).

    python -m uvicorn backend.app:app --reload        then open http://127.0.0.1:8000

On-demand analysis spawns `run_agents.py --company ...` in its own process and
output folder (data/runs/<symbol>_<time>), so a single-company run never
overwrites the batch (50-company) results in processed-data/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / "processed-data"
RUNS_ROOT = ROOT / "data" / "runs"
JOBS_ROOT = ROOT / "data" / "jobs"
MODES = ("offline", "dynamic")
RUN_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")
FRONTEND = ROOT / "frontend"

app = FastAPI(title="ESG Intelligence System", version="5.4")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
JOBS: Dict[str, Dict[str, Any]] = {}


# ----------------------------------------------------------------- helpers --
def norm_mode(mode: Optional[str]) -> str:
    m = (mode or "offline").lower()
    return m if m in MODES else "offline"


def batch_dir(mode: str) -> Path:
    return BASE / f"batch_{norm_mode(mode)}"


def runs_dir(mode: str) -> Path:
    return RUNS_ROOT / norm_mode(mode)


def csv_rows(folder: Path, name: str, code: Optional[str] = None) -> list:
    p = folder / name
    if not p.exists():
        return []
    try:
        df = pd.read_csv(p)
    except Exception:
        return []
    if code and "Company_Code" in df:
        df = df[df["Company_Code"].astype(str).str.upper() == code.upper()]
    return json.loads(df.to_json(orient="records"))      # NaN -> null


def mode_capabilities(folder: Path, code: Optional[str] = None) -> Dict[str, bool]:
    scores = csv_rows(folder, "esg_master_scores.csv", code)
    metrics = csv_rows(folder, "esg_metric_scores.csv", code)
    brsr = csv_rows(folder, "brsr_extracted_metrics.csv", code)
    market = csv_rows(folder, "stock_esg_correlation.csv", code)
    news = csv_rows(folder, "esg_news_company_summary.csv", code)
    air = csv_rows(folder, "company_location_environmental_risk.csv", code)
    claims = csv_rows(folder, "greenwashing_claims.csv", code)
    consistency = csv_rows(folder, "greenwashing_detection.csv", code)
    report = json_file(folder, "agent_run_report.json") or {}
    agents = report.get("agents") or []
    evidence = json_file(folder, "esg_evidence_packages.json") or {}
    return {
        "dashboard": bool(scores),
        "performance": bool(metrics or brsr),
        "market": any(row.get("Coverage") == "price_data" for row in market),
        "news": bool(news),
        "environment": bool(air),
        "greenwashing": bool(claims or consistency or any(
            a.get("agent_id") == "agent5" and a.get("status") == "completed" for a in agents)),
        "agents": bool(agents),
        "timing": (folder / "run_metrics.json").is_file(),
        "sources": bool(brsr or (evidence and (code is None or code in evidence))),
        "validation": (folder / "validation_summary.json").is_file(),
        "cohort": bool(scores),
    }


def json_file(folder: Path, name: str) -> Any:
    p = folder / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None


def resolve_folder(mode: str, run: Optional[str]) -> Path:
    if run in (None, "", "batch"):
        return batch_dir(mode)
    if not RUN_NAME.match(run):
        raise HTTPException(400, "bad run name")
    p = runs_dir(mode) / run
    if not p.is_dir():
        raise HTTPException(404, f"run '{run}' not found in {mode} mode")
    return p


def run_info(d: Path, mode: str) -> Dict[str, Any]:
    """Summary of a run folder. `ok` is True only for runs where EVERY agent completed and scores exist."""
    rep = json_file(d, "agent_run_report.json") or {}
    meta = json_file(d, "run_mode.json") or {}
    agents = rep.get("agents") or []
    ok = bool(rep) and bool(agents) and all(a.get("status") in ("completed", "skipped") for a in agents) \
        and (d / "esg_master_scores.csv").exists()
    state = "completed" if ok else ("running_or_crashed" if not rep else "failed")
    return {"run": d.name, "mode": mode, "companies": rep.get("companies") or [],
            "finished": rep.get("finished") or meta.get("finished"), "started": meta.get("started"),
            "total_seconds": (rep.get("timing") or {}).get("total_seconds"),
            "llm": rep.get("llm"), "llm_used": rep.get("llm_mode"), "live_data": meta.get("live_data"),
            "ok": ok, "state": state}


def list_runs(mode: str, include_invalid: bool = False) -> Dict[str, Any]:
    out, hidden = [], 0
    d0 = runs_dir(mode)
    if d0.exists():
        for d in d0.iterdir():
            if not d.is_dir():
                continue
            info = run_info(d, mode)
            if info["ok"] or include_invalid:
                out.append(info)
            else:
                hidden += 1
    out.sort(key=lambda r: r["run"], reverse=True)          # folder names end with a timestamp
    return {"runs": out, "hidden_unfinished_or_failed": hidden}


def latest_run_for(mode: str, code: str) -> Optional[Path]:
    for r in list_runs(mode)["runs"]:
        if code.upper() in [c.upper() for c in r["companies"]]:
            return runs_dir(mode) / r["run"]
    return None


def _batch_is_complete(folder: Path, returncode: int) -> bool:
    if returncode != 0:
        return False
    report = json_file(folder, "agent_run_report.json") or {}
    metadata = json_file(folder, "run_mode.json") or {}
    agents = report.get("agents") or []
    return (metadata.get("status") == "completed" and bool(agents)
            and all(agent.get("status") in ("completed", "skipped") for agent in agents)
            and (folder / "esg_master_scores.csv").is_file())


def _publish_batch(mode: str, staging: Path) -> Optional[str]:
    """Replace a mode's published batch only after a complete run has been staged."""
    from esg_agents.publishing import publish_directory
    return publish_directory(staging, batch_dir(mode))


def _job_progress(job: Dict[str, Any]) -> Dict[str, Any]:
    expected = job.get("expected_agents") or []
    states = {agent_id: "waiting" for agent_id in expected}
    for line in job.get("log", []):
        match = re.search(r"\[(agent\d+)\]\s+(start|completed|stalled|failed|max_steps)", line)
        if match and match.group(1) in states:
            states[match.group(1)] = "running" if match.group(2) == "start" else match.group(2)
        skipped = re.search(r"\[supervisor\]\s+(agent\d+)\s+skipped:", line)
        if skipped and skipped.group(1) in states:
            states[skipped.group(1)] = "skipped"
    done = sum(state in ("completed", "skipped") for state in states.values())
    return {"agents": [{"id": agent_id, "status": state} for agent_id, state in states.items()],
            "completed": done, "total": len(states),
            "percent": round(100 * done / len(states)) if states else 0,
            "current": [agent_id for agent_id, state in states.items() if state == "running"]}


def _persist_job(job: Dict[str, Any]) -> None:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    path = JOBS_ROOT / f"{job['id']}.json"
    temp = path.with_suffix(".tmp")
    snapshot = {key: job.get(key) for key in
                ("id", "company", "mode", "status", "log", "run", "error", "warning",
                 "expected_agents", "_staging_dir")}
    snapshot["progress"] = _job_progress(job)
    temp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    os.replace(temp, path)


# ---------------------------------------------------------------- endpoints --
@app.get("/api/health")
def health():
    from esg_agents.framework import LLMBrain
    b = LLMBrain(enabled=True)
    modes = {}
    for mode in MODES:
        folder = batch_dir(mode)
        run_report = json_file(folder, "agent_run_report.json") or {}
        metrics = json_file(folder, "run_metrics.json") or {}
        modes[mode] = {
            "batch_ready": (folder / "esg_master_scores.csv").exists(),
            "runs": len(list_runs(mode)["runs"]),
            "data_sources": metrics.get("data_sources", []),
            "llm_mode": bool(run_report.get("llm_mode")),
        }
    return {"status": "ok", "llm": b.describe(), "modes": modes}


@app.get("/api/cohort")
def cohort(mode: str = "offline"):
    folder = batch_dir(mode)
    rows = csv_rows(folder, "esg_master_scores.csv")
    metrics = json_file(folder, "run_metrics.json") or {}
    return {"mode": norm_mode(mode), "companies": sorted(rows, key=lambda r: (r.get("Rank") is None, r.get("Rank") or 1e9)),
            "meta": json_file(folder, "run_mode.json"), "llm_usage": metrics.get("llm_usage"),
            "capabilities": mode_capabilities(folder)}


@app.get("/api/runs")
def runs(mode: str = "offline", include_invalid: bool = False):
    return {"mode": norm_mode(mode), **list_runs(mode, include_invalid)}


@app.delete("/api/runs/{mode}/{run}")
def delete_run(mode: str, run: str):
    p = resolve_folder(mode, run)
    if p == batch_dir(mode):
        raise HTTPException(400, "the batch folder cannot be deleted here")
    shutil.rmtree(p, ignore_errors=True)
    return {"deleted": run}


@app.post("/api/runs/cleanup")
def cleanup_runs(mode: str = "offline"):
    """Delete unfinished/failed runs of this mode (they are hidden from the list anyway)."""
    n = 0
    d0 = runs_dir(mode)
    if d0.exists():
        busy = {j["run"] for j in JOBS.values() if j["status"] == "running" and j.get("run")}
        for d in d0.iterdir():
            if d.is_dir() and not run_info(d, norm_mode(mode))["ok"] and d.name not in busy:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
    return {"removed": n}


class AnalyzeRequest(BaseModel):
    company: str
    hq_city: str = ""
    mode: str = "offline"            # offline | dynamic
    use_llm: bool = True             # only meaningful in dynamic mode


def _run_job(job_id: str, cmd: list, staging_dir: Optional[Path] = None):
    job = JOBS[job_id]
    try:
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:                                   # type: ignore[union-attr]
            job["log"].append(line.rstrip())
            job["log"] = job["log"][-400:]
            m = re.match(r"Output folder:\s*(.+)", line)
            if m and job.get("run") != "batch":
                job["run"] = Path(m.group(1).strip()).name
            if len(job["log"]) % 20 == 0 or re.search(
                    r"\[(agent\d+)\]\s+(start|completed|stalled|failed|max_steps)", line) or \
                    "[supervisor]" in line and "skipped:" in line:
                _persist_job(job)
        p.wait()
        if staging_dir is not None:
            if not _batch_is_complete(staging_dir, p.returncode):
                job["status"] = "failed"
                job["error"] = "Batch did not complete successfully; the previously published results were kept."
                shutil.rmtree(staging_dir, ignore_errors=True)
                return
            job["warning"] = _publish_batch(job["mode"], staging_dir)
            job["status"] = "completed"
        else:
            job["status"] = "completed" if p.returncode == 0 else "failed"
        _persist_job(job)
    except Exception as exc:
        job["status"], job["error"] = "failed", str(exc)
        _persist_job(job)


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    if not req.company.strip():
        raise HTTPException(400, "company is required")
    mode = norm_mode(req.mode)
    job_id = uuid.uuid4().hex[:10]
    cmd = [sys.executable, "run_agents.py", "--company", req.company.strip(), "--mode", mode]
    if mode == "dynamic" and not req.use_llm:
        cmd.append("--no-llm")
    if req.hq_city:
        cmd += ["--hq-city", req.hq_city]
    JOBS[job_id] = {"id": job_id, "company": req.company, "mode": mode, "status": "running",
                    "log": [], "run": None, "expected_agents": [f"agent{i}" for i in range(8)]}
    _persist_job(JOBS[job_id])
    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id, "mode": mode}


@app.post("/api/batch/run")
def batch_run(mode: str = "offline", use_llm: bool = False):
    mode = norm_mode(mode)
    job_id = uuid.uuid4().hex[:10]
    staging_dir = BASE / f".batch_{mode}_{job_id}.staging"
    cmd = [sys.executable, "run_agents.py", "--mode", mode, "--out-dir", str(staging_dir)]
    if mode == "dynamic" and not use_llm:
        cmd.append("--no-llm")
    JOBS[job_id] = {"id": job_id, "company": f"ALL ({mode} batch)", "mode": mode,
                    "status": "running", "log": [], "run": "batch",
                    "expected_agents": [f"agent{i}" for i in range(1, 9)],
                    "_staging_dir": str(staging_dir)}
    _persist_job(JOBS[job_id])
    threading.Thread(target=_run_job, args=(job_id, cmd, staging_dir), daemon=True).start()
    return {"job_id": job_id, "mode": mode}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        if not re.fullmatch(r"[a-f0-9]{10}", job_id):
            raise HTTPException(404, "unknown job")
        path = JOBS_ROOT / f"{job_id}.json"
        j = json_file(JOBS_ROOT, path.name)
        if not j:
            raise HTTPException(404, "unknown job")
        if j.get("status") == "running":
            j["status"] = "failed"
            j["error"] = "The server restarted before this job completed; the published batch was not changed."
            _persist_job(j)
        JOBS[job_id] = j
    folder = None
    if j["status"] == "running" and j.get("_staging_dir"):
        folder = Path(j["_staging_dir"])
    elif j.get("run"):
        folder = batch_dir(j["mode"]) if j["run"] == "batch" else runs_dir(j["mode"]) / j["run"]
    metrics = json_file(folder, "run_metrics.json") if folder and folder.exists() else None
    agents_done = [l for l in j["log"] if re.search(r"\[agent\d\] (completed|stalled|failed)", l)]
    return {**{k: j[k] for k in ("id", "company", "mode", "status", "run")}, "log_tail": j["log"][-25:],
            "agents_finished": agents_done, "metrics": metrics, "error": j.get("error"),
            "warning": j.get("warning"), "progress": j.get("progress") or _job_progress(j)}


def _cohort_rank(mode: str, code: str) -> Dict[str, Any]:
    rows = csv_rows(batch_dir(mode), "esg_master_scores.csv")
    scored = [r for r in rows if r.get("Master_ESG_Score") is not None]
    mine = next((r for r in rows if str(r.get("Company_Code", "")).upper() == code.upper()), None)
    if not mine or mine.get("Rank") is None:
        return {"rank": None, "of": len(scored)}
    return {"rank": mine["Rank"], "of": len(scored)}


@app.get("/api/report")
def report(mode: str = "offline", run: str = "batch", company: Optional[str] = None):
    """run = run folder name | 'batch'. For a batch folder pass company=CODE."""
    mode = norm_mode(mode)
    folder = resolve_folder(mode, run)
    code = company
    if not code:
        rows = csv_rows(folder, "esg_master_scores.csv")
        if len(rows) != 1:
            raise HTTPException(400, "pass company=CODE for multi-company folders")
        code = rows[0]["Company_Code"]
    code = code.upper()
    scores = csv_rows(folder, "esg_master_scores.csv", code)
    if not scores:
        raise HTTPException(404, f"{code} has no {mode}-mode results yet")
    traces = {}
    tdir = folder / "agent_traces"
    if tdir.exists():
        for f in sorted(tdir.glob("*_trace.json")):
            t = json.loads(f.read_text(encoding="utf-8"))
            r = t["report"]
            traces[r["agent_id"]] = {"name": r["name"], "status": r["status"], "seconds": r["seconds"], "steps": r["steps"],
                                     "decisions": r["decisions_by_source"], "summary": r["summary"], "llm": r["llm_mode"],
                                     "planner_mode": r.get("planner_mode", "on" if r["llm_mode"] else "off"),
                                     "llm_planning_decisions": r.get("llm_planning_decisions", 0),
                                     "llm_planning_calls": r.get("llm_planning_calls", 0),
                                     "llm_judgements": r.get("llm_judgements", 0),
                                     "budget_reason": r.get("budget_reason", "none"),
                                     "llm_planning_seconds": r.get("llm_planning_seconds", 0.0),
                                     "llm_judging_seconds": r.get("llm_judging_seconds", 0.0),
                                     "guardrail_rejections": r.get("guardrail_rejections", []),
                                     "findings": r["findings"], "history": t["history"][-60:]}
    packages = json_file(folder, "esg_evidence_packages.json") or {}
    run_metrics = json_file(folder, "run_metrics.json") or {}
    all_data_sources = run_metrics.get("data_sources", [])
    data_sources = [source for source in all_data_sources
                    if source.get("company_code") in (None, code)]
    run_report = json_file(folder, "agent_run_report.json") or {}
    return {"mode": mode, "run": run, "run_meta": json_file(folder, "run_mode.json"),
            "llm_mode": bool(run_report.get("llm_mode")),
            "capabilities": mode_capabilities(folder, code),
            "cohort_rank": _cohort_rank(mode, code), "company": code, "scores": scores[0],
            "metrics": csv_rows(folder, "esg_metric_scores.csv", code),
            "brsr": csv_rows(folder, "brsr_extracted_metrics.csv", code),
            "trends": csv_rows(folder, "brsr_trends.csv", code),
            "news_summary": csv_rows(folder, "esg_news_company_summary.csv", code),
            "news_articles": csv_rows(folder, "esg_news_sentiment.csv", code)[:25],
            "market": csv_rows(folder, "stock_esg_correlation.csv", code),
            "air": csv_rows(folder, "company_location_environmental_risk.csv", code),
            "consistency": csv_rows(folder, "greenwashing_detection.csv", code),
            "claims": csv_rows(folder, "greenwashing_claims.csv", code),
            "explanation": csv_rows(folder, "multi_agent_explanations.csv", code),
            "evidence_package": packages.get(code), "filings": json_file(folder, "filing_discovery.json"),
            "agents": traces, "run_metrics": run_metrics, "data_sources": data_sources,
            "blackboard": json_file(folder, "agent_blackboard.json")}


@app.get("/api/company/{query}")
def company_lookup(query: str, mode: str = "offline"):
    """Latest finished on-demand run of this MODE for a company code, else that mode's batch result."""
    mode = norm_mode(mode)
    run = latest_run_for(mode, query)
    if run:
        return report(mode=mode, run=run.name, company=query)
    return report(mode=mode, run="batch", company=query)


@app.get("/api/validation")
def validation(mode: str = "offline"):
    b = batch_dir(mode)
    summary = json_file(b, "validation_summary.json")
    files = {n: csv_rows(b, n) for n in ("validation_nse_top200.csv", "validation_nse_cfa.csv", "validation_esg_stock.csv",
                                         "validation_esg_news.csv", "validation_esg_air.csv", "validation_nse_top200_overlap.csv")}
    return {"mode": norm_mode(mode), "summary": summary, "tables": files,
            "benchmark_files_present": {"nse_top200_sector.csv": (ROOT / "data/benchmark/nse_top200_sector.csv").exists(),
                                        "nse_top200_companies.csv": (ROOT / "data/benchmark/nse_top200_companies.csv").exists(),
                                        "nse_cfa_reference.csv": (ROOT / "data/benchmark/nse_cfa_reference.csv").exists()}}


@app.get("/api/methodology")
def methodology():
    """Scored metrics, anchors, weights and what is deliberately NOT in the score."""
    return json.loads((ROOT / "config" / "anchors.json").read_text(encoding="utf-8"))


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
