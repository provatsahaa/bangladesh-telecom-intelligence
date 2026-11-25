"""Registry of leading Bangladeshi news portals with live-verified collection strategies.

Sources are ordered by reliability and coverage. Each source prioritizes official
machine-readable endpoints (Quintype Search & Collections JSON APIs, live RSS feeds,
XML sitemaps) before falling back to permitted HTML section pages.
"""

from __future__ import annotations

from .models import SourceConfig

_EN_TELECOM_SEARCH_QUERIES: tuple[str, ...] = (
    "BTRC",
    "Grameenphone",
    "Robi",
    "Banglalink",
    "Teletalk",
    "telecom",
    "5G",
    "broadband",
    "Starlink",
)

_BN_TELECOM_SEARCH_QUERIES: tuple[str, ...] = (
    "বিটিআরসি",
    "গ্রামীণফোন",
    "বাংলালিংক",
    "রবি আজিয়াটা",
    "টেলিটক",
    "টেলিকম",
    "ব্রডব্যান্ড",
    "স্টারলিংক",
    "মোবাইল অপারেটর",
)

DEFAULT_SOURCES: tuple[SourceConfig, ...] = (
    # 1A. Prothom Alo (English) - Quintype CMS
    SourceConfig(
        source_id="prothom_alo_en",
        publisher="Prothom Alo (English)",
        language="en",
        base_url="https://en.prothomalo.com",
        rss_urls=("https://en.prothomalo.com/feed",),
        quintype_collections=(
            "https://en.prothomalo.com/api/v1/collections/business",
            "https://en.prothomalo.com/api/v1/collections/science-technology",
        ),
        quintype_search_queries=_EN_TELECOM_SEARCH_QUERIES,
        allowed_path_hints=("/business/", "/science-technology/", "/bangladesh/", "/corporate/"),
        rate_limit_seconds=1.0,
        notes="Quintype CMS: official Search + Collections JSON APIs + RSS.",
    ),
    # 1B. Prothom Alo (Bangla) - Quintype CMS
    SourceConfig(
        source_id="prothom_alo_bn",
        publisher="Prothom Alo (Bangla)",
        language="bn",
        base_url="https://www.prothomalo.com",
        rss_urls=("https://www.prothomalo.com/feed",),
        quintype_collections=(
            "https://www.prothomalo.com/api/v1/collections/business",
            "https://www.prothomalo.com/api/v1/collections/technology",
        ),
        quintype_search_queries=_BN_TELECOM_SEARCH_QUERIES,
        allowed_path_hints=("/business/", "/technology/", "/bangladesh/"),
        rate_limit_seconds=1.0,
        notes="Quintype CMS: official Bangla Search + Collections JSON APIs + RSS.",
    ),
    # 2. The Daily Star (English) - Drupal
    SourceConfig(
        source_id="the_daily_star",
        publisher="The Daily Star",
        language="en",
        base_url="https://www.thedailystar.net",
        rss_urls=(
            "https://www.thedailystar.net/business/rss.xml",
            "https://www.thedailystar.net/tech-startup/rss.xml",
        ),
        sitemap_urls=(
            "https://www.thedailystar.net/googlenews.xml",
            "https://www.thedailystar.net/sitemap.xml",
        ),
        section_urls=(
            "https://www.thedailystar.net/business",
            "https://www.thedailystar.net/tech-startup",
        ),
        allowed_path_hints=("/business/", "/tech-startup/", "/news/bangladesh/"),
        rate_limit_seconds=1.2,
        notes="Root /rss.xml is stale; uses live section RSS + googlenews.xml + half-year sitemap.",
    ),
    # 3. Dhaka Tribune (English)
    SourceConfig(
        source_id="dhaka_tribune",
        publisher="Dhaka Tribune",
        language="en",
        base_url="https://www.dhakatribune.com",
        rss_urls=(
            "https://www.dhakatribune.com/feed/",
            "https://www.dhakatribune.com/feed/business",
        ),
        sitemap_urls=(
            "https://www.dhakatribune.com/news-sitemap.xml",
            "https://www.dhakatribune.com/sitemap.xml",
        ),
        allowed_path_hints=("/business/", "/science-technology", "/bangladesh/"),
        rate_limit_seconds=1.2,
        notes="Live 100-item RSS + /feed/business + news-sitemap.xml + monthly XML sitemaps.",
    ),
    # 4A. The Business Standard (English) - Drupal
    SourceConfig(
        source_id="tbsnews",
        publisher="The Business Standard",
        language="en",
        base_url="https://www.tbsnews.net",
        rss_urls=(
            "https://www.tbsnews.net/tech/rss.xml",
            "https://www.tbsnews.net/economy/rss.xml",
        ),
        sitemap_urls=(
            "https://www.tbsnews.net/googlenews.xml",
            "https://www.tbsnews.net/sitemap.xml",
        ),
        allowed_path_hints=("/tech/", "/economy/", "/bangladesh/"),
        rate_limit_seconds=1.2,
        notes="Root /rss.xml is stale (2021); uses live /tech & /economy RSS + googlenews.xml + half-year sitemap.",
    ),
    # 4B. The Business Standard (Bangla) - Drupal
    SourceConfig(
        source_id="tbsnews_bn",
        publisher="The Business Standard (Bangla)",
        language="bn",
        base_url="https://www.tbsnews.net",
        sitemap_urls=(
            "https://www.tbsnews.net/bangla/googlenews.xml",
            "https://www.tbsnews.net/bangla/sitemap.xml",
        ),
        allowed_path_hints=("/bangla/",),
        rate_limit_seconds=1.2,
        notes="Bangla edition via googlenews.xml + bangla/sitemap.xml.",
    ),
    # 5. The Financial Express (English)
    SourceConfig(
        source_id="financial_express",
        publisher="The Financial Express",
        language="en",
        base_url="https://thefinancialexpress.com.bd",
        section_urls=(
            "https://thefinancialexpress.com.bd/category/trade",
            "https://thefinancialexpress.com.bd/category/economy",
            "https://thefinancialexpress.com.bd/category/sci-tech",
        ),
        allowed_path_hints=("/trade/", "/economy/", "/sci-tech/", "/national/", "/stock/"),
        rate_limit_seconds=1.5,
        notes="No public RSS; sitemap.xml lists categories only. Uses permitted /category/* HTML discovery.",
    ),
    # 6A. The Daily Ittefaq (English)
    SourceConfig(
        source_id="ittefaq_en",
        publisher="The Daily Ittefaq (English)",
        language="en",
        base_url="https://en.ittefaq.com.bd",
        rss_urls=("https://en.ittefaq.com.bd/feed/",),
        section_urls=("https://en.ittefaq.com.bd/business",),
        allowed_path_hints=("/business/", "/tech/", "/bangladesh/", "/finance/"),
        rate_limit_seconds=1.2,
        notes="Live 100-item RSS feed + section HTML fallback.",
    ),
    # 6B. The Daily Ittefaq (Bangla)
    SourceConfig(
        source_id="ittefaq_bn",
        publisher="The Daily Ittefaq (Bangla)",
        language="bn",
        base_url="https://www.ittefaq.com.bd",
        rss_urls=("https://www.ittefaq.com.bd/feed/",),
        sitemap_urls=(
            "https://www.ittefaq.com.bd/news-sitemap.xml",
            "https://www.ittefaq.com.bd/sitemap.xml",
        ),
        allowed_path_hints=("/business/", "/tech/", "/national/", "/economy/"),
        rate_limit_seconds=1.2,
        notes="Live RSS + news-sitemap.xml + monthly XML sitemaps.",
    ),
    # 7. bdnews24 (English) - Quintype CMS
    SourceConfig(
        source_id="bdnews24",
        publisher="bdnews24",
        language="en",
        base_url="https://bdnews24.com",
        quintype_search_queries=_EN_TELECOM_SEARCH_QUERIES,
        section_urls=(
            "https://bdnews24.com/business",
            "https://bdnews24.com/tech",
        ),
        allowed_path_hints=("/business/", "/tech/", "/economy/", "/bangladesh/"),
        rate_limit_seconds=1.0,
        notes="Quintype CMS: official Search JSON API + section fallback.",
    ),
    # 8. New Age (English)
    SourceConfig(
        source_id="new_age",
        publisher="New Age",
        language="en",
        base_url="https://www.newagebd.net",
        sitemap_urls=("https://www.newagebd.net/sitemap.xml",),
        section_urls=(
            "https://www.newagebd.net/articlelist/29/business-economy",
            "https://www.newagebd.net/articlelist/12/science-n-technology",
        ),
        allowed_path_hints=("/post/", "/article/"),
        rate_limit_seconds=1.2,
        notes="No public RSS; uses news-sitemap XML + permitted /articlelist/* HTML pages.",
    ),
    # 9. Bangla Tribune (Bangla)
    SourceConfig(
        source_id="bangla_tribune",
        publisher="Bangla Tribune",
        language="bn",
        base_url="https://www.banglatribune.com",
        rss_urls=("https://www.banglatribune.com/feed/",),
        sitemap_urls=(
            "https://www.banglatribune.com/news-sitemap.xml",
            "https://www.banglatribune.com/sitemap.xml",
        ),
        allowed_path_hints=("/tech-and-gadget/", "/business/", "/national/", "/country/"),
        rate_limit_seconds=1.2,
        notes="Live 100-item RSS + news-sitemap.xml + monthly XML sitemaps.",
    ),
    # 10. Techshohor (Bangla Tech & Telecom Specialist)
    SourceConfig(
        source_id="techshohor",
        publisher="Techshohor",
        language="bn",
        base_url="https://techshohor.com",
        rss_urls=("https://techshohor.com/feed/",),
        rate_limit_seconds=1.2,
        notes="Dedicated Bangladesh telecom & technology news portal with live RSS.",
    ),
    # 11. Bonik Barta (Bangla Business Daily)
    SourceConfig(
        source_id="bonik_barta",
        publisher="Bonik Barta",
        language="bn",
        base_url="https://bonikbarta.com",
        sitemap_urls=("https://bonikbarta.com/sitemap.xml",),
        rate_limit_seconds=1.5,
        notes="Leading Bangladesh Bangla business daily via XML sitemap.",
    ),
    # 12. BSS News (National News Agency)
    SourceConfig(
        source_id="bssnews",
        publisher="BSS News",
        language="en",
        base_url="https://www.bssnews.net",
        sitemap_urls=("https://www.bssnews.net/sitemap.xml",),
        allowed_path_hints=("/news/", "/business/", "/science-and-tech/"),
        rate_limit_seconds=1.5,
        notes="Bangladesh Sangbad Sangstha (national news agency) via XML sitemap.",
    ),
    # 13. Jugantor (Bangla) - Partially Cloudflare-protected
    SourceConfig(
        source_id="jugantor",
        publisher="Jugantor",
        language="bn",
        base_url="https://www.jugantor.com",
        rss_urls=("https://www.jugantor.com/feed/rss.xml",),
        sitemap_urls=("https://www.jugantor.com/sitemap.xml",),
        allowed_path_hints=("/economics/", "/tech/", "/national/", "/todays-paper/"),
        rate_limit_seconds=2.0,
        notes="robots.txt and /sitemap.xml accessible; RSS/HTML may return HTTP 403 (Cloudflare WAF). Never bypassed.",
    ),
    # 14. Kaler Kantho (Bangla) - Cloudflare-protected
    SourceConfig(
        source_id="kaler_kantho",
        publisher="Kaler Kantho",
        language="bn",
        base_url="https://www.kalerkantho.com",
        rss_urls=("https://www.kalerkantho.com/rss.xml",),
        sitemap_urls=("https://www.kalerkantho.com/sitemap.xml",),
        allowed_path_hints=("/online/business/", "/online/info-tech/", "/online/national/"),
        rate_limit_seconds=2.0,
        notes="Currently enforces Cloudflare HTTP 403 on automated clients; recorded as RESTRICTED without bypass.",
    ),
    # 15. Samakal (Bangla) - Cloudflare-protected
    SourceConfig(
        source_id="samakal",
        publisher="Samakal",
        language="bn",
        base_url="https://samakal.com",
        rss_urls=("https://samakal.com/rss.xml",),
        sitemap_urls=("https://samakal.com/sitemap.xml",),
        allowed_path_hints=("/economics/", "/technology/", "/bangladesh/"),
        rate_limit_seconds=2.0,
        notes="Currently enforces Cloudflare HTTP 403 even on robots.txt; recorded as RESTRICTED without bypass.",
    ),
)


def get_sources(source_ids: list[str] | None = None) -> list[SourceConfig]:
    """Return enabled sources, optionally filtered by a list of source_ids."""
    sources = [s for s in DEFAULT_SOURCES if s.enabled]
    if source_ids:
        requested = set(source_ids)
        sources = [s for s in sources if s.source_id in requested]
    return sources
