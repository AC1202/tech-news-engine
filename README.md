# Tech News Engine

A personal algorithmic news curation system that aggregates, scores, and pushes daily briefings via Telegram — across three independent engines covering Technology, Sports, and US General News.

---

## What It Does

Instead of scrolling through dozens of feeds manually, this system runs on a schedule, pulls articles from curated RSS sources, ranks them using a custom scoring algorithm, deduplicates overlapping stories, and delivers a concise bilingual (English + Traditional Chinese) digest every day.

Three engines run on independent schedules:

| Engine | Push Time (ET) | Stories | Sources |
|--------|---------------|---------|---------|
| **Tech News** (`main.py`) | 1:00 PM | Top 7 | 14 sources (Bloomberg, TechCrunch, The Information, etc.) |
| **US General News** (`news_main.py`) | 9:00 AM | Top 5 | 17 sources (AP, Reuters, NYT, WaPo, DCist, BBC, etc.) |
| **Sports News** (`sports_main.py`) | 5:00 PM | Top 5 | 10 sources (ESPN, Yahoo Sports, Google News RSS) |

---

## Scoring Algorithm (Tech Engine)

Each article receives a composite score before anything is delivered:

```
Base = (W_site × 0.30) + (W_fresh × 0.25) + (W_cross × 0.30) + (W_rank × 0.15)
FinalScore = Base × EntityMultiplier × ScarcityMultiplier + ScarcityAdditive
```

**Key factors:**

- **W_site** — Source credibility weight (The Information: 95, Bloomberg: 94, Ars Technica: 68, ...)
- **W_fresh** — Recency score (`<8h → 100`, `8–24h → 90`, `>48h → DROP`)
- **W_cross** — Cross-source signal: same story covered by multiple outlets scores higher
- **W_rank** — RSS position signal
- **Entity tiers** — Domain-relevant multipliers (e-commerce, fintech, payments: ×1.25; anti-keywords: ×0.85)
- **Scarcity bonus** — Additive premium for exclusive/paywalled sources with no substitute

**Filters applied in pipeline order:**
1. Hard 48h age cutoff
2. Aggregator/roundup title blocklist
3. Cross-source count calculation
4. Score all articles
5. Haiku AI relevance pre-filter (1–10 score; drops articles scoring < 5)
6. TF-IDF cosine similarity deduplication (threshold 0.65)
7. Quality floor (score ≥ 75)
8. Topic diversity cap (max 2 per domain)
9. Final top-7 selection

---

## Bilingual Output (Claude Haiku)

Each story is summarized using Claude Haiku (`claude-haiku-4-5-20251001`) with strict factual-only constraints — no analysis, no inference, no added context beyond what the source states. Output is in both English and Traditional Chinese.

Example output format:
```
#1 — Score: 118.4

EN:  Stripe acquires stablecoin platform Bridge for $1.1B
中:  Stripe 以 11 億美元收購穩定幣平台 Bridge

Source: TechCrunch + 3 sources | 6h ago | Tag: Payments / M&A

Summary (EN): Stripe has agreed to acquire Bridge, a stablecoin payments
infrastructure company, for approximately $1.1 billion. The deal marks one
of the largest acquisitions in the crypto payments space and signals Stripe's
intent to expand into stablecoin settlement rails for merchants.

摘要（中）：Stripe 同意以約 11 億美元收購穩定幣支付基礎設施公司 Bridge，
為加密支付領域規模最大的收購案之一，顯示 Stripe 擴展穩定幣結算的戰略意圖。

🔗 https://techcrunch.com/...
```

---

## Recurrence Deduplication

Sports and US News engines maintain a rolling history file (`sports_history.json`, `news_history.json`) committed back to the repo after each run. Stories with TF-IDF cosine similarity ≥ 0.85 to any story in the past 7 days are hard-excluded; 0.70–0.85 receives a ×0.60 score penalty.

---

## Infrastructure

- **Trigger:** GitHub Actions `workflow_dispatch`, called daily by [cron-job.org](https://cron-job.org) via HTTP POST with a GitHub PAT
- **Secrets:** `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` stored as GitHub Actions secrets
- **No server required** — runs entirely on GitHub-hosted runners

---

## Project Structure

```
tech-news-engine/
├── main.py                          # Tech News Engine v3.5
├── sports_main.py                   # Sports News Engine v1.1
├── news_main.py                     # US General News Engine v1.0
├── requirements.txt
├── sports_history.json              # 7-day recurrence state (sports)
├── news_history.json                # 2-day recurrence state (US news)
├── .env.example                     # Required environment variables
├── .github/workflows/
│   ├── daily_news.yml               # Tech push at 1pm ET
│   ├── daily_sports_news.yml        # Sports push at 5pm ET
│   └── daily_us_news.yml            # US news push at 9am ET
└── PRD_Daily_Tech_News_Engine_v3.4.md   # Product spec for tech engine
```

---

## Design Principles

- **Bias, don't censor** — off-topic articles are score-penalized, not removed
- **No deployment complexity** — GitHub Actions + free-tier cron trigger, zero infra cost beyond API usage
- **Scoring and summarization are independent** — entity tier multipliers affect ranking only; summaries are purely factual regardless of topic domain
- **Cost-efficient AI usage** — Claude Haiku at ~$0.03/day for all three engines combined

---

## License

MIT — see [LICENSE](LICENSE)
