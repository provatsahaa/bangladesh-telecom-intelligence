"""Strict Pydantic v2 schemas for LLM article intelligence and daily executive briefings."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

TELECOM_CATEGORIES: tuple[str, ...] = (
    "Telecom Regulation & Policy",
    "Network Infrastructure & RAN",
    "5G & Future Technologies",
    "Spectrum & Licensing",
    "Telecom Business & Finance",
    "Mobile Financial Services",
    "Digital Services & OTT",
    "Cybersecurity & Data Privacy",
    "Customer Experience",
    "Mergers & Acquisitions",
    "International Telecom Developments",
    "Other",
)

TelecomCategory = Literal[
    "Telecom Regulation & Policy",
    "Network Infrastructure & RAN",
    "5G & Future Technologies",
    "Spectrum & Licensing",
    "Telecom Business & Finance",
    "Mobile Financial Services",
    "Digital Services & OTT",
    "Cybersecurity & Data Privacy",
    "Customer Experience",
    "Mergers & Acquisitions",
    "International Telecom Developments",
    "Other",
]

SentimentLabel = Literal["Positive", "Negative", "Neutral", "Mixed"]
ImpactLevel = Literal["Low", "Medium", "High"]
RelevanceLevel = Literal["Low", "Medium", "High"]

_CANONICAL_OPERATORS: dict[str, str] = {
    "grameenphone": "Grameenphone",
    "gp": "Grameenphone",
    "skitto": "Grameenphone",
    "banglalink": "Banglalink",
    "banglalink digital": "Banglalink",
    "robi": "Robi",
    "robi axiata": "Robi",
    "airtel": "Robi",
    "airtel bangladesh": "Robi",
    "teletalk": "Teletalk",
    "teletalk bangladesh": "Teletalk",
    "btcl": "BTCL",
}

_CATEGORY_LOWER_MAP = {c.lower(): c for c in TELECOM_CATEGORIES}


def _dedupe_clean(items: list[str], max_items: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        val = " ".join(str(raw or "").split()).strip()
        if not val:
            continue
        key = val.lower()
        if key not in seen:
            seen.add(key)
            out.append(val)
        if len(out) >= max_items:
            break
    return out


class ArticleIntelligence(BaseModel):
    """Structured intelligence extracted from a single scraped news article."""

    english_headline: str = Field(
        ...,
        min_length=8,
        max_length=240,
        description="Normalized, factual English headline reflecting the original article.",
    )
    summary: str = Field(
        ...,
        min_length=40,
        max_length=1600,
        description="Concise, strictly grounded English summary (3 to 5 sentences) of reported facts only.",
    )
    key_takeaways: list[str] = Field(
        ...,
        min_length=2,
        max_length=6,
        description="3 to 5 concise bullet takeaways explicitly supported by the article.",
    )
    main_category: TelecomCategory = Field(
        ...,
        description="Primary telecom category from the canonical 12-category list.",
    )
    subcategory: str | None = Field(
        default=None,
        max_length=120,
        description="Specific sub-topic (e.g. 'Spectrum Renewal', 'Tower Sharing', 'AML Compliance').",
    )
    organizations: list[str] = Field(
        default_factory=list,
        description="Named organizations, regulators, vendors, and companies mentioned.",
    )
    operators_mentioned: list[str] = Field(
        default_factory=list,
        description="Telecom operators mentioned (canonical names: Grameenphone, Robi, Banglalink, Teletalk, BTCL).",
    )
    sentiment: SentimentLabel = Field(
        ...,
        description="Overall tone of the reporting: Positive, Negative, Neutral, or Mixed.",
    )
    sentiment_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )
    industry_impact: ImpactLevel = Field(
        ...,
        description="Estimated significance for Bangladesh's telecom sector: Low, Medium, or High.",
    )
    telecom_relevance: RelevanceLevel = Field(
        ...,
        description="Degree of direct telecommunications relevance: Low, Medium, or High.",
    )
    reported_facts: list[str] = Field(
        default_factory=list,
        description="Explicit numbers, dates, financial figures, tariffs, and statistics stated in the article.",
    )
    industry_implications: str | None = Field(
        default=None,
        max_length=900,
        description="AI interpretation of potential industry implications, clearly separated from reported facts.",
    )

    @field_validator("main_category", mode="before")
    @classmethod
    def normalize_category(cls, v: object) -> str:
        raw = str(v or "").strip()
        if raw in TELECOM_CATEGORIES:
            return raw
        low = raw.lower()
        if low in _CATEGORY_LOWER_MAP:
            return _CATEGORY_LOWER_MAP[low]
        # Fuzzy substring match before falling back to Other
        for cat_low, canonical in _CATEGORY_LOWER_MAP.items():
            if low in cat_low or cat_low in low:
                return canonical
        return "Other"

    @field_validator("sentiment", mode="before")
    @classmethod
    def normalize_sentiment(cls, v: object) -> str:
        raw = str(v or "").strip().capitalize()
        return raw if raw in ("Positive", "Negative", "Neutral", "Mixed") else "Neutral"

    @field_validator("sentiment_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: object) -> float:
        try:
            val = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.7
        if val > 10.0 and val <= 100.0:
            val = val / 100.0
        elif val > 1.0 and val <= 10.0:
            val = val / 10.0
        return round(min(max(val, 0.0), 1.0), 2)

    @field_validator("industry_impact", "telecom_relevance", mode="before")
    @classmethod
    def normalize_level(cls, v: object) -> str:
        raw = str(v or "").strip().capitalize()
        return raw if raw in ("Low", "Medium", "High") else "Medium"

    @field_validator("key_takeaways", mode="after")
    @classmethod
    def clean_takeaways(cls, v: list[str]) -> list[str]:
        cleaned = _dedupe_clean(v, max_items=5)
        if len(cleaned) < 2:
            raise ValueError("At least 2 substantive key takeaways are required.")
        return cleaned

    @field_validator("organizations", "reported_facts", mode="after")
    @classmethod
    def clean_lists(cls, v: list[str]) -> list[str]:
        return _dedupe_clean(v, max_items=15)

    @field_validator("operators_mentioned", mode="after")
    @classmethod
    def normalize_operators(cls, v: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in v:
            val = " ".join(str(raw or "").split()).strip()
            if not val:
                continue
            canon = _CANONICAL_OPERATORS.get(val.lower(), val)
            if canon.lower() not in seen:
                seen.add(canon.lower())
                normalized.append(canon)
        return normalized[:10]


class BriefingDevelopment(BaseModel):
    """A single consolidated industry development inside The Daily Brief."""

    title: str = Field(..., min_length=6, max_length=180)
    reported_facts: str = Field(
        ...,
        min_length=30,
        max_length=800,
        description="What actually happened, strictly grounded in the supporting articles.",
    )
    why_it_matters: str = Field(
        ...,
        min_length=20,
        max_length=600,
        description="AI interpretation explaining strategic significance for Bangladesh's telecom sector.",
    )
    key_entities: list[str] = Field(default_factory=list)
    supporting_article_ids: list[int] = Field(default_factory=list)


class DailyBriefingOutput(BaseModel):
    """Structured executive Daily Brief generated from clustered AI-analyzed articles."""

    headline: str = Field(..., min_length=8, max_length=200)
    intro_overview: str = Field(..., min_length=30, max_length=700)
    developments: list[BriefingDevelopment] = Field(..., min_length=1, max_length=5)
    overall_implications: str = Field(..., min_length=30, max_length=900)
