# PRD — Daily US Tech News Recommendation Engine

**Version:** 3.4 | **Date:** May 18, 2026 | **Status:** MVP-ready | **Owner:** Alvin Chen

---

## Changelog

| Version | Change |
|---------|--------|
| v3.4 | §5.5 REMOVED Key Point (EN + zh-TW). Output: bilingual headline + bilingual summary + source tag. Token budget reduced ~37%. |
| v3.3 | §3.1 HARD 48h cutoff. §5.1 Aggregator filter. §5.5 Bilingual output. §5.6 Paywalled content. |

---

## 1. Overview & Goals

Algorithmic curation system that filters mainstream noise and surfaces high-value technology news. Primary user has explicit professional bias toward E-commerce, Payments, FinTech, Marketplace, Digital Retail, and Omnichannel.

### 1.1 Core Metrics

- **Alpha Signal Discovery** — % of pushed stories the user could not have found via mainstream feeds.
- **Signal-to-Noise Ratio** — % of pushed stories the user reads beyond the headline. Target: ≥ 30%.
- **Time-to-Brief** — Total time spent on the daily digest. Target: 15–20 minutes.

### 1.2 Delivery Specification

- **Delivery time:** 13:00 US Eastern, every day (GitHub Actions cron).
- **Volume:** Up to 7 stories per day.
- **Format:** Bilingual headline + bilingual summary (60–80 words each) + source tag + URL.
- **Channel:** Telegram bot.

---

## 2. User Profile & Scope

Senior PM with background in SEA e-commerce, fintech, payments; MBA candidate exploring digital retail consulting.

### 2.1 In Scope

- **Core:** E-commerce, Payments, FinTech, Marketplace, Digital Retail, Omnichannel.
- **Adjacent:** Applied AI in commerce / B2B, PropTech, SEA tech, Taiwan tech.
- **Mainstream allowed:** Major hardware launches, large platform announcements, market-moving regulatory actions.

### 2.2 Out of Scope

- Pure gaming, gaming hardware reviews, esports.
- Automotive unless commerce/retail-related.
- Generic CES-style consumer electronics roundups.
- Crypto price commentary (crypto infrastructure / payments rails ARE in scope).

> **Design Principle: Bias, Don't Censor.** Off-domain stories are demoted (Anti-Entity × 0.85), not removed.

---

## 3. Data Sources & Retrieval

### 3.1 Retrieval Window — HARD 48-Hour Cutoff

Retrieval is the FIRST filter. Items older than 48 hours are discarded immediately.

```
Rule: keep only items where now() − published_at ≤ 48 hours
Implementation: parse RSS <pubDate>. Fallback: <dc:date>. If absent → discard.
Timezone: all timestamps converted to UTC.
```

W_fresh values: `<8h → 100`, `8–24h → 90`, `24–48h → 70`. The >48h bucket is removed.

### 3.2 Source Table & Site Weights (W_site)

| Source | W_site | Category |
|--------|--------|----------|
| The Information | 95 | Exclusive / SV Scoops |
| Bloomberg Tech | 94 | Finance / Markets |
| Stratechery | 93 | Strategy Analysis |
| TechCrunch | 92 | Deal Flow |
| PYMNTS | 90 | Payments / Commerce |
| VentureBeat | 90 | AI / Enterprise |
| The Verge | 88 | Mainstream / Product |
| Ars Technica | 86 | Hardcore Engineering |
| Retail Dive | 86 | Retail / Omnichannel |
| MIT Technology Review | 85 | Deep Tech / Research |
| Rest of World | 85 | Emerging Markets |
| Recode (Vox) | 84 | Policy / Business |
| Wired | 82 | Long-form Culture |
| Engadget | 75 | Consumer Hardware |

---

## 4. Scoring Algorithm

### 4.1 Base Score

```
Base = (W_site × 0.30) + (W_rank × 0.20) + (W_fresh × 0.20) + (W_cross × 0.30)
```

- **W_fresh:** `<8h → 100`, `8–24h → 90`, `24–48h → 70`
- **W_cross:** `3+ sources → 100`, `2 → 90`, `1 → 80`

### 4.2 W_rank — Observable Position Signals

```
W_rank = (Hero × 0.50) + (RSS_position × 0.20) + (Memeorandum × 0.30)
```

| Signal | Score |
|--------|-------|
| Hero / featured slot | +40 |
| Above-fold list | +25 |
| Regular feed only | +10 |
| RSS position 1 | +30 |
| RSS position 2–3 | +20 |
| RSS position 4–10 | +10 |
| Memeorandum rank 1–10 | +25 |
| Memeorandum rank 11–30 | +15 |
| Memeorandum rank 31–100 | +8 |
| Not on Memeorandum | +0 |

