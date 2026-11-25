"""Build the external benchmark files used by Agent 8 from the source PDFs.

    python tools/build_benchmarks.py --top200-pdf "<NSE ESG Analysis on Top 200 report>.pdf"

* data/benchmark/nse_top200_companies.csv  the report's company list (rank, name, sector, market cap)
* data/benchmark/nse_top200_sector.csv     sector and sample score statistics (overall, E, S, G)
* data/benchmark/nse_cfa_reference.csv     BRSR disclosure rates from the CFA Society India / NSE study

The NSE report publishes sector-level statistics, NOT per-company scores, so the comparison in
Agent 8 is made at sector level and by company-list overlap. Statistic rows are read from the PDF
tables; the CFA rates are the figures quoted in the study summary (FY2022-23, 300 companies).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmark"
TABLES = {"Overall": "TABLE 5:", "Environmental": "TABLE EV1:", "Social": "TABLE S1:", "Governance": "TABLE G1:"}


def read_text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def sector_rows(text: str) -> pd.DataFrame:
    lines, rows = text.splitlines(), []
    for pillar, marker in TABLES.items():
        start = next((i for i, l in enumerate(lines) if marker in l), None)
        if start is None:
            continue
        for l in lines[start:start + 25]:
            m = re.match(r"\s*(Sample|Information Technology)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\b", l)
            if m:
                rows.append({"Pillar": pillar, "Group": m.group(1), "Max": int(m.group(2)), "Avg": int(m.group(3)),
                             "Median": int(m.group(4)), "Min": int(m.group(5)),
                             "Source": "NSE ESG Analysis on Top 200 Listed Companies (2026), " + marker.strip(":")})
    return pd.DataFrame(rows)


def company_rows(text: str) -> pd.DataFrame:
    rx = re.compile(r"^\s*(\d{1,3})\s{2,}(.+?)\s{2,}([A-Za-z&\-\s]+?)\s{2,}([\d,]+)\s*$", re.M)
    rows = [{"Rank": int(a), "Company_Name": b.strip(), "Sector": c.strip(), "Market_Cap": d} for a, b, c, d in rx.findall(text)]
    return pd.DataFrame(rows).drop_duplicates("Company_Name")


CFA = [
    ("Total Energy Consumption", 96, "Share of companies reporting energy consumption (FY2022-23)"),
    ("Scope 1 and 2 Emissions", 94, "Share of companies reporting Scope 1 and Scope 2 emissions (FY2022-23)"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top200-pdf", type=Path, required=True)
    a = ap.parse_args()
    BENCH.mkdir(parents=True, exist_ok=True)
    text = read_text(a.top200_pdf)
    s, c = sector_rows(text), company_rows(text)
    s.to_csv(BENCH / "nse_top200_sector.csv", index=False)
    c.to_csv(BENCH / "nse_top200_companies.csv", index=False)
    pd.DataFrame([{"Metric": m, "Reference_Pct_Companies_Reporting": v, "Reference_Year": "FY2022-23", "Reference_Sample": "300 listed companies (~70% of market cap)",
                   "Definition": d, "Source": "CFA Institute / NSE / CFA Society India, The Current State of BRSR at Corporate India (Oct 2024)"}
                  for m, v, d in CFA]).to_csv(BENCH / "nse_cfa_reference.csv", index=False)
    print(f"sector rows {len(s)}, companies {len(c)}, IT companies {int((c['Sector'] == 'Information Technology').sum())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
