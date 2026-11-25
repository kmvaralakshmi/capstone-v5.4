"""Agent 3 - ESG News Sentiment Agent.

ROLE
    Turn a raw stock-market news corpus into a *reliable* per-company ESG
    reputation signal.

PROBLEMS THIS AGENT DETECTS AND FIXES (found in the earlier engine)
    1. Substring attribution: short codes such as "SIS", "NIIT" or "TITAN" were
       matched inside unrelated words ("analysis", "Titan..."), and every
       "Stocks to Watch: A, B, C ..." round-up was credited to all companies.
       -> word-boundary alias matching; title-level evidence required;
          round-up articles (many tracked companies in the title) are rejected.
    2. Score saturation: VADER run on title+description+full body returns
       compound ~ +/-0.99 for almost any long text, so everything was
       "Positive". -> sentences are scored separately and averaged, using only
       the title and the first sentences of the description.
    3. Over-confidence on tiny samples: 3 articles said "perfect 10/10".
       -> company score is shrunk toward neutral by sample size and a
          reliability value is published with it.

WHAT MAKES IT AN AGENT
    It inspects the corpus, measures how bad each problem is, picks cleaning
    steps in an order driven by those measurements, audits the *distribution*
    of the result (saturation check) and reports coverage gaps to the board.
"""
from __future__ import annotations

import re
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from .common import (BENCH_RAW, COMPANIES, PROCESSED, ROOT, OUTPUT_FILES, clean, np, pd)
from .data_providers import fetch_news, reset_gdelt_circuit, gdelt_last_error
from .framework import BaseAgent, Decision, Tool
import time

from .common import ESG_INCLUDE_KEYWORDS, ESG_EXCLUDE_KEYWORDS

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except Exception:  # pragma: no cover
    SentimentIntensityAnalyzer = None

# Extra finance/ESG-flavoured terms VADER does not know (small, auditable).
ESG_LEXICON = {
    "greenwashing": -3.0, "fine": -1.5, "penalty": -2.0, "violation": -2.5,
    "lawsuit": -2.0, "fraud": -3.2, "scam": -3.0, "probe": -1.5, "layoffs": -2.0,
    "layoff": -2.0, "pollution": -2.2, "emission": -0.8, "breach": -2.2,
    "net zero": 2.0, "renewable": 1.8, "sustainability": 1.5, "sustainable": 1.5,
    "carbon neutral": 2.2, "diversity": 1.2, "inclusion": 1.2, "csr": 1.0,
    "governance": 0.8, "esg": 0.6, "award": 1.8, "recognised": 1.5, "recognized": 1.5,
}
ROUNDUP_PAT = re.compile(
    r"(?:stocks? to (?:watch|buy|sell)|top (?:gainers|losers)|market (?:wrap|live|today|update|outlook)|"
    r"\b(?:nifty|sensex|bse|nse)\b|buzzing stocks|stocks? in (?:focus|news)|trade setup|opening bell|closing bell|"
    r"target price|buy (?:call|rating)|sell (?:call|rating)|brokerage|technical (?:pick|view)|stock market|"
    r"shares? (?:to|that|jump|surge|fall|slip|gain|rally|tank)|share price|q[1-4]\s?fy\d{2}|quarterly (?:results|earnings)|"
    r"dividend (?:declared|record date)|ex-dividend|top \d+ stocks)", re.I)

CATEGORY_WORDS = {
    "Environmental": ["emission", "carbon", "climate", "pollution", "renewable", "solar", "water", "waste",
                      "energy", "net zero", "biodiversity", "sustainab", "effluent", "decarbon"],
    "Social": ["employee", "layoff", "diversity", "inclusion", "gender", "women", "safety", "labour",
               "labor", "csr", "community", "wage", "attrition", "union", "harass", "education",
               "skilling", "student", "school", "teacher"],
    "Governance": ["board", "audit", "fraud", "governance", "bribery", "corruption", "penalty", "fine",
                   "lawsuit", "sebi", "probe", "ethic", "compliance", "whistle", "scam", "insider",
                   "regulation"],
}


def categorize(text: str) -> str:
    t = text.lower()
    best = max(CATEGORY_WORDS, key=lambda k: sum(w in t for w in CATEGORY_WORDS[k]))
    return best if any(w in t for w in CATEGORY_WORDS[best]) else "General"


