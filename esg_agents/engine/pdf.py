"""BRSR PDF reader used as *evidence* for XBRL values (never as the primary source).

Text comes from PyMuPDF (fast, per-page, cached). `verify_value` looks for the XBRL number on
pages that also contain the metric's anchor words and returns the page and a snippet, or lists the
numbers printed on the anchor line when none matches so the discrepancy can be adjudicated.
"""
from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
SCALES = (1.0, 1e3, 1e-3, 1e6, 1e-6)


def _to_float(tok: str) -> Optional[float]:
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def load_pages(pdf: Path, cache_dir: Optional[Path] = None) -> List[str]:
    """Page texts of a PDF (cached as gzip JSON next to nothing else; safe to delete)."""
    pdf = Path(pdf)
    cache = (cache_dir / (pdf.stem + ".json.gz")) if cache_dir else None
    if cache and cache.exists() and cache.stat().st_mtime >= pdf.stat().st_mtime:
        return json.loads(gzip.decompress(cache.read_bytes()).decode("utf-8"))
    import pymupdf as fitz  # PyMuPDF
    pages: List[str] = []
    with fitz.open(str(pdf)) as doc:
        for page in doc:
            try:
                pages.append(page.get_text("text") or "")
            except Exception:
                pages.append("")
    if cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(gzip.compress(json.dumps(pages).encode("utf-8")))
    return pages


@dataclass
class PdfEvidence:
    status: str                      # MATCH | MATCH_AFTER_SCALE | NOT_FOUND | MISMATCH | NO_PDF
    page: Optional[int] = None       # 1-based
    snippet: str = ""
    scale: float = 1.0
    pdf_numbers: List[float] = field(default_factory=list)   # numbers on the anchor line (MISMATCH only)


def _close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


def verify_value(pages: List[str], value: float, anchors: List[str], rel_tol: float = 0.005,
                 raw_value: Optional[float] = None) -> PdfEvidence:
    """Find `value` (as printed in the filing: `raw_value` if given) on a page with an anchor word.

    anchors: regex fragments, any of which must appear on the page (case-insensitive).
    """
    if not pages:
        return PdfEvidence("NO_PDF")
    target = raw_value if raw_value is not None else value
    arx = re.compile("|".join(anchors), re.I) if anchors else None
    anchor_line_numbers: List[float] = []
    anchor_page = None
    for pi, text in enumerate(pages, start=1):
        if arx is not None and not arx.search(text):
            continue
        lines = text.splitlines()
        for li, line in enumerate(lines):
            if arx is not None and not arx.search(line):
                continue
            window = " ".join(lines[li:li + 4])
            nums = [x for x in (_to_float(t) for t in _NUM.findall(window)) if x is not None]
            if nums and anchor_page is None:
                anchor_page, anchor_line_numbers = pi, nums[:8]
            for n in nums:
                for sc in SCALES:
                    if _close(n * sc, target, rel_tol):
                        return PdfEvidence("MATCH" if sc == 1.0 else "MATCH_AFTER_SCALE", pi,
                                           re.sub(r"\s+", " ", window)[:200], sc)
    # number anywhere on an anchor page (table layouts that split label and value)
    for pi, text in enumerate(pages, start=1):
        if arx is not None and not arx.search(text):
            continue
        for t in _NUM.findall(text):
            n = _to_float(t)
            if n is not None and _close(n, target, rel_tol) and n >= 10:
                return PdfEvidence("MATCH", pi, f"value {t} on page with anchor", 1.0)
    if anchor_page is not None:
        return PdfEvidence("MISMATCH", anchor_page, "", 1.0, anchor_line_numbers)
    return PdfEvidence("NOT_FOUND")


_UNIT_PATTERNS = {
    "energy": [(r"\(\s*(?:in\s+)?(GJ|MJ|TJ|kWh|MWh|GWh|kJ|J)\s*\)", None), (r"\b(GJ|MJ|TJ|kWh|MWh|GWh)\b", None), (r"\b(gigajoules?)\b", "GJ"), (r"\b(megajoules?)\b", "MJ"),
               (r"\b(terajoules?)\b", "TJ")],
    "revenue": [(r"\b(crores?|cr\.?)\b", "crore"), (r"\b(lakhs?)\b", "lakh"), (r"\b(millions?)\b", "million"),
                (r"\b(billions?)\b", "billion")],
}
UNIT_TEXT_TO_SCALE = {"crore": 1e7, "lakh": 1e5, "million": 1e6, "billion": 1e9}


def unit_hint(pages: List[str], anchors: List[str], kind: str) -> Optional[str]:
    """Unit printed next to an anchor line (e.g. '(GJ)' beside 'Total energy consumed'), or None."""
    if not pages or not anchors:
        return None
    arx = re.compile("|".join(anchors), re.I)
    for text in pages:
        if not arx.search(text):
            continue
        lines = text.splitlines()
        for li, line in enumerate(lines):
            if not arx.search(line):
                continue
            window = " ".join(lines[max(0, li - 1):li + 2])
            for rx, fixed in _UNIT_PATTERNS.get(kind, []):
                m = re.search(rx, window, re.I)
                if m:
                    return fixed or m.group(1)
    return None