> Fallback: if Memeorandum unreachable → renormalize to Hero 0.71 / RSS 0.29.

### 4.3 Bonus Multipliers

**Entity Match (at most one tier applies per story):**

| Tier | Multiplier | Keywords |
|------|-----------|---------| 
| Tier 1 | × 1.25 | Payments, Marketplace, E-commerce M&A, SEA Tech, PropTech |
| Tier 2 | × 1.15 | AI B2B/Commerce, FinTech, Taiwan |
| Tier 3 | × 1.10 | General AI, Tech Policy |
| Anti-Entity | × 0.85 | Consumer electronics reviews, gaming hardware, automotive |

**Scarcity Bonus (single-source premium):**

| Source | Multiplier | Additive | Constraint |
|--------|-----------|---------|------------|
| The Information | × 1.35 | +15 pts | Always |
| Ars Technica | × 1.25 | +10 pts | Always |
| TechCrunch | × 1.15 | +8 pts | Entity T1/T2 only |

**Information Gain (× 1.20):**
Whitelist: `Revenue, YoY, Funding, Valuation, Series A/B/C/D, GMV, Take Rate, ARR, Churn, IPO, EBITDA`

### 4.4 Recurrence Penalty

| Similarity to Prior Push | Action |
|--------------------------|--------|
| < 0.70 | No penalty |
| 0.70 – 0.85 | × 0.60 |
| ≥ 0.85 | HARD EXCLUDE |

### 4.5 Final Score Formula

```
FinalScore = (Base × Entity × Scarcity_mult × InfoGain × AntiEntity × Recurrence) + Scarcity_additive
```

---

## 5. Filtering, Diversity & Output

### 5.1 Aggregator / Roundup Filter

Apply regex blocklist against item title BEFORE clustering. If match → DROP.

Blocked patterns: `Top N`, `Weekly`, `This Week`, `Week in Review`, `Roundup`, `Wrap-up`, `Recap`, `Best of`, `Biggest of`, `X things you`, `Daily Digest`, `Daily Brief`, `Newsletter`, `Morning Brief`

### 5.2 Clustering & Deduplication

- Compute embedding cosine similarity.
- Similarity ≥ 0.70 → same story cluster.
- Keep highest-scoring story per cluster.

### 5.3 Topic Diversity

Hard limit: max 2 stories from the same primary entity in the final 7.

### 5.4 Quality Floor

- Hard threshold: FinalScore ≥ 75.
- If < 3 stories pass: deliver what's available + append `📭 Slow news day.`

### 5.5 Output Format (v3.4 — Key Point Removed)

Each story:

```
[#1 — Score: 122.0]

EN:  Bain Capital closes $10.5B Asia Fund VI
中:  Bain Capital 完成 105 億美元亞洲第六期基金募資

Source: Bloomberg + 3 sources | 18h ago | Tag: FinTech / Fundraising

Summary (EN, 60–80 words):
[generated by Claude Haiku]

摘要（中，60–80 字）：
[translated by Claude Haiku]

🔗 https://...
```

**Rules:**
- ONE EN headline (from source).
- ONE zh-TW headline — translate via Haiku. Preserve all proper nouns and finance acronyms in English.
- ONE EN summary — generate 60–80 words via Haiku.
- ONE zh-TW summary — translate EN summary via Haiku.
- Source tag: `[source name] + [N sources] | [Xh ago] | Tag: [entity category]`
- URL: direct article URL, no shorteners.

**Token budget per story (v3.4):** ~1,200 tokens per day across 7 stories.

### 5.6 Paywalled Content (The Information)

- Retrieve via direct RSS only: `https://www.theinformation.com/feed`
- Do NOT rely on Google search results.
- Use RSS snippet for headline.
- Cross-reference details via Bloomberg / Reuters for summary.
- Always cite original The Information URL.

---

## 6. Delivery & Operations

### 6.1 Schedule

GitHub Actions cron: `0 17 * * *` (13:00 ET = 17:00 UTC, adjusts for DST via IANA tz).

### 6.2 Telegram Delivery

- Bot token: GitHub Actions secret `TELEGRAM_BOT_TOKEN`.
- Chat ID: secret `TELEGRAM_CHAT_ID`.
- One message per story.
- On failure: retry 3× with 60s interval. After 3 failures: send error alert.

### 6.3 SLA & Error Handling

| Failure Mode | Handling |
|-------------|----------|
| Source scraper failure | Retry 3×, 60s interval. Skip source if all fail. |
| Telegram unreachable | Retry 3×. Send fallback error notification. |
| Embedding model unavailable | Skip dedup. Mark digest `⚠ Dedup degraded.` |
| Memeorandum unreachable | Skip (d) signal. Renormalize W_rank. |
| < 3 stories pass Quality Floor | Deliver what passes. Append `📭 Slow news day.` |

