"""Bangladesh Telecom Intelligence — Premium Editorial Dashboard.

Strictly four primary content sections:
  1. Editorial Masthead
  2. The Daily Brief
  3. Lead Story + Intelligence Feed
  4. Industry Signals
Plus a minimal collapsible System & Control sidebar (manual triggers only).

Launch:
    PYTHONPATH=. .venv/bin/streamlit run bd_telecom_scraper/dashboard/app.py
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

from bd_telecom_scraper.api.server import ensure_api_server_running
from bd_telecom_scraper.dashboard.data_service import DashboardDataService
from bd_telecom_scraper.dashboard.styles import EDITORIAL_CSS
from bd_telecom_scraper.discovery import DHAKA_TZ
from bd_telecom_scraper.llm.briefing import DailyBriefingEngine
from bd_telecom_scraper.llm.client import LocalOllamaClient
from bd_telecom_scraper.llm.config import load_llm_config
from bd_telecom_scraper.llm.pipeline import ArticleIntelligencePipeline

DB_PATH = Path("data/bd_telecom_news.db")


def _api_get_status(api_url: str) -> dict[str, Any]:
    try:
        resp = httpx.get(f"{api_url}/api/news/refresh/status", timeout=4.0)
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return {"is_running": False, "status": "IDLE", "stages": [], "metrics": {}}


def _api_trigger_refresh(api_url: str) -> tuple[bool, str, dict[str, Any]]:
    try:
        resp = httpx.post(
            f"{api_url}/api/news/refresh",
            headers={"X-Requested-By": "bd-telecom-dashboard"},
            json={"days": 14, "max_per_source": 30, "llm_batch_limit": 25},
            timeout=6.0,
        )
        data = resp.json()
        if resp.status_code == 202:
            return True, str(data.get("message") or "Refresh started."), data.get("job", {})
        if resp.status_code == 409:
            return False, "A refresh job is already running.", data.get("job", {})
        return False, f"API returned HTTP {resp.status_code}", {}
    except httpx.HTTPError as exc:
        return False, f"Could not reach local refresh API: {exc}", {}


def _esc(text: object) -> str:
    return html.escape(str(text or ""))


def _render_html(markup: str) -> None:
    """Render raw HTML safely in Streamlit without triggering 4-space Markdown code blocks."""
    compact = "\n".join(line.lstrip() for line in markup.splitlines() if line.strip())
    st.markdown(compact, unsafe_allow_html=True)


def _fmt_dt(iso_str: str | None) -> str:
    if not iso_str:
        return "Date unavailable"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return str(iso_str)[:16]


@st.cache_resource
def _get_service() -> DashboardDataService:
    return DashboardDataService(DB_PATH)


def _render_sidebar(service: DashboardDataService) -> None:
    cfg = load_llm_config()
    metrics = service.repo.get_queue_metrics()
    ollama_ok, installed_models, ollama_err = LocalOllamaClient(cfg).check_health()

    with st.sidebar:
        st.markdown("### System & Operations")
        st.caption("Local-only LLM inference · Zero cloud AI APIs")
        st.markdown("---")

        st.markdown(f"**Ollama Status:** {'Connected' if ollama_ok else 'Unavailable'}")
        st.markdown(f"**Active Model:** `{cfg.model_name}`")
        if not ollama_ok and ollama_err:
            st.caption(f"Connection detail: {ollama_err[:120]}")

        st.markdown("---")
        st.markdown(
            f"**Scraped Articles:** {metrics['total_articles']}  \n"
            f"**AI Completed:** {metrics['completed']}  \n"
            f"**Pending Queue:** {metrics['pending']}  \n"
            f"**Failed:** {metrics['failed']}"
        )
        st.caption(f"Last scrape: {_fmt_dt(metrics['last_scrape_at'])}")
        st.caption(f"Last AI run: {_fmt_dt(metrics['last_ai_processed_at'])}")

        st.markdown("---")
        batch_n = st.number_input("Batch size", min_value=1, max_value=50, value=min(cfg.batch_size, 10), step=1)
        is_running = service.repo.is_job_running("article_analysis") or service.repo.is_job_running("daily_briefing")

        if is_running:
            st.info("An AI job is currently running.")

        if st.button("Process Pending Batch", disabled=is_running or not ollama_ok, use_container_width=True):
            with st.spinner(f"Analyzing up to {batch_n} pending articles with {cfg.model_name}..."):
                pipeline = ArticleIntelligencePipeline(db_path=DB_PATH, config=cfg)
                res = pipeline.process_batch(limit=int(batch_n))
            if res.skipped_locked:
                st.warning("Another AI job is already running.")
            else:
                st.success(f"Completed: {res.completed} | Failed: {res.failed}")
                st.cache_data.clear()
                st.rerun()

        if st.button("Regenerate Daily Brief", disabled=is_running or not ollama_ok, use_container_width=True):
            with st.spinner("Consolidating developments into The Daily Brief..."):
                engine = DailyBriefingEngine(db_path=DB_PATH, config=cfg)
                brief = engine.generate_briefing(force=True)
            if brief.get("status") == "COMPLETED":
                st.success("Daily Brief updated.")
            else:
                st.warning(f"Briefing status: {brief.get('status')} — {brief.get('error_message') or ''}")
            st.cache_data.clear()
            st.rerun()

        if st.button("Refresh Dashboard Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        with st.expander("Reprocess Selected Article"):
            reproc_id = st.number_input("Article ID", min_value=1, value=1, step=1)
            if st.button("Reprocess Article", disabled=is_running or not ollama_ok, use_container_width=True):
                with st.spinner(f"Reprocessing article #{reproc_id}..."):
                    pipeline = ArticleIntelligencePipeline(db_path=DB_PATH, config=cfg)
                    res = pipeline.process_batch(article_ids=[int(reproc_id)], force_reprocess=True)
                st.success(f"Done (completed={res.completed}, failed={res.failed})")
                st.cache_data.clear()
                st.rerun()

        errors = service.repo.get_recent_errors(limit=10)
        with st.expander(f"Processing Errors ({len(errors)})"):
            if not errors:
                st.caption("No failed articles recorded.")
            else:
                for err in errors:
                    st.markdown(
                        f"**#{err['article_id']}** ({err['publisher']}) · retries={err['retry_count']}  \n"
                        f"`{str(err['error_message'] or '')[:160]}`"
                    )

        if installed_models:
            with st.expander("Installed Local Models"):
                st.caption(", ".join(installed_models))


def _render_masthead(service: DashboardDataService, api_url: str) -> dict[str, Any]:
    metrics = service.repo.get_queue_metrics()
    refresh_state = _api_get_status(api_url)
    today_str = datetime.now(DHAKA_TZ).strftime("%A, %d %B %Y")
    last_refresh_iso = refresh_state.get("last_successful_refresh_at") or metrics["last_scrape_at"]
    sync_str = _fmt_dt(last_refresh_iso)

    _render_html(
        f"""
        <div class="masthead-wrap" id="top" style="margin-bottom:0.85rem;">
            <div class="masthead-kicker">
                <span>{today_str} · DHAKA</span>
                <span>LAST SUCCESSFUL REFRESH: {_esc(sync_str)}</span>
            </div>
            <h1 class="masthead-title">BANGLADESH TELECOM INTELLIGENCE</h1>
            <p class="masthead-tagline">
                Independent regulatory, network, and competitive intelligence across Bangladesh’s telecommunications sector.
            </p>
            <div class="masthead-nav">
                <a href="#daily-brief">The Daily Brief</a>
                <a href="#intelligence-feed">Lead Story &amp; Feed</a>
                <a href="#industry-signals">Industry Signals</a>
            </div>
        </div>
        """
    )

    is_running = bool(refresh_state.get("is_running"))
    col_info, col_btn = st.columns([4.2, 1.3], vertical_alignment="center")
    with col_info:
        if is_running:
            st.caption("Pipeline active — collecting, analyzing, and updating intelligence in the background.")
        else:
            st.caption(
                f"{metrics['total_articles']} articles indexed · {metrics['completed']} AI-analyzed · "
                f"Last refresh: {sync_str}"
            )
    with col_btn:
        btn_label = "Refreshing..." if is_running else "Fetch Latest News"
        if st.button(btn_label, disabled=is_running, use_container_width=True, key="fetch_latest_news_btn"):
            ok, msg, job = _api_trigger_refresh(api_url)
            if ok:
                st.session_state["active_refresh_job_id"] = job.get("job_id")
                st.session_state["last_seen_running"] = True
                st.rerun()
            else:
                st.warning(msg)

    return refresh_state


def _render_refresh_monitor(api_url: str, initial_state: dict[str, Any]) -> None:
    """Restrained live progress strip shown only while a refresh job is running or just finished."""
    should_show = bool(initial_state.get("is_running")) or bool(
        st.session_state.get("active_refresh_job_id")
        and initial_state.get("job_id") == st.session_state.get("active_refresh_job_id")
    )
    if not should_show:
        return

    @st.fragment(run_every=2 if initial_state.get("is_running") else None)
    def _live_strip() -> None:
        state = _api_get_status(api_url)
        status = str(state.get("status") or "IDLE")
        running = bool(state.get("is_running"))
        m = state.get("metrics") or {}

        # Auto-refresh full dashboard once when a running job finishes
        if running:
            st.session_state["last_seen_running"] = True
        elif st.session_state.get("last_seen_running"):
            st.session_state["last_seen_running"] = False
            st.cache_data.clear()
            st.rerun()

        badge_cls = "tag-teal" if running or status == "COMPLETED" else "tag-high"
        status_label = "PROCESSING" if running else status.replace("_", " ")

        stage_rows: list[str] = []
        for s in state.get("stages", []):
            st_code = str(s.get("status") or "pending")
            marker = {"completed": "✓", "running": "●", "failed": "✕", "skipped": "–"}.get(st_code, "○")
            detail = s.get("error") or s.get("detail") or st_code.capitalize()
            stage_rows.append(
                f'<div class="refresh-stage-row">'
                f'<span>{marker} {_esc(s.get("label"))}</span>'
                f'<span class="signal-meta">{_esc(detail)}</span>'
                f'</div>'
            )

        err_html = (
            f'<div style="margin-top:0.45rem;font-size:0.8rem;color:#9E4722;">'
            f'Notice: {_esc(state.get("error_message"))}</div>'
            if state.get("error_message")
            else ""
        )

        _render_html(
            f"""
            <div class="refresh-strip">
                <div class="refresh-strip-header">
                    <span class="section-kicker" style="margin:0;">LIVE PIPELINE STATUS · {_esc(state.get("job_id") or "")}</span>
                    <span class="tag-pill {badge_cls}">{_esc(status_label)}</span>
                </div>
                {"".join(stage_rows)}
                <div class="refresh-metrics-bar">
                    Discovered: <strong>{int(m.get("discovered", 0))}</strong> ·
                    Extracted: <strong>{int(m.get("extracted", 0))}</strong> ·
                    Deduplicated: <strong>{int(m.get("deduplicated", 0))}</strong> ·
                    AI Processed: <strong>{int(m.get("processed", 0))}</strong> ·
                    Saved: <strong>{int(m.get("saved", 0))}</strong>
                </div>
                {err_html}
            </div>
            """
        )

    _live_strip()


def _render_daily_brief(service: DashboardDataService) -> None:
    _render_html('<div id="daily-brief"></div>')
    brief = service.repo.get_latest_briefing()

    if not brief or brief.get("status") != "COMPLETED":
        msg = (
            brief.get("error_message")
            if brief and brief.get("error_message")
            else "No executive Daily Brief has been generated yet. Process pending articles and click 'Regenerate Daily Brief' in the Operations panel."
        )
        _render_html(
            f"""
            <div class="brief-container">
                <div class="section-kicker">THE DAILY BRIEF</div>
                <h2 class="brief-headline">Awaiting Sufficient Verified Coverage</h2>
                <p class="brief-intro">{_esc(msg)}</p>
            </div>
            """
        )
        return

    all_ids: list[int] = []
    for dev in brief.get("developments", []):
        all_ids.extend(dev.get("supporting_article_ids", []))
    article_map = service.get_articles_by_ids(list(set(all_ids)))

    dev_blocks: list[str] = []
    for idx, dev in enumerate(brief.get("developments", []), start=1):
        entities = " · ".join(dev.get("key_entities", [])[:6])
        links_html: list[str] = []
        for aid in dev.get("supporting_article_ids", []):
            ref = article_map.get(int(aid))
            if ref:
                links_html.append(
                    f'<a href="{_esc(ref["url"])}" target="_blank" rel="noopener noreferrer">'
                    f'{_esc(ref["publisher"])}</a>'
                )
        sources_line = f'Sources: {", ".join(links_html)}' if links_html else ""
        entity_line = f'<div class="meta-line">{_esc(entities)}</div>' if entities else ""

        dev_blocks.append(
            f'<div class="brief-dev">'
            f'<div class="brief-dev-title">{idx}. {_esc(dev.get("title"))}</div>'
            f'{entity_line}'
            f'<div class="brief-dev-facts"><span class="brief-label">Reported Facts</span>{_esc(dev.get("reported_facts"))}</div>'
            f'<div class="brief-dev-implication"><span class="brief-label">Why It Matters</span>{_esc(dev.get("why_it_matters"))}</div>'
            f'<div class="brief-sources">{sources_line}</div>'
            f'</div>'
        )

    implications_html = (
        f'<div class="brief-dev"><span class="brief-label">Sector Outlook (AI Interpretation)</span> '
        f'<span style="font-size:0.92rem;color:#2E2C28;">{_esc(brief.get("overall_implications"))}</span></div>'
        if brief.get("overall_implications")
        else ""
    )

    _render_html(
        f"""
        <div class="brief-container">
            <div class="section-kicker">
                THE DAILY BRIEF · {_esc(brief.get("period_label"))} · MODEL: {_esc(brief.get("model_name"))}
            </div>
            <h2 class="brief-headline">{_esc(brief.get("headline"))}</h2>
            <p class="brief-intro">{_esc(brief.get("intro_overview"))}</p>
            {"".join(dev_blocks)}
            {implications_html}
        </div>
        """
    )


def _render_article_drawer(art: dict[str, object]) -> None:
    with st.expander("Read full AI analysis & original metadata"):
        if art["ai_completed"]:
            takeaways = art["key_takeaways"]
            if takeaways:
                st.markdown("**Key Takeaways (Reported Facts)**")
                for t in takeaways:
                    st.markdown(f"- {t}")
            facts = art["reported_facts"]
            if facts:
                st.markdown("**Explicit Figures, Dates & Facts**")
                for f in facts:
                    st.markdown(f"- {f}")
            if art["industry_implications"]:
                st.markdown("**AI Interpretation — Industry Implications**")
                st.markdown(f"> {art['industry_implications']}")
            orgs = ", ".join(art["organizations"]) if art["organizations"] else "None identified"
            ops = ", ".join(art["operators"]) if art["operators"] else "None"
            st.caption(
                f"Operators: {ops} · Organizations: {orgs} · "
                f"Sentiment: {art['sentiment']} ({art['sentiment_confidence']}) · "
                f"Impact: {art['industry_impact']} · Relevance: {art['telecom_relevance']} · "
                f"Model: {art['model_name']}"
            )
        else:
            st.caption("This article is queued for local LLM analysis. Run 'Process Pending Batch' in the Operations sidebar.")

        st.markdown(
            f"**Original Headline ({str(art['language']).upper()}):** {art['original_headline']}  \n"
            f"**Original Source:** [{art['publisher']}]({art['url']}) · Article ID `#{art['id']}`"
        )


def _render_lead_and_feed(service: DashboardDataService) -> None:
    _render_html('<hr class="section-rule" id="intelligence-feed">')
    _render_html('<div class="section-kicker">LEAD STORY &amp; INTELLIGENCE FEED</div>')

    lead = service.get_lead_story()
    if lead:
        impact_cls = "tag-high" if lead["industry_impact"] == "High" else ""
        ops_html = "".join(f'<span class="tag-pill tag-teal">{_esc(op)}</span>' for op in lead["operators"])
        _render_html(
            f"""
            <div class="lead-story">
                <div class="meta-line">
                    <span class="meta-accent">{_esc(lead["main_category"])}</span>
                    &nbsp;·&nbsp; {_esc(lead["publisher"])}
                    &nbsp;·&nbsp; {_esc(_fmt_dt(lead["published_at"]))}
                </div>
                <h2 class="lead-headline">{_esc(lead["display_headline"])}</h2>
                <p class="lead-summary">{_esc(lead["summary"])}</p>
                <div style="margin-bottom:0.55rem;">
                    <span class="tag-pill {impact_cls}">Impact: {_esc(lead["industry_impact"])}</span>
                    {ops_html}
                    <a class="source-link" href="{_esc(lead["url"])}" target="_blank" rel="noopener noreferrer">
                        Read original at {_esc(lead["publisher"])} &rarr;
                    </a>
                </div>
            </div>
            """
        )
        _render_article_drawer(lead)

    # Essential editorial filters (compact single row)
    opts = service.get_filter_options()
    c1, c2, c3, c4, c5 = st.columns([1.1, 1.3, 1.5, 1.1, 1.5])
    with c1:
        date_sel = st.selectbox("Date", ["All Dates", *opts["dates"]], label_visibility="collapsed")
    with c2:
        pub_sel = st.selectbox("Publisher", ["All Publishers", *opts["publishers"]], label_visibility="collapsed")
    with c3:
        cat_sel = st.selectbox("Category", ["All Categories", *opts["categories"]], label_visibility="collapsed")
    with c4:
        op_sel = st.selectbox("Operator", ["All Operators", *opts["operators"]], label_visibility="collapsed")
    with c5:
        query = st.text_input("Search", placeholder="Search headlines, entities...", label_visibility="collapsed")

    feed = service.get_intelligence_feed(
        date_filter=date_sel,
        publisher=pub_sel,
        category=cat_sel,
        operator=op_sel,
        search_query=query,
        exclude_id=lead["id"] if lead and date_sel == "All Dates" and not query else None,
        limit=25,
    )

    if not feed:
        st.caption("No articles match the selected filters.")
        return

    for art in feed:
        ops_tags = "".join(f'<span class="tag-pill tag-teal">{_esc(o)}</span>' for o in art["operators"][:3])
        impact_tag = (
            f'<span class="tag-pill {"tag-high" if art["industry_impact"] == "High" else ""}">'
            f'{_esc(art["industry_impact"])} Impact</span>'
            if art["industry_impact"]
            else '<span class="tag-pill">Pending AI</span>'
        )
        _render_html(
            f"""
            <div class="feed-item">
                <div class="meta-line">
                    <span class="meta-accent">{_esc(art["main_category"])}</span>
                    &nbsp;·&nbsp; {_esc(art["publisher"])}
                    &nbsp;·&nbsp; {_esc(_fmt_dt(art["published_at"]))}
                </div>
                <div class="feed-headline">{_esc(art["display_headline"])}</div>
                <div class="feed-summary">{_esc(art["summary"])}</div>
                <div>
                    {impact_tag}{ops_tags}
                    <a class="source-link" href="{_esc(art["url"])}" target="_blank" rel="noopener noreferrer">
                        Original article &rarr;
                    </a>
                </div>
            </div>
            """
        )
        _render_article_drawer(art)


def _render_industry_signals(service: DashboardDataService) -> None:
    _render_html('<hr class="section-rule" id="industry-signals">')
    signals = service.get_industry_signals(window_days=7)

    _render_html(
        f'<div class="section-kicker">INDUSTRY SIGNALS · {_esc(signals["period_label"])}</div>'
    )
    st.caption(
        "Momentum compares the latest 7-day window against the immediately preceding 7 days, "
        "separating emerging shifts from raw article volume."
    )

    col_left, col_right = st.columns(2, gap="large")

    with col_left:
        st.markdown("#### Topic Momentum")
        if not signals["topics"]:
            st.caption("Process articles with the local LLM to populate categorized topic signals.")
        else:
            for t in signals["topics"]:
                mom_cls = "tag-high" if t["momentum"] == "Emerging" else ("tag-teal" if t["momentum"] == "Rising" else "")
                _render_html(
                    f"""
                    <div class="signal-table-row">
                        <span class="signal-name">{_esc(t["topic"])}</span>
                        <span class="signal-meta">
                            <span class="tag-pill {mom_cls}">{_esc(t["momentum"])}</span>
                            {t["current_count"]} vs {t["prior_count"]} prior · {t["high_impact_count"]} high-impact
                        </span>
                    </div>
                    """
                )

    with col_right:
        st.markdown("#### Operator & Regulator Mentions")
        if not signals["operators"]:
            st.caption("No operator mentions recorded in the current window.")
        else:
            for op in signals["operators"]:
                delta = op["delta"]
                delta_str = f"+{delta}" if delta > 0 else str(delta)
                _render_html(
                    f"""
                    <div class="signal-table-row">
                        <span class="signal-name">{_esc(op["operator"])}</span>
                        <span class="signal-meta">{op["current_count"]} articles ({delta_str} vs prior 7d)</span>
                    </div>
                    """
                )

        if signals["regulatory_highlights"]:
            st.markdown("#### Key Regulatory Developments")
            for reg in signals["regulatory_highlights"]:
                _render_html(
                    f"""
                    <div class="signal-table-row">
                        <a class="source-link" href="{_esc(reg["url"])}" target="_blank" rel="noopener noreferrer">
                            {_esc(reg["headline"])}
                        </a>
                        <span class="signal-meta">{_esc(reg["date"])}</span>
                    </div>
                    """
                )


def main() -> None:
    st.set_page_config(
        page_title="Bangladesh Telecom Intelligence",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(EDITORIAL_CSS, unsafe_allow_html=True)

    try:
        service = _get_service()
    except Exception as exc:
        st.error(f"Could not connect to database at `{DB_PATH}`: {exc}")
        return

    api_url = ensure_api_server_running(DB_PATH)
    _render_sidebar(service)
    refresh_state = _render_masthead(service, api_url)
    _render_refresh_monitor(api_url, refresh_state)
    _render_daily_brief(service)
    _render_lead_and_feed(service)
    _render_industry_signals(service)


if __name__ == "__main__":
    main()