# An article is ESG-relevant only if it reports an environmental, social or governance FACT. Product
# launches, partnerships, deals, investments and market commentary are business news, whatever an LLM thinks.
ESG_FACT_RE = re.compile(
    r"\b(water[- ]?neutral|emission|carbon|climate|net[- ]?zero|renewable|solar|wind power|energy efficien|water|waste|recycl|"
    r"biodivers|pollut|esg|sustainab|diversity|inclusion|gender|women|workforce|layoff|attrition|labou?r|union|"
    r"wage|safety|injur|fatal|harass|human rights|ethic|corrupt|bribe|fraud|whistle|governance|board|audit|"
    r"penalt|fine[sd]?\b|lawsuit|probe|sebi|complian|breach|privacy|cyber|csr|education|skilling|scholarship|"
    r"student|school|teacher|community|volunteer|social impact|green|regulat\w*)", re.I)
BUSINESS_NOISE_RE = re.compile(
    r"\b(launch(es|ed)?|unveil\w*|partner(s|ship)?|mou|collaborat\w*|invest(s|ing|ment)?|deal|contract|"
    r"order win|wins?|acquir\w*|sponsor\w*|appoint\w*|stock|share price|target price|results?|earnings|"
    r"dividend|buyback|ai[- ]led)\b", re.I)
MARKET_NOISE_RE = re.compile(
    r"\b(msci|rejig|f&o|futures|options|index|inflow|outflow|discretionary spend|"
    r"global environment|shares?|stock|target price|says (?:ceo|chairman)|entrants?|bullish|bearish|"
    r"market outlook|stock picks?)\b", re.I)
NEWS_CLASSIFICATION_BATCH_SIZE = 6


NAME_STOP = {"limited", "ltd", "technologies", "technology", "services", "systems",
             "software", "india", "solutions", "global", "company", "corporation",
             "and", "the", "of", "formerly", "legacy", "symbol"}


def aliases_for(code: str, full_name: str) -> List[str]:
    """Distinctive, word-boundary-safe aliases for a company."""
    out = {full_name.lower()}
    base = re.sub(r"\(.*?\)", "", full_name).strip().lower()
    out.add(base)
    tokens = [t for t in re.split(r"[^a-z0-9&]+", base) if t and t not in NAME_STOP]
    if tokens:
        out.add(" ".join(tokens[:2]) if len(tokens) > 1 else tokens[0])
    if len(code) >= 4 and code.isalpha():
        out.add(code.lower())
    if len(code) <= 3 and code.isalpha() and code in ("TCS",):
        out.add(code.lower())  # TCS is unambiguous in practice
    return sorted(a for a in out if len(a) >= 3)