---

## 7. Acceptance Criteria

- On 7 consecutive delivery days, ≥ 5 days deliver 3–7 stories.
- ZERO stories older than 48 hours.
- ZERO aggregator/roundup articles.
- On any day The Information has a relevant scoop → appears in top 3.
- Every delivered story conforms to v3.4 format.
- Zero consecutive-day story repeats in first 14 days.
- Zero days where 3+ stories share the same primary entity.

---

## 8. Pipeline Configuration

Machine-readable config for implementation:

```json
{
  "meta": {
    "version": "3.4",
    "date": "2026-05-18",
    "owner": "Alvin Chen",
    "status": "mvp-ready"
  },

  "delivery": {
    "schedule_cron": "0 17 * * *",
    "timezone": "America/New_York",
    "channel": "telegram",
    "max_stories": 7,
    "quality_floor_min_score": 75,
    "slow_news_tag": "📭 Slow news day."
  },

  "retrieval": {
    "window_hours": 48,
    "drop_if_pubdate_missing": true,
    "sources": [
      { "name": "The Information",     "url": "https://www.theinformation.com/feed",               "W_site": 95 },
      { "name": "Bloomberg Tech",      "url": "https://feeds.bloomberg.com/technology/news.rss",    "W_site": 94 },
      { "name": "Stratechery",         "url": "https://stratechery.com/feed/",                      "W_site": 93 },
      { "name": "TechCrunch",          "url": "https://techcrunch.com/feed/",                       "W_site": 92 },
      { "name": "PYMNTS",              "url": "https://www.pymnts.com/feed/",                       "W_site": 90 },
      { "name": "VentureBeat",         "url": "https://venturebeat.com/feed/",                      "W_site": 90 },
      { "name": "The Verge",           "url": "https://www.theverge.com/rss/index.xml",             "W_site": 88 },
      { "name": "Ars Technica",        "url": "https://feeds.arstechnica.com/arstechnica/index",    "W_site": 86 },
      { "name": "Retail Dive",         "url": "https://www.retaildive.com/feeds/news/",             "W_site": 86 },
      { "name": "MIT Tech Review",     "url": "https://www.technologyreview.com/feed/",             "W_site": 85 },
      { "name": "Rest of World",       "url": "https://restofworld.org/feed/",                      "W_site": 85 },
      { "name": "Recode",              "url": "https://www.vox.com/rss/index.xml",                  "W_site": 84 },
      { "name": "Wired",               "url": "https://www.wired.com/feed/rss",                     "W_site": 82 },
      { "name": "Engadget",            "url": "https://www.engadget.com/rss.xml",                   "W_site": 75 },
      { "name": "Memeorandum",         "url": "https://www.memeorandum.com/",                       "W_site": null, "purpose": "W_rank_signal_only" }
    ]
  },

  "aggregator_filter": {
    "apply_before": "clustering",
    "patterns_case_insensitive": [
      "top \\d+ stor", "top \\d+ news", "top \\d+ pick",
      "weekly", "this week in", "week in review",
      "roundup", "wrap-up", "recap",
      "best of", "biggest of",
      "\\d+ things you", "\\d+ stor(ies|y) you",
      "daily digest", "daily brief", "newsletter", "morning brief"
    ]
  },

  "scoring": {
    "base_formula": "W_site*0.30 + W_rank*0.20 + W_fresh*0.20 + W_cross*0.30",
    "W_fresh": { "lt_8h": 100, "8h_to_24h": 90, "24h_to_48h": 70, "gt_48h": "DROP" },
    "W_cross": { "3_or_more": 100, "2_sources": 90, "1_source": 80 },
    "W_rank": {
      "formula": "hero*0.50 + rss_position*0.20 + memeorandum*0.30",
      "fallback_if_memeorandum_down": "hero*0.71 + rss_position*0.29",
      "hero_scores": { "hero_banner": 40, "above_fold": 25, "regular_feed": 10 },
      "rss_position_scores": { "pos_1": 30, "pos_2_3": 20, "pos_4_10": 10 },
      "memeorandum_scores": { "rank_1_10": 25, "rank_11_30": 15, "rank_31_100": 8, "not_listed": 0 }
    },
    "entity_tiers": {
      "tier_1": { "multiplier": 1.25, "keywords": ["Payments", "Marketplace", "E-commerce", "M&A", "SEA Tech", "PropTech"] },
      "tier_2": { "multiplier": 1.15, "keywords": ["AI B2B", "FinTech", "Taiwan"] },
      "tier_3": { "multiplier": 1.10, "keywords": ["AI", "Tech Policy"] },
      "anti_entity": { "multiplier": 0.85, "keywords": ["gaming hardware", "consumer electronics", "automotive"] }
    },
    "scarcity_bonus": [
      { "source": "The Information", "multiplier": 1.35, "additive": 15, "constraint": "always" },
      { "source": "Ars Technica",    "multiplier": 1.25, "additive": 10, "constraint": "always" },
      { "source": "TechCrunch",      "multiplier": 1.15, "additive": 8,  "constraint": "entity_tier_1_or_2_only" }
    ],
    "information_gain": {
      "multiplier": 1.20,
      "whitelist_keywords": ["Revenue", "YoY", "Funding", "Valuation", "Series A", "Series B", "Series C", "Series D", "GMV", "Take Rate", "ARR", "Churn", "IPO", "EBITDA"]
    },
    "recurrence_penalty": {
      "sim_below_0_70": 1.0,
      "sim_0_70_to_0_85": 0.60,
      "sim_0_85_or_above": "HARD_EXCLUDE"
    },
    "final_formula": "(Base * Entity * Scarcity_mult * InfoGain * AntiEntity * Recurrence) + Scarcity_additive"
  },

  "clustering": {
    "method": "cosine_similarity",
    "threshold": 0.70,
    "keep": "highest_score_in_cluster"
  },

  "topic_diversity": {
    "max_stories_per_entity": 2
  },

  "output": {
    "version": "3.4",
    "key_point_removed": true,
    "fields": ["rank", "score", "en_headline", "zh_tw_headline", "source_tag", "en_summary", "zh_tw_summary", "url"],
    "source_tag_format": "[source_name] + [N sources] | [Xh ago] | Tag: [entity_category]",
    "generation": {
      "en_headline": { "origin": "source_rss", "llm_required": false },
      "zh_tw_headline": {
        "task": "translate",
        "model": "claude-haiku-4-5-20251001",
        "instruction": "Translate to zh-TW. Preserve proper nouns and finance acronyms (IPO, Series B, GMV, etc.) in English.",
        "est_tokens_per_story": 260
      },
      "en_summary": {
        "task": "generate",
        "model": "claude-haiku-4-5-20251001",
        "instruction": "Write 60–80 word summary in English. Capture what changed and why it matters.",
        "est_tokens_per_story": 580
      },
      "zh_tw_summary": {
        "task": "translate",
        "model": "claude-haiku-4-5-20251001",
        "instruction": "Translate EN summary to zh-TW. Preserve proper nouns and finance acronyms in English.",
        "est_tokens_per_story": 360
      }
    }
  },

  "cost_analysis": {
    "daily_tokens": 8400,
    "stories_per_day": 7,
    "tokens_per_story": 1200,
    "est_input_tokens_per_day": 4200,
    "est_output_tokens_per_day": 4200,
    "pricing_model": "claude-haiku-4-5-20251001",
    "price_per_1M_input": 1.00,
    "price_per_1M_output": 5.00,
    "cost_per_day_usd": 0.0252,
    "cost_per_month_usd": 0.76,
    "batch_api_discount": 0.50,
    "cost_per_month_batch_usd": 0.38,
    "notes": "Pure Haiku all-in. If using Sonnet for EN summary: +$0.15/month. With batch: total ~$0.34/month mixed Haiku+Sonnet."
  },

  "infrastructure": {
    "trigger": "github_actions_cron",
    "cron": "0 17 * * *",
    "secrets_required": ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "telegram": {
      "retry_attempts": 3,
      "retry_interval_seconds": 60,
      "max_message_chars": 4096,
      "messages_per_story": 1
    },
    "error_handling": {
      "source_failure": "retry_3x_then_skip_source",
      "telegram_failure": "retry_3x_then_send_error_alert",
      "embedding_failure": "skip_dedup_mark_degraded",
      "memeorandum_failure": "renormalize_W_rank"
    }
  },

  "out_of_scope": [
    "click_through_tracking",
    "learning_loop",
    "reddit_x_signals",
    "user_configurable_delivery_time",
    "translation_qa_metrics_phase2",
    "key_point_field"
  ]
}
```

---

## 9. Appendix A — Bilingual Terminology (MVP rule)

All acronyms (IPO, Series B, GMV, ARR, M&A, BNPL, POS, SKU, API, SDK, LLM) and ALL proper nouns stay in English inside zh-TW output. Only natural-language verbs, common nouns, and connectives are translated.

---

*End — PRD v3.4 — Daily US Tech News Recommendation Engine*
