"""Run the multi-agent ESG pipeline in one of two completely separate MODES.

    OFFLINE  bundled data, NO LLM            deterministic, fast, fully reproducible
    DYNAMIC  requests live Yahoo/GDELT/CPCB data, with explicit cache/bundled fallback;
             LLM (Ollama / OpenAI / compatible) if available

Each mode has its own results and its own dashboard view, so the two are never mixed:
    batch results   processed-data/batch_offline/      processed-data/batch_dynamic/
    on-demand runs  data/runs/offline/<company>_<time>/   data/runs/dynamic/<company>_<time>/

BATCH (all companies)
    python run_agents.py --mode offline
    python run_agents.py --mode dynamic
ON-DEMAND (one company: name or NSE symbol)
    python run_agents.py --company Infosys --mode offline
    python run_agents.py --company Infosys --mode dynamic
    python run_agents.py --company HDFCBANK --hq-city Mumbai --mode dynamic     # outside the cohort
Subsets / re-runs
    python run_agents.py --mode offline --agents agent6 agent7 agent8
    python run_agents.py --mode offline --companies TCS INFY WIPRO
Other
    --no-llm      dynamic data but without the LLM (faster)
    --force       ignore the Agent 1 extraction cache
    (legacy flags still work:  --offline = --mode offline,  --dynamic = --mode dynamic,
     --dynamic --offline = live data without the LLM)
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["offline", "dynamic"],
                    help="offline = bundled data, no LLM; dynamic = request live feeds and use available LLM")
    ap.add_argument("--company", help="on-demand run for ONE company (name or NSE symbol)")
    ap.add_argument("--hq-city", default="", help="HQ city for companies outside the cohort (air-quality context)")
    ap.add_argument("--companies", nargs="*", help="batch subset, e.g. TCS INFY")
    ap.add_argument("--agents", nargs="*", help="agent0 ... agent8")
    ap.add_argument("--no-llm", action="store_true", help="in dynamic mode: live data but no LLM")
    ap.add_argument("--dynamic", action="store_true", help="legacy alias for --mode dynamic")
    ap.add_argument("--offline", action="store_true", help="legacy alias for --mode offline")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out-dir", help="output folder (default: depends on mode, see above)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    mode = a.mode or ("dynamic" if a.dynamic else "offline")
    if a.dynamic and a.offline and not a.mode:         # legacy combination: live data but no LLM
        mode, a.no_llm = "dynamic", True
    use_llm = (mode == "dynamic") and not a.no_llm
    dynamic = mode == "dynamic"
    batch_target = None
    os.environ["ESG_MODE"] = mode                      # must be set BEFORE esg_agents is imported

    if a.company and not a.out_dir:
        slug = "".join(ch for ch in a.company if ch.isalnum()).upper()[:20]
        a.out_dir = str(ROOT / "data" / "runs" / mode / f"{slug}_{datetime.now():%Y%m%d_%H%M%S}")
    if not a.out_dir:
        batch_target = ROOT / "processed-data" / f"batch_{mode}"
        a.out_dir = str(ROOT / "processed-data" / f".batch_{mode}_{uuid.uuid4().hex}.staging")
    os.environ["ESG_OUT_DIR"] = a.out_dir
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Output folder: {a.out_dir}")
    print(f"Mode: {mode.upper()}  (live data: {dynamic}, LLM requested: {use_llm})", flush=True)
    started = datetime.now().isoformat(timespec="seconds")
    (out / "run_mode.json").write_text(json.dumps(
        {"mode": mode, "live_data": dynamic, "llm_requested": use_llm, "started": started,
         "kind": "on_demand" if a.company else "batch", "query": a.company, "status": "running"}, indent=2),
        encoding="utf-8")

    from esg_agents.supervisor import Supervisor
    sup = Supervisor(agents=a.agents, companies=[c.upper() for c in a.companies] if a.companies else None,
                     query=a.company, hq_city=a.hq_city, use_llm=use_llm, force=a.force,
                     dynamic=dynamic, verbose=not a.quiet)
    rep = sup.run()
    t = rep["timing"]
    ok = all(r["status"] in ("completed", "skipped") for r in rep["agents"])
    if batch_target is not None:
        ok = ok and (out / "esg_master_scores.csv").is_file()
    (out / "run_mode.json").write_text(json.dumps(
        {"mode": mode, "live_data": dynamic, "llm_requested": use_llm, "llm_used": bool(rep.get("llm_mode")),
         "llm": rep.get("llm"), "started": started, "finished": datetime.now().isoformat(timespec="seconds"),
         "kind": "on_demand" if a.company else "batch", "query": a.company, "companies": rep.get("companies"),
         "status": "completed" if ok else "failed"}, indent=2), encoding="utf-8")
    print(f"\n=== RUN SUMMARY ===  MODE: {mode.upper()}  LLM: {rep['llm']}  | total {t['total_seconds']}s")
    for r in rep["agents"]:
        print(f"{r['agent_id']:8} {r['status']:10} {r.get('seconds', '-')!s:>8}s  {r['summary']}")
        print(f"  planner={r.get('planner_mode', 'off')} "
              f"decisions={r.get('llm_planning_decisions', 0)} "
              f"judgements={r.get('llm_judgements', 0)} "
              f"budget={r.get('budget_reason', 'none')} "
              f"planning/judging={r.get('llm_planning_seconds', 0.0):.2f}s/"
              f"{r.get('llm_judging_seconds', 0.0):.2f}s")
    for name, st in t["stages"].items():
        print(f"  stage {name:12} wall={st['stage_wall_seconds']}s  sum_of_parts={st['sum_seconds']}s")
    if t["bottleneck"]:
        print(f"  bottleneck: {t['bottleneck']['name']} ({t['bottleneck']['seconds']}s)")
    if batch_target is not None:
        if ok:
            from esg_agents.publishing import publish_directory
            warning = publish_directory(out, batch_target)
            print(f"Published batch folder: {batch_target}")
            if warning:
                print(f"Warning: {warning}")
        else:
            print(f"Batch failed; previous results were kept. Staged output: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
