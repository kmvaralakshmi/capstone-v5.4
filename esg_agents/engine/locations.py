"""Company location evidence taken from the BRSR XBRL filing (Section A addresses)."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from .xbrl import XbrlFiling

ADDRESS_CONCEPTS = (("AddressOfCorporateOfficeOfCompany", "Corporate office"),
                    ("AddressOfRegisteredOfficeOfCompany", "Registered office"))


def city_in_text(text: str, known: Iterable[str]) -> Optional[str]:
    """Longest known city name that appears as a whole word in the address text."""
    best = None
    for c in known:
        if re.search(r"\b" + re.escape(c) + r"\b", text, re.I) and (best is None or len(c) > len(best)):
            best = c
    return best


def filing_locations(filing: XbrlFiling, known_cities: Iterable[str]) -> List[Dict[str, str]]:
    """[{type, city, address, source}] from the filing's own address facts."""
    known = list(known_cities)
    out: List[Dict[str, str]] = []
    for concept, label in ADDRESS_CONCEPTS:
        text = filing.text_fact(concept)
        if not text:
            continue
        city = city_in_text(text, known)
        out.append({"type": label, "city": city or "", "address": text[:160], "source": "BRSR XBRL address"})
    return out
