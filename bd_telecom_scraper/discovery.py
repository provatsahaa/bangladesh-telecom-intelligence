"""Multi-strategy article discovery: Quintype JSON API -> RSS -> XML Sitemaps -> Section HTML."""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from .compliance import PoliteHttpClient
from .models import DiscoveredItem, SourceConfig

logger = logging.getLogger(__name__)

DHAKA_TZ = timezone(timedelta(hours=6))

_DAILY_SITEMAP_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_MONTHLY_SITEMAP_RE = re.compile(r"/(\d{4})-(\d{2})-01\.xml$")
_HALFYEAR_SITEMAP_RE = re.compile(r"sitemap-(\d{4})-(\d+)\.xml$")


def parse_datetime_flexible(value: str | int | float | None) -> datetime | None:
    """Parse ISO-8601, RFC-2822, or millisecond epoch timestamps into Asia/Dhaka datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # Quintype uses epoch milliseconds
        ts = value / 1000.0 if value > 1e11 else float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(DHAKA_TZ)

    raw = str(value).strip()
    if not raw:
        return None

    # ISO-8601 (handles trailing Z and timezone offsets)
    try:
        iso = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DHAKA_TZ)
        return dt.astimezone(DHAKA_TZ)
    except ValueError:
        pass

    # RFC-2822 (RSS pubDate, including 2-digit year variants like 'Fri, 20 Nov 26 ...')
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DHAKA_TZ)
        return dt.astimezone(DHAKA_TZ)
    except (TypeError, ValueError):
        pass

    # Common date-only fallback
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=DHAKA_TZ)
            return dt
        except ValueError:
            pass
    return None


def _select_relevant_child_sitemaps(
    child_locs: list[str],
    cutoff: datetime,
    now: datetime,
    max_children: int = 16,
) -> list[str]:
    """Select only child sitemaps that can contain articles within [cutoff, now]."""
    selected: list[str] = []
    cutoff_date = cutoff.date()
    now_date = now.date()

    # 1. Always include news/google-news sitemaps first (small & fresh)
    for loc in child_locs:
        low = loc.lower()
        if any(tok in low for tok in ("news-sitemap", "news_sitemap", "googlenews")):
            if loc not in selected:
                selected.append(loc)

    # 2. Daily sitemaps (e.g. Prothom Alo, bdnews24: sitemap-daily-2026-11-20.xml)
    daily_matches: list[tuple[str, str]] = []
    for loc in child_locs:
        m = _DAILY_SITEMAP_RE.search(loc)
        if m and not _MONTHLY_SITEMAP_RE.search(loc):
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                if cutoff_date <= d <= now_date + timedelta(days=1):
                    daily_matches.append((m.group(1), loc))
            except ValueError:
                pass
    for _, loc in sorted(daily_matches, reverse=True):
        if loc not in selected:
            selected.append(loc)

    # 3. Monthly sitemaps (e.g. Dhaka Tribune, Ittefaq: /2026-11-01.xml)
    valid_months = {(cutoff_date.year, cutoff_date.month), (now_date.year, now_date.month)}
    for loc in child_locs:
        m = _MONTHLY_SITEMAP_RE.search(loc)
        if m:
            ym = (int(m.group(1)), int(m.group(2)))
            if ym in valid_months and loc not in selected:
                selected.append(loc)

    # 4. Half-year sitemaps (e.g. Daily Star, TBS: sitemap-2026-2.xml)
    halfyear: list[tuple[int, int, str]] = []
    for loc in child_locs:
        m = _HALFYEAR_SITEMAP_RE.search(loc)
        if m:
            yr, part = int(m.group(1)), int(m.group(2))
            if yr >= cutoff_date.year:
                halfyear.append((yr, part, loc))
    if halfyear:
        # Pick the latest partition for the current year
        halfyear.sort(reverse=True)
        latest_loc = halfyear[0][2]
        if latest_loc not in selected:
            selected.append(latest_loc)

    return selected[:max_children]


def _parse_sitemap_urlset(
    xml_text: str,
    source: SourceConfig,
    cutoff: datetime,
) -> list[DiscoveredItem]:
    """Parse a <urlset> XML sitemap into DiscoveredItems within the lookback window."""
    items: list[DiscoveredItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("Malformed XML sitemap for %s: %s", source.source_id, exc)
        return items

    for url_el in root.iter():
        if not url_el.tag.endswith("}url") and url_el.tag != "url":
            continue
        loc = ""
        lastmod = ""
        news_date = ""
        news_title = ""
        for child in url_el.iter():
            tag = child.tag.split("}")[-1].lower()
            text = (child.text or "").strip()
            if not text:
                continue
            if tag == "loc" and not loc:
                loc = text
            elif tag == "lastmod" and not lastmod:
                lastmod = text
            elif tag == "publication_date" and not news_date:
                news_date = text
            elif tag == "title" and not news_title:
                news_title = text

        if not loc:
            continue
        pub_dt = parse_datetime_flexible(news_date or lastmod)
        if pub_dt is not None and pub_dt < cutoff:
            continue

        items.append(
            DiscoveredItem(
                url=loc,
                source_id=source.source_id,
                publisher=source.publisher,
                language=source.language,
                discovery_method="sitemap",
                headline=news_title,
                published_at=pub_dt,
            )
        )
    return items


class SourceDiscoverer:
    """Discovers candidate articles for a SourceConfig using compliant endpoints."""

    def __init__(self, client: PoliteHttpClient) -> None:
        self.client = client

    def discover(
        self,
        source: SourceConfig,
        cutoff: datetime,
        now: datetime,
    ) -> tuple[list[DiscoveredItem], list[str], list[str], bool]:
        """Run all configured discovery methods for `source`.

        Returns:
            (items, methods_used, errors, blocked_by_waf)
        """
        discovered: list[DiscoveredItem] = []
        methods_used: list[str] = []
        errors: list[str] = []
        waf_hits = 0
        total_attempts = 0

        # 1. Official Quintype Collections & Search JSON APIs (highest reliability where available)
        for api_url in source.quintype_collections:
            total_attempts += 1
            res = self.client.get(
                api_url,
                rate_limit_seconds=source.rate_limit_seconds,
                respect_robots=source.respect_robots_txt,
            )
            if res.blocked_reason == "http_403_waf":
                waf_hits += 1
                errors.append(f"Quintype API {api_url}: {res.error}")
                continue
            if not res.ok:
                errors.append(f"Quintype API {api_url}: {res.error}")
                continue
            api_items = self._parse_quintype_collection(res.text, source, cutoff)
            if api_items:
                discovered.extend(api_items)
                if "quintype_api" not in methods_used:
                    methods_used.append("quintype_api")

        for query in source.quintype_search_queries:
            from urllib.parse import quote
            search_url = f"{source.base_url}/api/v1/search?q={quote(query)}&limit=15&sort=latest-published"
            total_attempts += 1
            res = self.client.get(
                search_url,
                rate_limit_seconds=source.rate_limit_seconds,
                respect_robots=source.respect_robots_txt,
            )
            if res.blocked_reason == "http_403_waf":
                waf_hits += 1
                errors.append(f"Quintype Search {query}: {res.error}")
                continue
            if not res.ok:
                errors.append(f"Quintype Search {query}: {res.error}")
                continue
            search_items = self._parse_quintype_search(res.text, source, cutoff)
            if search_items:
                discovered.extend(search_items)
                if "quintype_search" not in methods_used:
                    methods_used.append("quintype_search")

        # 2. RSS / Atom Feeds
        for rss_url in source.rss_urls:
            total_attempts += 1
            res = self.client.get(
                rss_url,
                rate_limit_seconds=source.rate_limit_seconds,
                respect_robots=source.respect_robots_txt,
            )
            if res.blocked_reason == "http_403_waf":
                waf_hits += 1
                errors.append(f"RSS {rss_url}: {res.error}")
                continue
            if not res.ok:
                errors.append(f"RSS {rss_url}: {res.error}")
                continue
            rss_items = self._parse_rss(res.text, source, cutoff)
            if rss_items:
                discovered.extend(rss_items)
                if "rss" not in methods_used:
                    methods_used.append("rss")

        # 3. XML Sitemaps (crucial for full 14-day coverage beyond RSS entry caps)
        for sm_url in source.sitemap_urls:
            total_attempts += 1
            res = self.client.get(
                sm_url,
                rate_limit_seconds=source.rate_limit_seconds,
                respect_robots=source.respect_robots_txt,
            )
            if res.blocked_reason == "http_403_waf":
                waf_hits += 1
                errors.append(f"Sitemap {sm_url}: {res.error}")
                continue
            if not res.ok:
                errors.append(f"Sitemap {sm_url}: {res.error}")
                continue

            sm_items, sm_errs, sm_waf = self._handle_sitemap(res.text, source, cutoff, now)
            errors.extend(sm_errs)
            waf_hits += sm_waf
            if sm_items:
                discovered.extend(sm_items)
                if "sitemap" not in methods_used:
                    methods_used.append("sitemap")

        # 4. Permitted Section HTML fallback (used when no RSS/sitemap or for additional coverage)
        if source.section_urls and (not discovered or not source.rss_urls):
            for sec_url in source.section_urls:
                total_attempts += 1
                res = self.client.get(
                    sec_url,
                    rate_limit_seconds=source.rate_limit_seconds,
                    respect_robots=source.respect_robots_txt,
                )
                if res.blocked_reason == "http_403_waf":
                    waf_hits += 1
                    errors.append(f"Section {sec_url}: {res.error}")
                    continue
                if not res.ok:
                    errors.append(f"Section {sec_url}: {res.error}")
                    continue
                html_items = self._parse_section_html(res.text, sec_url, source)
                if html_items:
                    discovered.extend(html_items)
                    if "section_html" not in methods_used:
                        methods_used.append("section_html")

        blocked_by_waf = total_attempts > 0 and waf_hits == total_attempts and not discovered
        return discovered, methods_used, errors, blocked_by_waf

    def _parse_quintype_collection(
        self,
        raw_json: str,
        source: SourceConfig,
        cutoff: datetime,
    ) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return items

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "story" and isinstance(node.get("story"), dict):
                    story = node["story"]
                    url = story.get("url") or ""
                    if not url and story.get("slug"):
                        url = urljoin(source.base_url + "/", story["slug"])
                    if not url:
                        return
                    pub_dt = parse_datetime_flexible(
                        story.get("published-at") or story.get("first-published-at")
                    )
                    if pub_dt is not None and pub_dt < cutoff:
                        return
                    headline = story.get("headline") or ""
                    summary = story.get("subheadline") or story.get("summary") or ""
                    items.append(
                        DiscoveredItem(
                            url=url,
                            source_id=source.source_id,
                            publisher=source.publisher,
                            language=source.language,
                            discovery_method="quintype_api",
                            headline=headline,
                            summary=summary,
                            published_at=pub_dt,
                        )
                    )
                    return
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for el in node:
                    _walk(el)

        _walk(data)
        return items

    def _parse_quintype_search(
        self,
        raw_json: str,
        source: SourceConfig,
        cutoff: datetime,
    ) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return items

        stories = data.get("results", {}).get("stories", []) if isinstance(data, dict) else []
        for story in stories:
            if not isinstance(story, dict):
                continue
            url = story.get("url") or ""
            if not url and story.get("slug"):
                url = urljoin(source.base_url + "/", story["slug"])
            if not url:
                continue
            pub_dt = parse_datetime_flexible(
                story.get("published-at") or story.get("first-published-at")
            )
            if pub_dt is not None and pub_dt < cutoff:
                continue
            headline = story.get("headline") or ""
            summary = story.get("subheadline") or story.get("summary") or ""
            items.append(
                DiscoveredItem(
                    url=url,
                    source_id=source.source_id,
                    publisher=source.publisher,
                    language=source.language,
                    discovery_method="quintype_search",
                    headline=headline,
                    summary=summary,
                    published_at=pub_dt,
                )
            )
        return items

    def _parse_rss(
        self,
        xml_text: str,
        source: SourceConfig,
        cutoff: datetime,
    ) -> list[DiscoveredItem]:
        parsed = feedparser.parse(xml_text)
        items: list[DiscoveredItem] = []
        for entry in parsed.entries:
            link = (entry.get("link") or "").strip()
            if not link:
                continue
            pub_raw = entry.get("published") or entry.get("updated") or ""
            pub_dt = parse_datetime_flexible(pub_raw)
            if pub_dt is not None and pub_dt < cutoff:
                continue
            headline = (entry.get("title") or "").strip()
            summary_html = entry.get("summary") or entry.get("description") or ""
            summary = BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)
            items.append(
                DiscoveredItem(
                    url=link,
                    source_id=source.source_id,
                    publisher=source.publisher,
                    language=source.language,
                    discovery_method="rss",
                    headline=headline,
                    summary=summary[:500],
                    published_at=pub_dt,
                )
            )
        return items

    def _handle_sitemap(
        self,
        xml_text: str,
        source: SourceConfig,
        cutoff: datetime,
        now: datetime,
    ) -> tuple[list[DiscoveredItem], list[str], int]:
        errors: list[str] = []
        waf_hits = 0
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            return [], [f"XML parse error: {exc}"], 0

        root_tag = root.tag.split("}")[-1].lower()
        if root_tag == "urlset":
            return _parse_sitemap_urlset(xml_text, source, cutoff), errors, waf_hits

        if root_tag == "sitemapindex":
            child_locs = [
                el.text.strip()
                for el in root.iter()
                if el.tag.split("}")[-1].lower() == "loc" and el.text
            ]
            chosen = _select_relevant_child_sitemaps(child_locs, cutoff=cutoff, now=now)
            all_items: list[DiscoveredItem] = []
            for child_url in chosen:
                res = self.client.get(
                    child_url,
                    rate_limit_seconds=source.rate_limit_seconds,
                    respect_robots=source.respect_robots_txt,
                )
                if res.blocked_reason == "http_403_waf":
                    waf_hits += 1
                    errors.append(f"Child sitemap {child_url}: {res.error}")
                    continue
                if not res.ok:
                    errors.append(f"Child sitemap {child_url}: {res.error}")
                    continue
                all_items.extend(_parse_sitemap_urlset(res.text, source, cutoff))
            return all_items, errors, waf_hits

        return [], errors, waf_hits

    def _parse_section_html(
        self,
        html_text: str,
        page_url: str,
        source: SourceConfig,
    ) -> list[DiscoveredItem]:
        soup = BeautifulSoup(html_text, "html.parser")
        base_netloc = urlparse(source.base_url).netloc.lower()
        seen: set[str] = set()
        items: list[DiscoveredItem] = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            full_url = urljoin(page_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc.lower() != base_netloc:
                continue
            path = parsed.path
            # Skip root/section index pages; keep article-like paths
            if path.count("/") < 2 or len(path) < 15:
                continue
            if source.allowed_path_hints and not any(h in path for h in source.allowed_path_hints):
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            headline = a.get_text(" ", strip=True)
            items.append(
                DiscoveredItem(
                    url=full_url,
                    source_id=source.source_id,
                    publisher=source.publisher,
                    language=source.language,
                    discovery_method="section_html",
                    headline=headline[:250],
                )
            )
        return items
