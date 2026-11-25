"""Compliant, zero-cost Facebook Page telecom intelligence sub-module.

Extends `bd_telecom_scraper` additively without modifying the existing news scraper.
Supports:
  - Method 1: Official Meta Graph API v27.0 (managed Pages + PPCA-ready third-party Pages)
  - Method 2: Manual Public Content Ingestion (CLI single post + batch JSON/CSV)
"""

from .models import FacebookPost, FacebookRunReport, FacebookSource, FacebookSyncState
from .pipeline import FacebookIntelPipeline
from .source_registry import DEFAULT_FACEBOOK_SOURCES, get_facebook_sources
from .storage import FacebookStore

__all__ = [
    "DEFAULT_FACEBOOK_SOURCES",
    "FacebookIntelPipeline",
    "FacebookPost",
    "FacebookRunReport",
    "FacebookSource",
    "FacebookStore",
    "FacebookSyncState",
    "get_facebook_sources",
]
