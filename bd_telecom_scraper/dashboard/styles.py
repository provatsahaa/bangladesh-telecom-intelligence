"""Editorial CSS design system inspired by Financial Times & Monocle.

Warm ivory canvas, deep charcoal ink, restrained muted copper & deep teal accents,
hairline rules, refined serif/sans typography hierarchy, zero SaaS/gradient clutter.
"""

EDITORIAL_CSS = """
<style>
/* --- Base Canvas & Typography --- */
html, body, [data-testid="stAppViewContainer"], .stApp {
    background-color: #FAF8F4 !important;
    color: #1A1917 !important;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
}

[data-testid="stHeader"] {
    background-color: rgba(250, 248, 244, 0.92) !important;
    border-bottom: 1px solid #E5E0D6;
}

[data-testid="stSidebar"] {
    background-color: #F3EFE8 !important;
    border-right: 1px solid #DFDAD0 !important;
}

.block-container {
    max-width: 1080px !important;
    padding-top: 2.2rem !important;
    padding-bottom: 4.5rem !important;
    padding-left: 2.2rem !important;
    padding-right: 2.2rem !important;
}

/* --- Editorial Masthead --- */
.masthead-wrap {
    border-top: 3px solid #1A1917;
    border-bottom: 1px solid #1A1917;
    padding: 1.5rem 0 1.15rem 0;
    margin-bottom: 2.25rem;
}
.masthead-kicker {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.74rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #5C5852;
    margin-bottom: 0.65rem;
}
.masthead-title {
    font-family: Georgia, "Times New Roman", "Palatino Linotype", serif;
    font-size: 2.45rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: #1A1917;
    line-height: 1.1;
    margin: 0 0 0.45rem 0;
}
.masthead-tagline {
    font-family: Georgia, "Times New Roman", serif;
    font-style: italic;
    font-size: 1.02rem;
    color: #4A4741;
    margin: 0 0 1rem 0;
}
.masthead-nav {
    display: flex;
    gap: 1.8rem;
    border-top: 1px solid #DFDAD0;
    padding-top: 0.65rem;
    font-size: 0.78rem;
    letter-spacing: 0.09em;
    text-transform: uppercase;
}
.masthead-nav a {
    color: #1A1917 !important;
    text-decoration: none !important;
    border-bottom: 1px solid transparent;
    padding-bottom: 2px;
    transition: border-color 0.2s ease, color 0.2s ease;
}
.masthead-nav a:hover {
    color: #9E4722 !important;
    border-bottom-color: #9E4722;
}

/* --- Section Headers --- */
.section-kicker {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #9E4722;
    margin-bottom: 0.35rem;
}
.section-rule {
    border: none;
    border-top: 1px solid #1A1917;
    margin: 2.6rem 0 1.4rem 0;
}
.hairline-rule {
    border: none;
    border-top: 1px solid #DFDAD0;
    margin: 1.25rem 0;
}

/* --- The Daily Brief --- */
.brief-container {
    background-color: #F4F0E8;
    border-top: 2px solid #9E4722;
    border-bottom: 1px solid #D9D2C5;
    padding: 2rem 2.2rem;
    margin-bottom: 2.5rem;
}
.brief-headline {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1.85rem;
    font-weight: 700;
    line-height: 1.22;
    color: #1A1917;
    margin: 0.25rem 0 0.8rem 0;
}
.brief-intro {
    font-size: 1.02rem;
    line-height: 1.65;
    color: #2E2C28;
    margin-bottom: 1.5rem;
}
.brief-dev {
    border-top: 1px solid #DDD6C9;
    padding: 1.15rem 0;
}
.brief-dev-title {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1.16rem;
    font-weight: 700;
    color: #1A1917;
    margin-bottom: 0.45rem;
}
.brief-dev-facts {
    font-size: 0.94rem;
    line-height: 1.6;
    color: #242320;
    margin-bottom: 0.45rem;
}
.brief-dev-implication {
    font-size: 0.9rem;
    line-height: 1.55;
    color: #3D4A4B;
    border-left: 2px solid #1B5E63;
    padding-left: 0.85rem;
    margin: 0.5rem 0 0.55rem 0;
}
.brief-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #1B5E63;
    margin-right: 0.35rem;
}
.brief-sources {
    font-size: 0.8rem;
    color: #5C5852;
}
.brief-sources a {
    color: #9E4722 !important;
    text-decoration: none;
    border-bottom: 1px solid #D9B7A7;
}
.brief-sources a:hover {
    border-bottom-color: #9E4722;
}

/* --- Lead Story --- */
.lead-story {
    padding: 0.4rem 0 1.6rem 0;
    border-bottom: 1px solid #1A1917;
    margin-bottom: 1.6rem;
}
.lead-headline {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1.95rem;
    font-weight: 700;
    line-height: 1.2;
    color: #1A1917;
    margin: 0.35rem 0 0.7rem 0;
}
.lead-summary {
    font-size: 1.02rem;
    line-height: 1.68;
    color: #2B2926;
    margin-bottom: 0.9rem;
}

/* --- Editorial Meta & Tags --- */
.meta-line {
    font-size: 0.78rem;
    color: #5C5852;
    letter-spacing: 0.02em;
    margin-bottom: 0.4rem;
}
.meta-accent {
    color: #9E4722;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.7rem;
}
.tag-pill {
    display: inline-block;
    font-size: 0.69rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 2px 7px;
    border: 1px solid #D5CEC2;
    color: #3B3833;
    background-color: #F4F0E8;
    margin-right: 6px;
}
.tag-high {
    border-color: #9E4722;
    color: #9E4722;
    background-color: #FBF3EE;
}
.tag-teal {
    border-color: #1B5E63;
    color: #1B5E63;
    background-color: #EEF5F5;
}

/* --- Compact Intelligence Feed --- */
.feed-item {
    padding: 1.05rem 0;
    border-bottom: 1px solid #E5E0D6;
}
.feed-headline {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 1.18rem;
    font-weight: 700;
    line-height: 1.3;
    color: #1A1917;
    margin: 0.25rem 0 0.4rem 0;
}
.feed-summary {
    font-size: 0.92rem;
    line-height: 1.58;
    color: #363430;
    margin-bottom: 0.45rem;
}
.source-link {
    font-size: 0.8rem;
    color: #9E4722 !important;
    text-decoration: none !important;
    font-weight: 500;
}
.source-link:hover {
    text-decoration: underline !important;
}

/* --- Industry Signals --- */
.signal-table-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.55rem 0;
    border-bottom: 1px solid #E5E0D6;
    font-size: 0.9rem;
}
.signal-name {
    font-weight: 500;
    color: #1A1917;
}
.signal-meta {
    font-size: 0.78rem;
    color: #5C5852;
}

/* --- Restrained Editorial Refresh Progress Strip --- */
.refresh-strip {
    background-color: #F4F0E8;
    border-top: 1px solid #1B5E63;
    border-bottom: 1px solid #DFDAD0;
    padding: 0.95rem 1.25rem;
    margin: -1.1rem 0 1.8rem 0;
}
.refresh-strip-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.55rem;
}
.refresh-stage-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.25rem 0;
    border-top: 1px solid #E5E0D6;
    font-size: 0.82rem;
    color: #2E2C28;
}
.refresh-metrics-bar {
    margin-top: 0.6rem;
    padding-top: 0.5rem;
    border-top: 1px solid #DFDAD0;
    font-size: 0.77rem;
    letter-spacing: 0.03em;
    color: #5C5852;
}

/* --- Clean Streamlit Widget Overrides --- */
div[data-testid="stExpander"] {
    border: none !important;
    background-color: transparent !important;
}
div[data-testid="stExpander"] details {
    border: none !important;
    background-color: transparent !important;
}
div[data-testid="stExpander"] summary {
    padding-left: 0 !important;
    font-size: 0.82rem !important;
    color: #1B5E63 !important;
}
.stButton > button {
    border-radius: 2px !important;
    border: 1px solid #1A1917 !important;
    background-color: #FAF8F4 !important;
    color: #1A1917 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
}
.stButton > button:hover {
    background-color: #1A1917 !important;
    color: #FAF8F4 !important;
}
</style>
"""
