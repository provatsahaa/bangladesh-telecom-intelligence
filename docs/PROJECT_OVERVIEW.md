# Project Overview — Bangladesh Telecom Intelligence

## What This Platform Is

**Bangladesh Telecom Intelligence** is a locally hosted, AI-assisted industry monitoring and executive briefing platform built specifically for Bangladesh's telecommunications ecosystem. It aggregates Bangla and English telecom coverage from national newspapers, trade publications, and verified social channels, structures raw reporting using a local language model, and presents consolidated daily intelligence through a clean editorial dashboard.

---

## Why Bangladesh's Telecom Industry Needs This

Bangladesh's telecommunications market is dynamic, highly regulated, and fiercely competitive—spanning four mobile network operators (**Grameenphone**, **Robi**, **Banglalink**, **Teletalk**), state-owned infrastructure entities (**BTCL**, **BSCCL**), regulatory bodies (**BTRC**, **Posts and Telecommunications Division**), industry associations (**AMTOB**, **ISPAB**), tower/fiber companies (**Edotco**, **Summit**, **Fiber@Home**), satellite entrants (**Starlink**), and mobile financial services (**bKash**, **Nagad**).

Decision-makers face three recurring operational challenges:
1. **Bilingual Media Split:** Critical regulatory updates and tariff/spectrum debates often break first in Bangla dailies before appearing in English business press.
2. **High Signal-to-Noise Ratio:** Wire-service syndication causes the same press release to appear across 5–10 portals, making it hard to tell what is genuinely new or emerging.
3. **Cost & Privacy Barriers:** Enterprise media-intelligence subscriptions are expensive, while pasting internal monitoring corpora into third-party cloud AI services introduces recurring costs and privacy concerns.

---

## Intended Users & Key Use Cases

| User Persona | Core Use Cases |
| :--- | :--- |
| **Telecom Strategy & Competitive Intelligence Teams** | Track competitor launches (4G/5G, Starlink partnerships, digital apps), M&A/investment moves (VEON, Telenor, Axiata), and executive appointments across English and Bangla media. |
| **Regulatory & Corporate Affairs Teams** | Monitor BTRC policy directives, spectrum auction/renewal negotiations, SIM/telecom taxation debates, and unified licensing developments with direct source citations. |
| **Network & Technology Leaders** | Follow fiber, tower sharing, submarine cable, Open RAN, and telecom AI deployments locally and globally. |
| **Executives & Senior Management** | Read a 2-minute **Daily Brief** that deduplicates repetitive reporting, separates verified facts from strategic implications, and links straight to primary articles. |

---

## Main Capabilities (Current Scope)

- **Multi-Source Compliant Discovery:** Automated 14-day scraping across 18 Bangladeshi news portal editions (honoring `robots.txt` and rate limits) plus optional Tavily Search & Extract fallback for additional Bangladesh and global trade press.
- **Compliant Social Channel Support:** Verified registry of 24 telecom-relevant Facebook Pages supporting official Meta Graph API `v27.0` (when authorized) and structured manual ingestion (immediately available).
- **Local Bilingual AI Structuring:** Runs `qwen2.5:3b` via Ollama on local hardware (tested on an 8 GB MacBook Air M1) to normalize Bangla and English articles into English headlines, factual summaries, key takeaways, 12 canonical telecom categories, operator/entity tags, sentiment, impact ratings, and separated strategic implications.
- **Executive Daily Brief & Momentum Signals:** Consolidates related articles into distinct developments and compares 7-day topic/operator frequency against the prior 7-day window to separate emerging shifts from baseline coverage volume.
- **One-Click Refresh Workflow:** A single **Fetch Latest News** button triggers the end-to-end backend orchestrator asynchronously with live progress tracking.

---

## Business Value

- **Zero Recurring LLM Cost:** Runs entirely on local open-weight models via Ollama (optional Tavily discovery fits well within its 1,000-credit/month free tier).
- **Auditability & Trust:** Original articles and languages are never overwritten; every claim in The Daily Brief links directly to supporting source articles; reported facts are visually separated from AI interpretation.
- **Extensible Foundation:** Clean SQLite schema and modular Python subpackages allow downstream analytics (longitudinal operator benchmarking, alerting, scheduled digests) to be layered on without rewriting collection or storage.

---

## Future Possibilities *(Planned — Outside Current Scope)*

- Automated daily background scheduling and email/Slack digest delivery
- Multi-week operator share-of-voice and sentiment trend tracking
- Automated PDF/Markdown briefing exports for executive distribution
- Optional integration with verified Meta Business / PPCA credentials for automated public Page syncing
