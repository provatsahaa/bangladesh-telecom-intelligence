"""Bilingual (English + Bangla) telecom relevance filter and entity extractor.

Uses weighted keyword/phrase matching across the six target telecom domains plus
canonical entity normalization for Bangladesh's telecom ecosystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical entity -> (English aliases, Bangla aliases)
ENTITY_ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # Mobile Network Operators
    "Grameenphone": (
        ("Grameenphone", "GP", "MyGP", "Skitto"),
        ("গ্রামীণফোন", "জিপি", "মাইজিপি", "স্কিটো"),
    ),
    "Robi": (
        ("Robi", "Robi Axiata", "Airtel Bangladesh", "Airtel"),
        ("রবি", "রবি আজিয়াটা", "এয়ারটেল"),
    ),
    "Banglalink": (
        ("Banglalink", "Banglalink Digital", "Toffee", "MyBL", "RYZE"),
        ("বাংলালিংক", "বাংলালিংক ডিজিটাল", "টফি", "মাইবিএল"),
    ),
    "Teletalk": (
        ("Teletalk", "Teletalk Bangladesh"),
        ("টেলিটক", "টেলিটক বাংলাদেশ"),
    ),
    # State & Infrastructure Operators
    "BTCL": (
        ("BTCL", "Bangladesh Telecommunications Company"),
        ("বিটিসিএল",),
    ),
    "BSCCL": (
        ("BSCCL", "BSCPLC", "Bangladesh Submarine Cable", "SEA-ME-WE"),
        ("বিএসসিসিএল", "সাবমেরিন ক্যাবল", "সাবমেরিন কেবল"),
    ),
    # Regulators & Industry Bodies
    "BTRC": (
        ("BTRC", "Bangladesh Telecommunication Regulatory Commission", "telecom regulator"),
        ("বিটিআরসি", "বাংলাদেশ টেলিযোগাযোগ নিয়ন্ত্রণ কমিশন"),
    ),
    "PTD / MoPTIT": (
        ("Posts and Telecommunications Division", "MoPTIT", "Telecom Ministry", "ICT Division"),
        ("ডাক ও টেলিযোগাযোগ বিভাগ", "টেলিযোগাযোগ মন্ত্রণালয়", "তথ্য ও যোগাযোগ প্রযুক্তি বিভাগ"),
    ),
    "AMTOB": (
        ("AMTOB", "Association of Mobile Telecom Operators of Bangladesh"),
        ("অ্যামটব", "এমটব"),
    ),
    "ISPAB": (
        ("ISPAB", "Internet Service Providers Association of Bangladesh"),
        ("আইএসপিএবি",),
    ),
    # Parent Groups
    "Telenor": (("Telenor", "Telenor Asia"), ("টেলিনর",)),
    "Axiata": (("Axiata", "Axiata Group"), ("আজিয়াটা",)),
    "VEON": (("VEON", "VEON Group"), ("ভিয়ন", "ভিওন")),
    # TowerCos & NTTN
    "Edotco Bangladesh": (("Edotco", "Edotco Bangladesh"), ("ইডটকো",)),
    "Summit Towers": (("Summit Towers", "Summit Tower"), ("সামিট টাওয়ার্স", "সামিট টাওয়ার")),
    "Summit Communications": (("Summit Communications",), ("সামিট কমিউনিকেশনস",)),
    "Fiber@Home": (("Fiber@Home", "Fiber at Home"), ("ফাইবার অ্যাট হোম",)),
    "Kirtonkhola Tower": (("Kirtonkhola Tower",), ("কীর্তনখোলা টাওয়ার",)),
    "Frontier Towers": (("Frontier Towers",), ("ফ্রন্টিয়ার টাওয়ার্স",)),
    # Satellite & Global Telecom Vendors
    "Starlink": (("Starlink",), ("স্টারলিংক",)),
    "Huawei": (("Huawei",), ("হুয়াওয়ে",)),
    "Ericsson": (("Ericsson",), ("এরিকসন",)),
    "Nokia": (("Nokia",), ("নকিয়া",)),
    "ZTE": (("ZTE",), ("জেডটিই",)),
    # Digital Services / MFS (tracked when article passes telecom filter)
    "bKash": (("bKash",), ("বিকাশ",)),
    "Nagad": (("Nagad",), ("নগদ",)),
    "Rocket": (("Dutch-Bangla Rocket", "Rocket MFS"), ("রকেট",)),
    "Upay": (("Upay",), ("উপায়",)),
}

# Short/ambiguous tokens requiring exact case-sensitive or strict word-boundary match
_CASE_SENSITIVE_SHORT = {"GP", "ZTE", "RYZE"}

# Topic buckets with weighted bilingual keywords: (term, weight, topic)
TOPIC_RULES: tuple[tuple[str, float, str], ...] = (
    # --- Mobile Network Operators (High weight: 3.0) ---
    ("grameenphone", 3.0, "operators"),
    ("গ্রামীণফোন", 3.0, "operators"),
    ("banglalink", 3.0, "operators"),
    ("বাংলালিংক", 3.0, "operators"),
    ("robi axiata", 3.0, "operators"),
    ("রবি আজিয়াটা", 3.0, "operators"),
    ("teletalk", 3.0, "operators"),
    ("টেলিটক", 3.0, "operators"),
    ("mobile operator", 3.0, "operators"),
    ("telecom operator", 3.0, "operators"),
    ("মোবাইল অপারেটর", 3.0, "operators"),
    ("টেলিকম অপারেটর", 3.0, "operators"),
    ("skitto", 2.5, "operators"),
    ("স্কিটো", 2.5, "operators"),
    ("mygp", 2.5, "operators"),
    ("mybl", 2.5, "operators"),
    ("airtel bangladesh", 2.5, "operators"),
    # --- Regulation, BTRC & Spectrum (High weight: 3.0) ---
    ("btrc", 3.0, "regulation_btrc"),
    ("বিটিআরসি", 3.0, "regulation_btrc"),
    ("বাংলাদেশ টেলিযোগাযোগ নিয়ন্ত্রণ কমিশন", 3.0, "regulation_btrc"),
    ("telecom regulator", 3.0, "regulation_btrc"),
    ("spectrum auction", 3.0, "regulation_btrc"),
    ("spectrum allocation", 3.0, "regulation_btrc"),
    ("তরঙ্গ নিলাম", 3.0, "regulation_btrc"),
    ("তরঙ্গ বরাদ্দ", 3.0, "regulation_btrc"),
    ("স্পেকট্রাম", 2.5, "regulation_btrc"),
    ("unified license", 3.0, "regulation_btrc"),
    ("ইউনিফাইড লাইসেন্স", 3.0, "regulation_btrc"),
    ("sim tax", 3.0, "regulation_btrc"),
    ("telecom tax", 3.0, "regulation_btrc"),
    ("সিম কর", 3.0, "regulation_btrc"),
    ("সিম ট্যাক্স", 3.0, "regulation_btrc"),
    ("amtob", 3.0, "regulation_btrc"),
    ("অ্যামটব", 3.0, "regulation_btrc"),
    ("number portability", 2.5, "regulation_btrc"),
    ("mnp", 2.0, "regulation_btrc"),
    ("telecommunication act", 2.5, "regulation_btrc"),
    ("টেলিযোগাযোগ আইন", 2.5, "regulation_btrc"),
    ("টেলিযোগাযোগ মন্ত্রণালয়", 2.5, "regulation_btrc"),
    ("ডাক ও টেলিযোগাযোগ", 2.5, "regulation_btrc"),
    ("posts and telecommunications", 2.5, "regulation_btrc"),
    # --- Infrastructure, 2G/3G/4G/5G/6G, RAN, Core & Vendors ---
    ("6g", 2.0, "infrastructure_5g"),
    ("৬জি", 2.5, "infrastructure_5g"),
    ("5g", 2.0, "infrastructure_5g"),
    ("৫জি", 2.5, "infrastructure_5g"),
    ("ফাইভজি", 2.5, "infrastructure_5g"),
    ("4g", 2.0, "infrastructure_5g"),
    ("৪জি", 2.5, "infrastructure_5g"),
    ("ফোরজি", 2.5, "infrastructure_5g"),
    ("3g", 1.5, "infrastructure_5g"),
    ("৩জি", 2.0, "infrastructure_5g"),
    ("2g", 1.5, "infrastructure_5g"),
    ("২জি", 2.0, "infrastructure_5g"),
    ("lte", 1.5, "infrastructure_5g"),
    ("ran", 1.5, "infrastructure_5g"),
    ("open ran", 2.5, "infrastructure_5g"),
    ("core network", 2.5, "infrastructure_5g"),
    ("network expansion", 2.0, "infrastructure_5g"),
    ("service disruption", 2.0, "infrastructure_5g"),
    ("huawei", 2.0, "telecom_vendors"),
    ("হুয়াওয়ে", 2.0, "telecom_vendors"),
    ("ericsson", 2.5, "telecom_vendors"),
    ("এরিকসন", 2.5, "telecom_vendors"),
    ("nokia", 2.0, "telecom_vendors"),
    ("নকিয়া", 2.0, "telecom_vendors"),
    ("zte", 2.0, "telecom_vendors"),
    ("জেডটিই", 2.0, "telecom_vendors"),
    ("customer experience", 1.0, "market_competition"),
    ("market share", 1.25, "market_competition"),
    ("telecom tower", 3.0, "infrastructure_5g"),
    ("mobile tower", 3.0, "infrastructure_5g"),
    ("tower sharing", 3.0, "infrastructure_5g"),
    ("মোবাইল টাওয়ার", 3.0, "infrastructure_5g"),
    ("টাওয়ার শেয়ারিং", 3.0, "infrastructure_5g"),
    ("nttn", 2.5, "infrastructure_5g"),
    ("submarine cable", 2.5, "infrastructure_5g"),
    ("সাবমেরিন ক্যাবল", 2.5, "infrastructure_5g"),
    ("সাবমেরিন কেবল", 2.5, "infrastructure_5g"),
    ("sea-me-we", 2.5, "infrastructure_5g"),
    ("call drop", 2.5, "infrastructure_5g"),
    ("কল ড্রপ", 2.5, "infrastructure_5g"),
    ("কলড্রপ", 2.5, "infrastructure_5g"),
    ("starlink", 2.5, "infrastructure_5g"),
    ("স্টারলিংক", 2.5, "infrastructure_5g"),
    ("satellite internet", 2.5, "infrastructure_5g"),
    ("edotco", 2.5, "infrastructure_5g"),
    ("fiber@home", 2.5, "infrastructure_5g"),
    ("summit communications", 2.5, "infrastructure_5g"),
    ("summit towers", 2.5, "infrastructure_5g"),
    ("network outage", 2.5, "infrastructure_5g"),
    ("নেটওয়ার্ক বিভ্রাট", 2.5, "infrastructure_5g"),
    ("fiber optic", 1.5, "infrastructure_5g"),
    ("ফাইবার অপটিক", 1.5, "infrastructure_5g"),
    ("base transceiver", 2.0, "infrastructure_5g"),
    # --- Investments, Partnerships, M&A ---
    ("telecom", 2.0, "industry_investment"),
    ("telecommunications", 2.0, "industry_investment"),
    ("টেলিকম", 2.5, "industry_investment"),
    ("টেলিযোগাযোগ", 2.5, "industry_investment"),
    ("telenor", 2.5, "industry_investment"),
    ("টেলিনর", 2.5, "industry_investment"),
    ("axiata", 2.5, "industry_investment"),
    ("আজিয়াটা", 2.5, "industry_investment"),
    ("veon", 2.5, "industry_investment"),
    ("ভিয়ন", 2.5, "industry_investment"),
    ("btcl", 2.5, "industry_investment"),
    ("বিটিসিএল", 2.5, "industry_investment"),
    ("bsccl", 2.5, "industry_investment"),
    ("বিএসসিসিএল", 2.5, "industry_investment"),
    # --- Broadband, Connectivity & Digital/Fintech Services ---
    ("broadband", 2.0, "broadband_digital"),
    ("ব্রডব্যান্ড", 2.5, "broadband_digital"),
    ("mobile internet", 2.5, "broadband_digital"),
    ("মোবাইল ইন্টারনেট", 2.5, "broadband_digital"),
    ("internet speed", 2.0, "broadband_digital"),
    ("ইন্টারনেট গতি", 2.0, "broadband_digital"),
    ("internet shutdown", 2.5, "broadband_digital"),
    ("ইন্টারনেট বন্ধ", 2.0, "broadband_digital"),
    ("ispab", 2.5, "broadband_digital"),
    ("আইএসপিএবি", 2.5, "broadband_digital"),
    ("isp", 1.5, "broadband_digital"),
    ("iig", 1.5, "broadband_digital"),
    ("international bandwidth", 2.0, "broadband_digital"),
    ("আন্তর্জাতিক ব্যান্ডউইথ", 2.0, "broadband_digital"),
    ("data package", 2.0, "broadband_digital"),
    ("data pack", 2.0, "broadband_digital"),
    ("ডাটা প্যাকেজ", 2.0, "broadband_digital"),
    ("ডেটা প্যাকেজ", 2.0, "broadband_digital"),
    ("mobile financial service", 2.0, "broadband_digital"),
    ("mfs", 1.5, "broadband_digital"),
    ("ussd", 2.0, "broadband_digital"),
    ("toffee", 1.5, "broadband_digital"),
    ("bkash", 1.0, "broadband_digital"),
    ("nagad", 1.0, "broadband_digital"),
    ("বিকাশ", 1.0, "broadband_digital"),
    # --- Telecom Tech, AI & Trends ---
    ("esim", 2.5, "telecom_tech_ai"),
    ("e-sim", 2.5, "telecom_tech_ai"),
    ("sim card", 2.0, "telecom_tech_ai"),
    ("সিম কার্ড", 2.0, "telecom_tech_ai"),
    ("roaming", 1.5, "telecom_tech_ai"),
    ("রোমিং", 1.5, "telecom_tech_ai"),
    ("arpu", 2.0, "telecom_tech_ai"),
    ("qos", 1.5, "telecom_tech_ai"),
    ("interconnection", 1.5, "telecom_tech_ai"),
    ("iot", 1.0, "telecom_tech_ai"),
    ("artificial intelligence", 0.75, "telecom_tech_ai"),
    ("ai", 0.5, "telecom_tech_ai"),
    ("bandwidth", 1.25, "telecom_tech_ai"),
    ("ব্যান্ডউইথ", 1.5, "telecom_tech_ai"),
    ("subscriber", 1.0, "telecom_tech_ai"),
    ("গ্রাহক সংখ্যা", 1.0, "telecom_tech_ai"),
    ("tariff", 1.0, "telecom_tech_ai"),
    ("ট্যারিফ", 1.0, "telecom_tech_ai"),
    ("spectrum", 1.5, "regulation_btrc"),
    ("robi", 1.5, "operators"),
)

# High-signal URL slug tokens for fast pre-filtering of large XML sitemaps
URL_SLUG_HINTS: tuple[str, ...] = (
    "telecom", "grameenphone", "banglalink", "robi", "teletalk", "airtel",
    "btrc", "btcl", "bsccl", "amtob", "ispab", "starlink", "edotco",
    "spectrum", "5g", "4g", "broadband", "internet", "mobile-operator",
    "sim-", "call-drop", "submarine-cable", "bandwidth", "telenor",
    "axiata", "veon", "fiber", "tower", "esim", "bkash", "nagad", "mfs",
    "huawei", "ericsson", "nokia", "satellite", "digital-bank", "toffee",
    "skitto", "roaming", "tariff", "data-pack", "nttn", "isp", "connectivity",
)


def _is_ascii_word(term: str) -> bool:
    return all(ord(ch) < 128 for ch in term)


def _compile_term_pattern(term: str, case_sensitive: bool = False) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    escaped = re.escape(term)
    if _is_ascii_word(term):
        # Strict word boundaries for ASCII terms (prevents matching 'ai' in 'daily' or '4g' in '4gb' wait: allow 4G/5G boundaries)
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", flags)
    # Bangla / Unicode substring match (Bangla suffixes attach directly to stems like গ্রামীণফোনের)
    return re.compile(escaped, flags)


_COMPILED_TOPIC_RULES: list[tuple[re.Pattern[str], str, float, str]] = [
    (_compile_term_pattern(term), term, weight, topic)
    for term, weight, topic in TOPIC_RULES
]

_COMPILED_ENTITY_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = []
for _canonical, (_en_aliases, _bn_aliases) in ENTITY_ALIASES.items():
    _patterns: list[re.Pattern[str]] = []
    for _alias in _en_aliases:
        _patterns.append(_compile_term_pattern(_alias, case_sensitive=_alias in _CASE_SENSITIVE_SHORT))
    for _alias in _bn_aliases:
        _patterns.append(_compile_term_pattern(_alias, case_sensitive=False))
    _COMPILED_ENTITY_PATTERNS.append((_canonical, _patterns))


@dataclass
class RelevanceResult:
    """Result of evaluating an article for Bangladesh telecom relevance."""

    is_relevant: bool
    score: float
    matched_topics: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)


def extract_entities(text: str) -> list[str]:
    """Extract canonical Bangladesh telecom entities/companies mentioned in text."""
    if not text:
        return []
    found: list[str] = []
    for canonical, patterns in _COMPILED_ENTITY_PATTERNS:
        if any(p.search(text) for p in patterns):
            found.append(canonical)
    return found


def evaluate_relevance(
    headline: str,
    summary: str = "",
    content: str = "",
    *,
    threshold: float = 2.0,
) -> RelevanceResult:
    """Score an article's telecom relevance across headline, summary, and content.

    Headline matches receive a 1.5x boost so telecom-focused articles rank clearly
    above passing mentions.
    """
    headline_text = headline or ""
    body_text = f"{summary or ''}\n{content or ''}"
    full_text = f"{headline_text}\n{body_text}"

    score = 0.0
    topics: set[str] = set()
    keywords: list[str] = []

    for pattern, term, weight, topic in _COMPILED_TOPIC_RULES:
        in_headline = bool(pattern.search(headline_text))
        in_body = bool(pattern.search(body_text)) if not in_headline else False
        if in_headline:
            score += weight * 1.5
            topics.add(topic)
            keywords.append(term)
        elif in_body:
            score += weight
            topics.add(topic)
            keywords.append(term)

    entities = extract_entities(full_text)
    is_relevant = score >= threshold
    return RelevanceResult(
        is_relevant=is_relevant,
        score=round(score, 2),
        matched_topics=sorted(topics),
        matched_keywords=keywords[:20],
        entities=entities,
    )


_URL_SLUG_HINT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(rf"(?<![a-z0-9]){re.escape(hint)}(?![a-z0-9])", re.IGNORECASE)
    for hint in URL_SLUG_HINTS
]

_BANGLA_URL_HINTS: tuple[str, ...] = (
    "টেলিকম", "টেলিযোগাযোগ", "বিটিআরসি", "গ্রামীণফোন", "বাংলালিংক",
    "টেলিটক", "রবি", "ইন্টারনেট", "ব্রডব্যান্ড", "৫জি", "৪জি", "স্টারলিংক",
    "মোবাইল-অপারেটর", "বিকাশ", "নগদ", "সাবমেরিন", "তরঙ্গ", "সিম",
)


def candidate_looks_promising(url: str, headline: str = "", summary: str = "") -> bool:
    """Fast pre-filter before fetching full article HTML from huge sitemaps.

    If headline/summary is already known (e.g. RSS, news-sitemap, Quintype API),
    use a permissive relevance threshold (1.25). If only a URL is known (e.g.
    generic XML sitemap), check whether a word-bounded slug token matches a
    telecom hint (EN or URL-decoded BN).
    """
    if headline or summary:
        res = evaluate_relevance(headline, summary, threshold=1.25)
        return res.is_relevant

    if any(p.search(url) for p in _URL_SLUG_HINT_PATTERNS):
        return True
    if "%" in url:
        from urllib.parse import unquote
        decoded = unquote(url)
        return any(hint in decoded for hint in _BANGLA_URL_HINTS)
    return False