def _url_context(url: str) -> str:
    try:
        slug = unquote(urlsplit(str(url)).path.rsplit("/", 1)[-1])
    except ValueError:
        return ""
    slug = re.sub(r"\.(?:html?|aspx?)$", "", slug, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", slug)).strip()[:500]


class NewsSentimentAgent(BaseAgent):
    agent_id = "agent3"
    name = "Agent 3 - ESG News Sentiment Agent"
    role = "Build a defensible per-company ESG news sentiment signal."
    goal = ("Corpus inspected, attribution cleaned, articles ESG-filtered and "
            "scored sentence-wise, distribution audited, outputs written.")
    rules = [
        "Inspect the corpus before choosing cleaning steps.",
        "Attribute an article to a company only with title-level or lead-text "
        "evidence using word boundaries; reject market round-ups.",
        "Never report a high score from very few articles - shrink toward neutral.",
        "Audit the final sentiment distribution; if it is saturated, say so.",
    ]

    def __init__(self, *args, companies: Optional[List[str]] = None, **kw):
        self.codes = [c.upper() for c in companies] if companies else list(COMPANIES)
        super().__init__(*args, **kw)
        self.vader = SentimentIntensityAnalyzer() if SentimentIntensityAnalyzer else None
        if self.vader:
            self.vader.lexicon.update(ESG_LEXICON)
        self.memory.update({"corpus": None, "inspected": False, "attributed": None,
                            "scored": None, "audited": False, "finalized": False,
                            "diag": {}})

    # ------------------------------------------------------------------ tools
    def build_tools(self) -> List[Tool]:
        return [
            Tool("load_corpus", "Load the news corpus and report its shape and "
                 "how many articles look like multi-company round-ups.", self.t_load),
            Tool("attribute_articles",
                 "Assign articles to companies with word-boundary aliases; "
                 "reject round-ups. strict=true requires the alias in the title.",
                 self.t_attribute,
                 {"strict": {"type": "boolean"}}, []),
            Tool("score_articles", "ESG-filter attributed articles and score "
                 "sentiment sentence-by-sentence.", self.t_score),
            Tool("audit_distribution", "Check the sentiment distribution for "
                 "saturation and thin coverage.", self.t_audit),
            Tool("finalize", "Write the article-level and company-level CSVs "
                 "and publish coverage warnings.", self.t_finalize),
        ]

    # ----------------------------------------------------------------- stages
    def _path(self):
        f = BENCH_RAW / "news" / "zenodo_esg_news.csv"
        return f if f.exists() else None

    def _load_dynamic(self):
        """GDELT per company (rate-limited ~1 request / 5 s; cached 3 h)."""
        frames, ok_n, failed, no_articles = [], 0, [], []
        reset_gdelt_circuit()
        for i, c in enumerate(self.codes):
            res = fetch_news(COMPANIES[c]["full_name"], tracker=self.tracker, company_code=c)
            if not res.ok and "circuit open" in (res.error or ""):
                failed.extend(self.codes[i:])
                break
            if res.ok and res.payload:
                ok_n += 1
                d = pd.DataFrame(res.payload)
                for col in ("title", "url", "seendate", "domain"):
                    if col not in d:
                        d[col] = ""
                frames.append(pd.DataFrame({"Title": d["title"].fillna("").astype(str), "Description": "",
                                            "Content": "", "Date": pd.to_datetime(d["seendate"], format="%Y%m%dT%H%M%SZ", errors="coerce"),
                                            "URL": d["url"], "Domain": d["domain"], "Query_Company": c}))
            elif res.ok:
                no_articles.append(c)
            else:
                failed.append(c)
            if res.status == "success" and i < len(self.codes) - 1:
                time.sleep(5.2)          # respect GDELT's rate limit
        if not frames:
            return None, {"failed": failed, "no_articles": no_articles}
        df = pd.concat(frames).drop_duplicates(["Title", "Query_Company"]).reset_index(drop=True)
        if self.tracker:
            dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
            self.tracker.record_data_source("news:corpus", {
                "category": "news", "source": "GDELT", "kind": "live",
                "date_min": dates.min().date().isoformat() if not dates.empty else None,
                "date_max": dates.max().date().isoformat() if not dates.empty else None,
                "rows": len(df), "status": "success",
                "note": "; ".join(note for note in (
                    (f"GDELT failed for {len(failed)} company quer{'y' if len(failed) == 1 else 'ies'}"
                     + (f": {gdelt_last_error()}" if gdelt_last_error() else ""))
                    if failed else "",
                    f"GDELT returned no articles for {len(no_articles)} company quer{'y' if len(no_articles) == 1 else 'ies'}"
                    if no_articles else "",
                ) if note),
            })
        return df, {"companies_ok": ok_n, "failed": failed, "no_articles": no_articles}

    def t_load(self) -> Dict[str, Any]:
        if self.dynamic:
            df, info = self._load_dynamic()
            if df is not None and len(df):
                self.memory.update({"corpus": df, "corpus_source": "GDELT"})
                self.memory["diag"]["roundup_like_titles"] = int(df["Title"].str.contains(ROUNDUP_PAT).sum())
                if info["failed"] or info["no_articles"]:
                    self.add_finding("gdelt_gaps", "medium",
                                     f"GDELT failed for {len(info['failed'])} companies and returned no articles for "
                                     f"{len(info['no_articles'])} companies",
                                     companies=info["failed"] + info["no_articles"])
                return {"ok": True, "source": "GDELT", "articles": len(df), **info}
            failure_reason = gdelt_last_error()
            no_articles = bool(info.get("no_articles"))
            self.add_finding("gdelt_unavailable", "high",
                             f"GDELT returned no usable articles; using bundled corpus. "
                             f"Fetch error: {failure_reason or 'none'}")
            if self.tracker:
                self.tracker.mark_fallback("news:fallback_to_bundled_csv", "bundled benchmark news csv",
                                           failure_reason or ("GDELT returned no articles" if no_articles else "no usable GDELT data"))
        self.memory["corpus_source"] = "bundled csv" if not self.dynamic else "bundled csv (GDELT fallback)"
        p = self._path()
        if p is None:
            return {"ok": False, "error": "news corpus not found in data/benchmark/news/"}
        df = pd.read_csv(p)
        for c in ("Title", "Description", "Content"):
            if c not in df.columns:
                df[c] = ""
        df["Title"] = df["Title"].fillna("").astype(str)
        df["Description"] = df["Description"].fillna("").astype(str)
        df["Content"] = df["Content"].fillna("").astype(str)
        df["Date"] = pd.to_datetime(df.get("Date"), errors="coerce")
        roundup = df["Title"].str.contains(ROUNDUP_PAT)
        self.memory["corpus"] = df
        self.memory["diag"]["roundup_like_titles"] = int(roundup.sum())
        if self.tracker:
            dates = df["Date"].dropna()
            note = (("GDELT failed; bundled corpus used: " + gdelt_last_error()
                     if gdelt_last_error() else "GDELT returned no articles; bundled corpus used")
                    if self.dynamic else "Offline mode: bundled news corpus; no network requested")
            self.tracker.record_data_source("news:corpus", {
                "category": "news",
                "source": "bundled news corpus",
                "kind": "bundled" if self.dynamic else "static",
                "date_min": dates.min().date().isoformat() if not dates.empty else None,
                "date_max": dates.max().date().isoformat() if not dates.empty else None,
                "rows": len(df), "status": "fallback" if self.dynamic else "success", "note": note,
            })
        return {"ok": True, "articles": len(df),
                "date_min": str(df["Date"].min().date()) if df["Date"].notna().any() else None,
                "date_max": str(df["Date"].max().date()) if df["Date"].notna().any() else None,
                "roundup_like_titles": int(roundup.sum())}

    def t_attribute(self, strict: bool = True) -> Dict[str, Any]:
        df = self.memory["corpus"]
        pats = {c: re.compile(r"\b(" + "|".join(re.escape(a) for a in
                              aliases_for(c, COMPANIES[c]["full_name"])) + r")\b", re.I)
                for c in self.codes}
        rows, rejected_roundup, rejected_multi = [], 0, 0
        for i, r in df.iterrows():
            title = r["Title"]
            lead = (r["Description"] + " " + r["Content"][:300])
            hits_title = [c for c, p in pats.items() if p.search(title)]
            # market round-ups ("Stocks to Watch Today: A, B, C ...") are rejected whatever the
            # number of tracked companies in the title (a single-company run sees only 1 hit)
            if len(hits_title) > 3 or ROUNDUP_PAT.search(title) or title.count(",") >= 4:
                rejected_roundup += 1
                continue
            if hits_title:
                cands = hits_title
            elif not strict:
                cands = [c for c, p in pats.items() if p.search(lead)]
                if len(cands) != 1:
                    rejected_multi += int(len(cands) > 1)
                    continue
            else:
                continue
            for c in cands:
                rows.append({"idx": i, "Company_Code": c, "Title": title, "URL": r.get("URL", ""),
                             "Domain": r.get("Domain", ""),
                             "Description": r["Description"], "Date": r["Date"],
                             "evidence": "title" if hits_title else "lead"})
        att = pd.DataFrame(rows)
        self.memory["attributed"] = att
        per = att.groupby("Company_Code").size() if not att.empty else pd.Series(dtype=int)
        return {"ok": True, "attributed_rows": len(att), "rejected_roundups": rejected_roundup,
                "rejected_ambiguous": rejected_multi,
                "companies_with_articles": int(per.shape[0]),
                "companies_with_5plus": int((per >= 5).sum())}

    def _relevance(self, text: str) -> Tuple[int, bool, List[str], List[str]]:
        t = text.lower()
        inc = [k for k in ESG_INCLUDE_KEYWORDS if re.search(r"\b" + re.escape(k) + r"\b", t)]
        exc = [k for k in ESG_EXCLUDE_KEYWORDS if re.search(r"\b" + re.escape(k) + r"\b", t)]
        score = len(inc) - len(exc)
        return score, (len(inc) >= 1 and score >= 1), inc, exc

    def _sentiment(self, title: str, description: str) -> float:
        sents = [title] + re.split(r"(?<=[.!?])\s+", description)[:3]
        vals = [self.vader.polarity_scores(s)["compound"] for s in sents if s.strip()]
        return float(np.mean(vals)) if vals else 0.0

    @staticmethod
    def _normalise_quote(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()

    def _classification_evidence(self, code: str, title: str, description: str,
                                 url: str, row_index: int = 0, article_date: Any = "") -> Dict[str, Any]:
        context = _url_context(url)
        key = f"{code}|{str(article_date)[:10]}|{row_index}|{title[:80]}"
        return {"key": key, "company": COMPANIES[code]["full_name"], "title": title[:300],
                "snippet": description[:500], "url_path_context": context}

    def _verify_classification(self, answer: Dict[str, Any], evidence: Dict[str, Any]):
        relevant = answer.get("relevant")
        company_is_subject = answer.get("company_is_subject")
        if not isinstance(relevant, bool):
            return False, None, "'relevant' must be true or false"
        if not isinstance(company_is_subject, bool):
            return False, None, "'company_is_subject' must be true or false"
        rationale = answer.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            return False, None, "rationale must explain why the article passed or failed"
        rationale = rationale.strip()[:300]
        quote = str(answer.get("evidence_quote", "")).strip()
        pillar, polarity, nature = answer.get("pillar"), answer.get("polarity"), answer.get("nature")
        source = ""
        if relevant:
            if not company_is_subject:
                return False, None, "a relevant article must identify the company as its subject"
            context = " ".join((evidence["title"], evidence["snippet"], evidence["url_path_context"]))
            if not ESG_FACT_RE.search(context):
                return False, None, "no specific ESG fact or claim appears in title, snippet, or URL path"
            if pillar not in ("Environmental", "Social", "Governance"):
                return False, None, "pillar must be Environmental, Social or Governance for a relevant article"
            if polarity not in ("negative", "neutral", "positive"):
                return False, None, "polarity must be negative, neutral or positive"
            if nature not in ("allegation", "verified_finding", "company_announcement"):
                return False, None, "relevant nature must be allegation, verified_finding or company_announcement"
            quote_norm = self._normalise_quote(quote)
            if len(quote_norm) < 8:
                return False, None, "evidence_quote must contain at least 8 characters"
            for name in ("title", "snippet", "url_path_context"):
                if quote_norm in self._normalise_quote(evidence[name]):
                    source = {"title": "title", "snippet": "snippet", "url_path_context": "url_path"}[name]
                    break
            if not source:
                return False, None, "evidence_quote must match the title, snippet, or disclosed URL path context"
        else:
            pillar = pillar if pillar in ("Environmental", "Social", "Governance") else None
            polarity = polarity if polarity in ("negative", "neutral", "positive") else "neutral"
            nature = nature if nature in ("allegation", "verified_finding", "company_announcement", "other") else "other"
        return True, {"relevant": relevant, "pillar": pillar, "polarity": polarity, "nature": nature,
                      "evidence_quote": quote[:200], "evidence_source": source,
                      "company_is_subject": company_is_subject, "rationale": rationale}, ""

    @staticmethod
    def _rule_classification(rule: Dict[str, Any]) -> Dict[str, Any]:
        compound = rule["compound"]
        relevant = bool(rule["ok"])
        return {"relevant": relevant,
                "pillar": rule["category"] if rule["category"] != "General" else None,
                "polarity": "positive" if compound >= 0.15 else "negative" if compound <= -0.15 else "neutral",
                "nature": "company_announcement" if relevant else "other",
                "evidence_quote": "", "evidence_source": "rule_keywords" if relevant else "",
                "company_is_subject": True,
                "rationale": ("deterministic ESG keyword match" if relevant
                              else "no specific ESG keyword matched the title, snippet, or URL path")}

    def _classify_batch(self, candidates: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        if not candidates:
            return {}
        articles = [item["evidence"] for item in candidates]
        evidence = {"key": f"news-batch:{articles[0]['key']}", "articles": articles}
        by_key = {article["key"]: item for item, article in zip(candidates, articles)}

        def verify(answer, package):
            outputs = answer.get("items")
            if not isinstance(outputs, list) or len(outputs) != len(package["articles"]):
                return False, None, "items must contain exactly one result per input article"
            clean, seen = [], set()
            for output in outputs:
                key = output.get("key") if isinstance(output, dict) else None
                if key not in by_key or key in seen:
                    return False, None, "each input article key must appear exactly once"
                ok, result, why = self._verify_classification(output, by_key[key]["evidence"])
                if not ok:
                    return False, None, f"{key}: {why}"
                result["key"] = key
                clean.append(result)
                seen.add(key)
            if seen != set(by_key):
                return False, None, "one or more input article keys are missing"
            return True, {"items": clean}, ""

        def fallback(_package):
            return {"items": [{"key": item["evidence"]["key"],
                               **self._rule_classification(item["rule"])} for item in candidates]}

        instructions = (
            'Return JSON {"items":[{"key":"input key","relevant":bool,"company_is_subject":bool,'
            '"pillar":"Environmental|Social|Governance|null","polarity":"negative|neutral|positive",'
            '"nature":"allegation|verified_finding|company_announcement|other",'
            '"evidence_quote":"verbatim phrase from supplied text or URL path","rationale":"short reason"}]}. '
            "Return exactly one item for every input key. Decide whether the item reports a specific ESG fact, "
            "claim, or initiative about the named company. Environmental examples include emissions, renewable "
            "energy, water neutrality or water use, and climate impacts; social examples include workforce "
            "conditions, safety, and concrete education/skilling/CSR programs with beneficiaries; governance "
            "examples include board/audit matters, legal findings, penalties, and regulatory compliance. "
            "A specific company announcement about such an initiative can be relevant. A product launch, "
            "commercial partnership, investment, contract, sponsorship, stock roundup, or market opinion alone "
            "is not ESG. URL path context is a noisy headline clue, not article body text; if it is the only "
            "source for your quote, record the exact URL-path words. Do not invent details. Use nature=other "
            "and relevant=false when the evidence is only commercial or generic."
        )
        answer = self.judge("classify a batch of company news articles for ESG analysis",
                            instructions, evidence, verify, fallback)
        out = {}
        for item in answer["items"]:
            key = item["key"]
            out[key] = {**item, "_source": answer["_source"], "_reason": answer["_reason"]}
            if answer["_source"] == "rule_fallback" and answer.get("_reason", "").startswith("rejected:"):
                out[key]["rationale"] = f"{item.get('rationale', '')}; model response rejected: {answer['_reason'][9:]}"
        return out

    def _classify(self, code: str, title: str, description: str, rule: Dict[str, Any],
                  url: str = "", article_date: Any = "") -> Dict[str, Any]:
        evidence = self._classification_evidence(code, title, description, url, article_date=article_date)
        instructions = (
            'Return JSON {"relevant":bool,"company_is_subject":bool,'
            '"pillar":"Environmental|Social|Governance|null","polarity":"negative|neutral|positive",'
            '"nature":"allegation|verified_finding|company_announcement|other",'
            '"evidence_quote":"verbatim phrase from supplied text or URL path","rationale":"short reason"}. '
            "Relevant means a specific ESG fact, claim, or initiative about the named company. A concrete "
            "education/skilling/CSR program with beneficiaries is Social; a specific company water-neutrality "
            "or emissions claim is Environmental. A commercial announcement alone is not ESG. URL path context "
            "is a noisy clue, not article body text; do not invent facts."
        )
        return self.judge(
            "classify one company news article for ESG analysis", instructions, evidence,
            lambda answer, package: self._verify_classification(answer, package),
            lambda _package: self._rule_classification(rule),
        )

    def t_score(self) -> Dict[str, Any]:
        if self.vader is None:
            return {"ok": False, "error": "vaderSentiment is not installed"}
        att = self.memory["attributed"]
        keep, drop, general, candidates, decisions = [], [], [], [], {}
        POL = {"negative": -0.5, "neutral": 0.0, "positive": 0.5}
        for row_index, r in att.iterrows():
            url = str(r.get("URL", ""))
            url_context = _url_context(url)
            text = f"{r['Title']}. {r['Description']} {url_context}"
            sc, ok, inc, exc = self._relevance(text)
            comp = self._sentiment(r["Title"], r["Description"])
            general.append({"Company_Code": r["Company_Code"], "score": comp})
            has_esg = bool(ESG_FACT_RE.search(text)) or ok
            rule = {"ok": ok and has_esg, "compound": comp, "category": categorize(text)}
            evidence = self._classification_evidence(
                r["Company_Code"], str(r["Title"]), str(r["Description"]), url, row_index, r.get("Date", ""))
            candidate = {"evidence": evidence, "rule": rule, "row": r,
                         "score": sc, "include": inc, "exclude": exc, "context": url_context,
                         "text": text}
            if (BUSINESS_NOISE_RE.search(r["Title"]) or MARKET_NOISE_RE.search(r["Title"])) and not has_esg:
                candidate["classification"] = {
                    "relevant": False, "pillar": None, "polarity": "neutral", "nature": "other",
                    "evidence_quote": "", "evidence_source": "", "company_is_subject": True,
                    "rationale": "commercial or market headline with no specific ESG cue",
                    "_source": "rule_guard", "_reason": "deterministic business/market guard",
                }
                decisions[evidence["key"]] = candidate
            else:
                candidates.append(candidate)
                decisions[evidence["key"]] = candidate
        classified = {}
        for start in range(0, len(candidates), NEWS_CLASSIFICATION_BATCH_SIZE):
            classified.update(self._classify_batch(candidates[start:start + NEWS_CLASSIFICATION_BATCH_SIZE]))
        for key, candidate in decisions.items():
            cls = candidate.get("classification") or classified[key]
            if not cls.get("company_is_subject", True) or str(cls.get("nature", "other")) == "other":
                cls = {**cls, "relevant": False}
            r = candidate["row"]
            base = {"Company_Code": r["Company_Code"],
                    "Company_Name": COMPANIES[r["Company_Code"]]["full_name"],
                    "Article_Title": r["Title"][:200], "Article_Date": r["Date"],
                    "ESG_Relevance_Score": candidate["score"], "Is_ESG_Relevant": bool(cls["relevant"]),
                    "Matched_ESG_Keywords": ", ".join(candidate["include"]),
                    "Matched_Non_ESG_Keywords": ", ".join(candidate["exclude"]),
                    "Attribution_Evidence": r["evidence"],
                    "ESG_Category": cls["pillar"] or categorize(candidate["text"]),
                    "URL": r.get("URL", ""), "Domain": r.get("Domain", ""),
                    "Judgement_Source": cls["_source"], "Nature": cls["nature"], "Evidence_Quote": cls["evidence_quote"],
                    "Evidence_Source": cls.get("evidence_source", ""),
                    "URL_Path_Context": candidate["context"],
                    "Classifier_Rationale": cls.get("rationale", ""),
                    "Analysis_Date": date.today().isoformat()}
            if not cls["relevant"]:
                reason = cls.get("rationale") or "classifier did not identify a specific ESG fact or claim"
                drop.append({**base, "Rejection_Reason": reason, "Retain_Reason": ""})
            else:
                vs = self.vader.polarity_scores(r["Title"])
                score = POL[cls["polarity"]] if cls["_source"] == "llm" else candidate["rule"]["compound"]
                keep.append({**base, "Sentiment_Score": round(score, 4),
                             "Sentiment_Score_Rule": round(candidate["rule"]["compound"], 4),
                             "Sentiment_Class": cls["polarity"].capitalize(),
                             "Retain_Reason": f"Specific ESG fact; Nature={cls['nature']}",
                             "Positive_Score": vs["pos"], "Negative_Score": vs["neg"],
                             "Neutral_Score": vs["neu"]})
            if os.getenv("NEWS_ESG_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on"):
                title = re.sub(r"\s+", " ", str(r["Title"]))[:180]
                reason = re.sub(r"\s+", " ", str(cls.get("rationale", "")))[:220]
                self.log(f"candidate {key}: relevant={bool(cls['relevant'])} source={cls['_source']} "
                         f"title={title!r} reason={reason!r}")
        self.memory["scored"] = pd.DataFrame(keep)
        self.memory["rejected"] = pd.DataFrame(drop)
        self.memory["general"] = pd.DataFrame(general)
        return {"ok": True, "candidates": len(att), "esg_relevant": len(keep), "rejected_not_esg": len(drop),
                "judgements": self._judgement_counts()}

    def t_audit(self) -> Dict[str, Any]:
        s = self.memory["scored"]
        if s is None or s.empty:
            self.memory["audited"] = True
            return {"ok": True, "warning": "no scored articles"}
        extreme = float((s["Sentiment_Score"].abs() > 0.9).mean())
        classes = s["Sentiment_Class"].value_counts(normalize=True).round(3).to_dict()
        per = s.groupby("Company_Code").size()
        thin = sorted(set(self.codes) - set(per[per >= 5].index))
        self.memory["diag"].update({"extreme_share": extreme, "classes": classes,
                                    "thin_companies": thin})
        self.memory["audited"] = True
        return {"ok": True, "share_abs_gt_0.9": round(extreme, 3), "class_mix": classes,
                "mean": round(float(s["Sentiment_Score"].mean()), 3),
                "std": round(float(s["Sentiment_Score"].std()), 3),
                "companies_below_5_articles": len(thin)}

    def t_finalize(self) -> Dict[str, Any]:
        s, rej = self.memory["scored"], self.memory["rejected"]
        rows, summary = [], []
        for c in self.codes:
            sub = s[s["Company_Code"] == c] if s is not None and not s.empty else pd.DataFrame()
            gen = self.memory.get("general")
            g = gen[gen["Company_Code"] == c]["score"] if gen is not None and not gen.empty else pd.Series(dtype=float)
            g_n = len(g)
            g_shrunk = float(g.mean()) * g_n / (g_n + 5.0) if g_n else None
            if sub.empty:
                summary.append({"Company_Code": c, "Articles": 0, "Raw_Sentiment": None,
                                "Shrunk_Sentiment": None, "Sentiment_Reliability": 0.0,
                                "News_Sentiment_Score_10": None,
                                "General_Articles": g_n,
                                "General_Sentiment_Shrunk": None if g_shrunk is None else round(g_shrunk, 4)})
                continue
            n = len(sub)
            raw = float(sub["Sentiment_Score"].mean())
            rel = n / (n + 5.0)
            shrunk = raw * rel                       # shrink toward neutral (0)
            summary.append({"Company_Code": c, "Articles": n, "Raw_Sentiment": round(raw, 4),
                            "Shrunk_Sentiment": round(shrunk, 4),
                            "Sentiment_Reliability": round(rel, 3),
                            "Positive_Share": round(float((sub["Sentiment_Class"] == "Positive").mean()), 3),
                            "Negative_Share": round(float((sub["Sentiment_Class"] == "Negative").mean()), 3),
                            "News_Sentiment_Score_10": round(5 + 5 * shrunk, 2),
                            "General_Articles": g_n,
                            "General_Sentiment_Shrunk": None if g_shrunk is None else round(g_shrunk, 4),
                            "Environmental_Events": int((sub["ESG_Category"] == "Environmental").sum()),
                            "Social_Events": int((sub["ESG_Category"] == "Social").sum()),
                            "Governance_Events": int((sub["ESG_Category"] == "Governance").sum()),
                            "Negative_Events": int((sub["Sentiment_Class"] == "Negative").sum()),
                            "Corpus_Source": self.memory.get("corpus_source")})
        ps = PROCESSED / OUTPUT_FILES["news_sentiment"]
        (s if s is not None else pd.DataFrame()).to_csv(ps, index=False)
        (rej if rej is not None else pd.DataFrame()).to_csv(PROCESSED / OUTPUT_FILES["news_rejected"], index=False)
        pd.DataFrame(summary).to_csv(PROCESSED / "esg_news_company_summary.csv", index=False)
        thin = [r["Company_Code"] for r in summary if r["Articles"] < 5]
        self.board.post(self.agent_id, "news_coverage",
                        {"companies_below_5_articles": thin,
                         "extreme_share": self.memory["diag"].get("extreme_share")})
        self.memory["finalized"] = True
        self.artifacts.append(str(ps.relative_to(ROOT)))
        return {"ok": True, "articles_scored": 0 if s is None else len(s),
                "companies_with_5plus_articles": len(self.codes) - len(thin)}

    # --------------------------------------------------------------- reasoning
    def observe(self) -> Dict[str, Any]:
        m = self.memory
        return {"corpus_loaded": m["corpus"] is not None,
                "attributed": m["attributed"] is not None,
                "scored": m["scored"] is not None, "audited": m["audited"],
                "finalized": m["finalized"], "diagnostics": m["diag"]}

    def is_done(self, state) -> Tuple[bool, str]:
        if state["finalized"]:
            return True, "news sentiment written with reliability and shrinkage"
        return False, ""

    def policy(self, state) -> Optional[Decision]:
        if not state["corpus_loaded"]:
            return Decision("load_corpus", {}, "inspect the corpus first")
        if not state["attributed"]:
            return Decision("attribute_articles", {"strict": True},
                            "title-level attribution avoids false credit")
        if not state["scored"]:
            return Decision("score_articles", {}, "score relevant attributed articles")
        if not state["audited"]:
            return Decision("audit_distribution", {}, "verify the score distribution")
        return Decision("finalize", {}, "write outputs")

    def reflect(self, decision: Decision, result: Dict[str, Any]) -> None:
        if decision.tool == "attribute_articles" and result.get("ok") \
                and result.get("companies_with_5plus", 0) < len(self.codes) * 0.4 \
                and not self.memory.get("relaxed"):
            # Too few companies have usable evidence: relax to lead-text
            # attribution once and re-run, recording that it was relaxed.
            self.memory["relaxed"] = True
            self.memory["attributed"] = None
            self.add_finding("relaxed_attribution", "low",
                             "strict title attribution left <40% of companies with 5+ "
                             "articles; relaxed to lead-text attribution")
            self._relax_next = True
        if decision.tool == "audit_distribution" and result.get("share_abs_gt_0.9", 0) > 0.5:
            self.add_finding("saturated_sentiment", "high",
                             "over half of article scores are extreme (|s|>0.9)")

    def _choose(self, state):
        d, e = super()._choose(state)
        if d and d.tool == "attribute_articles" and getattr(self, "_relax_next", False):
            d.args = {"strict": False}
            self._relax_next = False
        return d, e
