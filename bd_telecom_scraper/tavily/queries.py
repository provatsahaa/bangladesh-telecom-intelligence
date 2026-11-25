"""Curated Bangladesh & Global telecom discovery queries with credible domain filters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TavilyQuerySpec:
    """A single targeted telecom search query with optional credible domain filtering."""

    query_id: str
    scope: str  # "bangladesh" | "global"
    query: str
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = (
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "youtube.com",
        "linkedin.com",
        "x.com",
        "twitter.com",
        "reddit.com",
        "wikipedia.org",
    )


BANGLADESH_CREDIBLE_DOMAINS: tuple[str, ...] = (
    "thedailystar.net",
    "tbsnews.net",
    "prothomalo.com",
    "dhakatribune.com",
    "thefinancialexpress.com.bd",
    "newagebd.net",
    "bdnews24.com",
    "bonikbarta.com",
    "kalerkantho.com",
    "samakal.com",
    "jugantor.com",
    "ittefaq.com.bd",
    "banglatribune.com",
    "techshohor.com",
    "bssnews.net",
    "daily-sun.com",
    "unb.com.bd",
)

GLOBAL_TELECOM_CREDIBLE_DOMAINS: tuple[str, ...] = (
    "reuters.com",
    "bloomberg.com",
    "lightreading.com",
    "rcrwireless.com",
    "telecomtv.com",
    "capacitymedia.com",
    "developingtelecoms.com",
    "mobileworldlive.com",
    "fierce-network.com",
    "gsma.com",
    "telecomreview.com",
    "veon.com",
)

DEFAULT_TAVILY_QUERIES: tuple[TavilyQuerySpec, ...] = (
    # --- Tier A: Bangladesh Telecom (4 semantic natural-language queries) ---
    TavilyQuerySpec(
        query_id="bd_operators_veon",
        scope="bangladesh",
        query="Grameenphone Banglalink Robi Teletalk VEON Bangladesh mobile operator telecom news",
        include_domains=BANGLADESH_CREDIBLE_DOMAINS,
    ),
    TavilyQuerySpec(
        query_id="bd_btrc_regulation_spectrum",
        scope="bangladesh",
        query="BTRC Bangladesh telecom regulation spectrum renewal auction policy",
        include_domains=BANGLADESH_CREDIBLE_DOMAINS,
    ),
    TavilyQuerySpec(
        query_id="bd_5g_infrastructure_broadband",
        scope="bangladesh",
        query="Bangladesh 5G network infrastructure broadband internet Starlink submarine cable",
        include_domains=BANGLADESH_CREDIBLE_DOMAINS,
    ),
    TavilyQuerySpec(
        query_id="bd_digital_mfs_ai_telecom",
        scope="bangladesh",
        query="Bangladesh telecom AI digital services mobile financial services bKash Nagad Toffee",
        include_domains=BANGLADESH_CREDIBLE_DOMAINS,
    ),
    # --- Tier B: Global & Regional Telecom Developments (2 semantic queries) ---
    TavilyQuerySpec(
        query_id="global_veon_telenor_axiata",
        scope="global",
        query="VEON Telenor Axiata telecommunications investment partnership acquisition",
        include_domains=GLOBAL_TELECOM_CREDIBLE_DOMAINS,
    ),
    TavilyQuerySpec(
        query_id="global_5g_ran_ai_telecom",
        scope="global",
        query="global telecommunications 5G Open RAN AI network infrastructure industry developments",
        include_domains=GLOBAL_TELECOM_CREDIBLE_DOMAINS,
    ),
)


def get_tavily_queries(
    query_ids: list[str] | None = None,
    *,
    scope: str | None = None,
) -> list[TavilyQuerySpec]:
    queries = list(DEFAULT_TAVILY_QUERIES)
    if scope:
        queries = [q for q in queries if q.scope == scope]
    if query_ids:
        wanted = set(query_ids)
        queries = [q for q in queries if q.query_id in wanted]
    return queries
