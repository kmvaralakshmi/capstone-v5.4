"""Regenerate tests/golden/master_scores_golden.csv from a fresh offline run (do this only on purpose)."""
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
out = Path(tempfile.mkdtemp(prefix="esg_golden_"))
subprocess.run([sys.executable, "run_agents.py", "--offline", "--quiet", "--out-dir", str(out)], cwd=ROOT, check=True)
m = pd.read_csv(out / "esg_master_scores.csv")[["Company_Code", "Master_ESG_Score", "Quality_Gate_Status"]]
(Path(__file__).resolve().parent / "golden").mkdir(exist_ok=True)
m.to_csv(Path(__file__).resolve().parent / "golden" / "master_scores_golden.csv", index=False)
print("wrote golden for", len(m), "companies")
