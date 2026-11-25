"""Live-verified registry of 24 target Facebook Pages for Bangladesh telecom intelligence.

Every URL was verified from the organization's official website or official secondary
channels. Unverified pages keep `facebook_url=None` rather than guessing URLs.
All third-party pages default to `access_mode="manual_only"` until a verified Meta
App with Page Public Content Access (PPCA) or Page management role is configured.
"""

from __future__ import annotations

from .models import FacebookSource

DEFAULT_FACEBOOK_SOURCES: tuple[FacebookSource, ...] = (
    # --- A. Mobile Network Operators (4) ---
    FacebookSource(
        source_id="fb_grameenphone",
        page_name="Grameenphone",
        category="mno",
        official_website="https://www.grameenphone.com",
        facebook_url="https://www.facebook.com/grameenphone",
        facebook_page_id="135237519825044",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from grameenphone.com homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_banglalink",
        page_name="Banglalink",
        category="mno",
        official_website="https://www.banglalink.net",
        facebook_url="https://www.facebook.com/banglalinkdigital",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from banglalink.net homepage social link.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_robi",
        page_name="Robi",
        category="mno",
        official_website="https://www.robi.com.bd",
        facebook_url="https://www.facebook.com/RobiFanz",
        facebook_page_id=None,
        verification_status="VERIFIED_SECONDARY",
        verification_evidence="SPA site; confirmed via official Robi Axiata references.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_teletalk",
        page_name="Teletalk",
        category="mno",
        official_website="https://www.teletalk.com.bd",
        facebook_url="https://www.facebook.com/yourTELETALK",
        facebook_page_id=None,
        verification_status="VERIFIED_SECONDARY",
        verification_evidence="Confirmed via official Teletalk Bangladesh YouTube channel links.",
        access_mode="manual_only",
    ),
    # --- B. Telecom Regulators & Industry Organizations (5) ---
    FacebookSource(
        source_id="fb_btrc",
        page_name="Bangladesh Telecommunication Regulatory Commission (BTRC)",
        category="regulator_org",
        official_website="https://btrc.gov.bd",
        facebook_url="https://www.facebook.com/btrc.gov",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from btrc.gov.bd homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_amtob",
        page_name="Association of Mobile Telecom Operators of Bangladesh (AMTOB)",
        category="regulator_org",
        official_website="https://amtob.org.bd",
        facebook_url="https://www.facebook.com/ConnectAMTOB",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from amtob.org.bd homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_btcl",
        page_name="Bangladesh Telecommunications Company Limited (BTCL)",
        category="regulator_org",
        official_website="https://btcl.gov.bd",
        facebook_url="https://www.facebook.com/btclptd",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from btcl.gov.bd homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_ptd",
        page_name="Posts and Telecommunications Division",
        category="regulator_org",
        official_website="https://ptd.gov.bd",
        facebook_url="https://www.facebook.com/ptdivision",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from ptd.gov.bd homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_bscl",
        page_name="Bangladesh Satellite Company Limited",
        category="regulator_org",
        official_website="https://bscl.gov.bd",
        facebook_url="https://www.facebook.com/bcsclbd",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from bscl.gov.bd homepage.",
        access_mode="manual_only",
    ),
    # --- C. Telecom & Technology Media (5) ---
    FacebookSource(
        source_id="fb_cablegram",
        page_name="Cablegram",
        category="telecom_media",
        official_website="https://cablgram.com",
        facebook_url=None,
        facebook_page_id=None,
        verification_status="UNVERIFIED_NEEDS_CONFIRMATION",
        verification_evidence="Official website timed out during probe; URL left unset until confirmed.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_markedium",
        page_name="Markedium",
        category="telecom_media",
        official_website="https://markedium.com",
        facebook_url="https://www.facebook.com/Markedium.bd",
        facebook_page_id="1800342353606071",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from markedium.com homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_techshohor",
        page_name="Techshohor",
        category="telecom_media",
        official_website="https://techshohor.com",
        facebook_url="https://www.facebook.com/Techshohor",
        facebook_page_id="165779040261204",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from techshohor.com homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_rcr_wireless",
        page_name="RCR Wireless News",
        category="telecom_media",
        official_website="https://www.rcrwireless.com",
        facebook_url="https://www.facebook.com/rcrwireless",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from rcrwireless.com homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_light_reading",
        page_name="Light Reading",
        category="telecom_media",
        official_website="https://www.lightreading.com",
        facebook_url="https://www.facebook.com/LightReading",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from lightreading.com homepage.",
        access_mode="manual_only",
    ),
    # --- D. Mainstream Bangladeshi News Publishers (10) ---
    FacebookSource(
        source_id="fb_prothom_alo",
        page_name="Prothom Alo",
        category="bd_news",
        official_website="https://www.prothomalo.com",
        facebook_url="https://www.facebook.com/DailyProthomAlo",
        facebook_page_id="163059227060505",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from prothomalo.com homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_the_daily_star",
        page_name="The Daily Star",
        category="bd_news",
        official_website="https://www.thedailystar.net",
        facebook_url="https://www.facebook.com/dailystarnews",
        facebook_page_id="100117146754830",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from thedailystar.net homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_dhaka_tribune",
        page_name="Dhaka Tribune",
        category="bd_news",
        official_website="https://www.dhakatribune.com",
        facebook_url="https://www.facebook.com/dhakatribune",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from dhakatribune.com homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_tbsnews",
        page_name="The Business Standard",
        category="bd_news",
        official_website="https://www.tbsnews.net",
        facebook_url="https://www.facebook.com/tbsnews.net",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from tbsnews.net homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_bdnews24",
        page_name="bdnews24.com",
        category="bd_news",
        official_website="https://bdnews24.com",
        facebook_url="https://www.facebook.com/bdnews24",
        facebook_page_id="188640165600",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from bdnews24.com homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_financial_express",
        page_name="The Financial Express",
        category="bd_news",
        official_website="https://thefinancialexpress.com.bd",
        facebook_url="https://www.facebook.com/febdonline",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from thefinancialexpress.com.bd homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_bonik_barta",
        page_name="Bonik Barta",
        category="bd_news",
        official_website="https://bonikbarta.com",
        facebook_url="https://www.facebook.com/dailybonikbarta",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from bonikbarta.com homepage.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_kaler_kantho",
        page_name="Kaler Kantho",
        category="bd_news",
        official_website="https://www.kalerkantho.com",
        facebook_url="https://www.facebook.com/kalerkantho",
        facebook_page_id=None,
        verification_status="VERIFIED_SECONDARY",
        verification_evidence="Website returns 403; confirmed via official Kaler Kantho YouTube channel.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_bangla_tribune",
        page_name="Bangla Tribune",
        category="bd_news",
        official_website="https://www.banglatribune.com",
        facebook_url="https://www.facebook.com/BanglaTribuneOnline",
        facebook_page_id="2083680901755355",
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted from banglatribune.com homepage + fb:pages meta tag.",
        access_mode="manual_only",
    ),
    FacebookSource(
        source_id="fb_new_age",
        page_name="New Age",
        category="bd_news",
        official_website="https://www.newagebd.net",
        facebook_url="https://www.facebook.com/NewAgeBDNews",
        facebook_page_id=None,
        verification_status="VERIFIED_OFFICIAL_SITE",
        verification_evidence="Extracted directly from newagebd.net homepage.",
        access_mode="manual_only",
    ),
)


def get_facebook_sources(
    source_ids: list[str] | None = None,
    *,
    category: str | None = None,
) -> list[FacebookSource]:
    """Return enabled Facebook Page sources, optionally filtered by source_id or category."""
    sources = [s for s in DEFAULT_FACEBOOK_SOURCES if s.enabled]
    if category:
        sources = [s for s in sources if s.category == category]
    if source_ids:
        requested = set(source_ids)
        sources = [s for s in sources if s.source_id in requested]
    return sources


def match_source_by_url(post_url: str) -> FacebookSource | None:
    """Match a submitted Facebook post URL against a verified Page in the registry."""
    low = (post_url or "").lower()
    for src in DEFAULT_FACEBOOK_SOURCES:
        if not src.facebook_url:
            continue
        handle = src.facebook_url.rstrip("/").split("/")[-1].lower()
        if f"facebook.com/{handle}/" in low or low.endswith(f"facebook.com/{handle}"):
            return src
        if src.facebook_page_id and src.facebook_page_id in low:
            return src
    return None
