"""Read a SEBI BRSR XBRL instance into plain facts.

Only the XML structure is interpreted here (contexts, units, facts). What a concept *means*
for ESG is decided in `metrics.py`. Nothing in this module guesses or defaults a value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree

_UNIT_NS_STRIP = re.compile(r"^.*:")


@dataclass(frozen=True)
class Fact:
    concept: str
    context: str
    unit: str          # unit id as written in the filing (may be mislabelled by the filer)
    text: str
    value: Optional[float]   # parsed number, None for text/boolean facts


class XbrlFiling:
    """Facts, contexts and units of one BRSR XBRL instance."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.facts: List[Fact] = []
        self.contexts: Dict[str, Dict[str, str]] = {}
        self.unit_measure: Dict[str, str] = {}
        self._index: Dict[str, List[Fact]] = {}
        self._parse()

    # ------------------------------------------------------------------ parse
    def _parse(self) -> None:
        for _, el in etree.iterparse(str(self.path), events=("end",), huge_tree=True, recover=True):
            tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
            if tag == "context":
                cid = el.get("id")
                info = {}
                for child in el.iter():
                    if not isinstance(child.tag, str):
                        continue
                    ln = etree.QName(child).localname
                    if ln in ("startDate", "endDate", "instant", "identifier") and child.text:
                        info[ln] = child.text.strip()
                    if ln in ("explicitMember", "typedMember"):
                        info.setdefault("dims", "")
                        info["dims"] += (child.get("dimension", "") + ";")
                self.contexts[cid] = info
            elif tag == "unit":
                m = [c.text for c in el.iter() if isinstance(c.tag, str) and etree.QName(c).localname == "measure"]
                self.unit_measure[el.get("id")] = (m[0] or "") if m else ""
            elif el.get("contextRef") is not None:
                text = (el.text or "").strip()
                val = None
                if el.get("unitRef") is not None and text not in ("", "-"):
                    try:
                        val = float(text.replace(",", ""))
                    except ValueError:
                        val = None
                self.facts.append(Fact(tag, el.get("contextRef"), el.get("unitRef") or "", text, val))
        for f in self.facts:
            self._index.setdefault(f.concept, []).append(f)

    # --------------------------------------------------------------- queries
    @property
    def entity_id(self) -> Optional[str]:
        c = self.contexts.get("DCYMain") or next(iter(self.contexts.values()), {})
        return c.get("identifier")

    def period(self) -> Tuple[Optional[str], Optional[str]]:
        c = self.contexts.get("DCYMain", {})
        return c.get("startDate"), c.get("endDate")

    def get(self, concept: str, context: str = "DCYMain") -> Optional[Fact]:
        for f in self._index.get(concept, []):
            if f.context == context:
                return f
        return None

    def find(self, concept: str, ctx_regex: str) -> List[Fact]:
        rx = re.compile(ctx_regex)
        return [f for f in self._index.get(concept, []) if rx.search(f.context)]

    def first_number(self, concept: str, contexts: Iterable[str]) -> Optional[Fact]:
        for c in contexts:
            f = self.get(concept, c)
            if f is not None and f.value is not None:
                return f
        return None

    def text_fact(self, concept: str, context: str = "DCYMain") -> Optional[str]:
        f = self.get(concept, context)
        return f.text if f is not None and f.text else None

    def find_prefix(self, prefix: str, ctx_regex: str = ".*") -> List[Fact]:
        rx = re.compile(ctx_regex)
        return [f for c, fs in self._index.items() if c.startswith(prefix) for f in fs if rx.search(f.context)]

    def has_concept(self, concept: str) -> bool:
        return concept in self._index

    def unit_label(self, fact: Fact) -> str:
        return self.unit_measure.get(fact.unit, fact.unit)


def truthy(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    t = text.strip().lower()
    if t in ("yes", "true", "y", "1"):
        return True
    if t in ("no", "false", "n", "0"):
        return False
    return None
