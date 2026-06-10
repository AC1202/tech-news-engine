---
name: tech-news-engine
description: >
  Project knowledge base for the tech-news-engine repo (github.com/AC1202/tech-news-engine).
  Use this skill at the START of any new session involving this project — whenever the user mentions
  "tech news engine", "tech-news-engine", "新聞推播", "news push project", "sports engine",
  "US news engine", or asks you to read/understand/modify/debug this news aggregation system.
  Also trigger when the user says "讀 tech news skill" or "理解這個專案背景".
  This skill gives you full context on the 3-engine architecture, scoring algorithm, file structure,
  deployment setup, and known gotchas — so the user never has to re-explain the project from scratch.
---

# Tech News Engine — Project Knowledge Base

**Owner:** Alvin Chen (DC-based, FinTech/e-commerce PM background)
**Repo:** `https://github.com/AC1202/tech-news-engine` (public)
**Local path:** `~/Documents/Claude/Projects/US News Push/tech-news-engine`

---

## What This Project Does

Three independent Python news aggregation engines that run on schedule via GitHub Actions, pull RSS feeds, score articles using a custom algorithm, deduplicate using TF-IDF cosine similarity, generate bilingual (EN + 繁中) summaries via Claude Haiku, and push to Telegram.

| Engine | File | Push Time (ET) | Stories | Status |
|--------|------|---------------|---------|--------|
| Tech News | `main.py` | 1:00 PM | Top 7 | v3.5 ✅ live |
| US General News | `news_main.py` | 9:00 AM | Top 5 | v1.0 ✅ live |
| Sports News | `sports_main.py` | 5:00 PM | Top 5 | v1.1 ✅ live |

All three push to the **same Telegram bot and chat** (`TELEGRAM_CHAT_ID`).

---

## File Navigator

```
tech-news-engine/
├── main.py              # Tech News Engine v3.5 — 14 RSS sources, top 7
├── news_main.py         # US General News Engine v1.0 — 17 sources, top 5, DC guarantee
├── sports_main.py       # Sports News Engine v1.1 — 10 sources, top 5
├── requirements.txt     # feedparser, sklearn, anthropic, requests
├── sports_history.json  # 7-day recurrence state (auto-committed by workflow)
├── news_history.json    # 2-day recurrence state (auto-committed by workflow)
├── .env                 # Local secrets — NEVER committed (.gitignore)
├── .env.example         # Variable names only, no values
├── README.md            # Public-facing project overview
├── SKILL.md             # This file — project knowledge base for agents
├── PRD_Daily_Tech_News_Engine_v3.4.md
└── .github/workflows/
    ├── daily_news.yml         # Tech engine — 17:00 UTC (1pm ET)
    ├── daily_us_news.yml      # US news — 13:00 UTC (9am ET)
    └── daily_sports_news.yml  # Sports — 21:00 UTC (5pm ET)
```

### Per-file details

**`main.py` — Tech News v3.5**
- Sources: The Information(95), Bloomberg(94), TechCrunch(92), PYMNTS(90), VentureBeat(90), The Verge(88), Retail Dive(86), MIT Tech Review(85), Rest of World(85), Recode(84), Wired(82), Engadget(75), Ars Technica(68)
- Pipeline: fetch → 48h age filter → aggregator title filter → cross counts → score_all → `filter_by_relevance()` (Haiku 1-10, drop <5) → TF-IDF dedup (threshold=0.65) → quality floor (≥75) → diversity cap → top 7
- Entity tiers: tier_1=payments/marketplace/e-commerce ×1.25, tier_2=fintech/taiwan ×1.15, tier_3=AI ×1.10, anti=gaming/automotive ×0.85
- Scarcity: The Information ×1.35+15pts, TechCrunch ×1.15+8pts (tier1/2 only)
- Summary: 40–55 words, purely factual, no PM analysis, temperature=0.1

**`news_main.py` — US General News v1.0**
- US sources (12): AP(95), Reuters(93), NYT(91), WaPo(90), WSJ(88), NPR(86), Axios(85), Politico(84), CNN(80), USA Today(78), Fox(73), DCist(78, dc_source=True)
- INTL sources (5): BBC(90), Economist(88), FT(87), Guardian(82), Al Jazeera(80)
- DC guarantee: if no DC in US top-5, replace #5 with best DC story (score ≥65)
- INTL filter: non-US-related articles ×0.60
- Freshness: <4h→100, 4-12h→90, 12-24h→75, >24h→DROP
- Output emoji: 🇺🇸 US, 🌍 INTL, 🏛️ DC

**`sports_main.py` — Sports News v1.1**
- Sources: ESPN(92), ESPN NBA(92), ESPN MLB(88), ESPN NFL(85), Yahoo(85), CBS(82), Google News RSS ×4 (NBA/MLB/NFL/NHL)
- CLUSTER_THRESHOLD=0.50 (must stay low — short sports titles)
- MAX_PER_SPORT=2, MAX_STORIES=5
- Freshness: <12h→100, 12-24h→95, >24h→80

**`.github/workflows/`**
- ALL use `workflow_dispatch` ONLY — no native cron
- Triggered by cron-job.org HTTP POST
- `daily_us_news.yml` and `daily_sports_news.yml` have `permissions: contents: write` to commit history JSON

---

## Scoring Formula

```
Base = W_site×0.30 + W_fresh×0.25 + W_cross×0.30 + W_rank×0.15
FinalScore = (Base × EntityMult × ScarcityMult) + ScarcityAdditive
```

W_cross: 4+→100, 3→95, 2→88, 1→78

---

## Deployment Architecture

```
cron-job.org → HTTP POST (Authorization: Bearer <Classic PAT>) 
    → GitHub API /dispatches 
    → GitHub Actions workflow_dispatch 
    → pip install + python script 
    → Telegram Bot API
```

**Three cron-job.org tasks:**
| Task | UTC | ET |
|------|-----|----|
| US News | 13:00 | 9am |
| Tech News | 17:00 | 1pm |
| Sports | 21:00 | 5pm |

**GitHub Secrets:** `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

---

## Known Gotchas — Read Before Making Changes

1. **cron-job.org URL 欄位**: GitHub API URL 放在 COMMON tab → URL 欄位。絕對不能放在 Advanced tab 的 Username 欄位（這個坑踩了三次，每次都導致 404）

2. **GitHub PAT 類型**: 必須用 **Classic PAT** + `workflow` scope。Fine-grained PAT 即使給 Actions R/W 也會 404

3. **PAT 到期更新**: 更新 PAT expire date 後 GitHub 會產生新 token value，必須同步更新 cron-job.org 三個任務的 Authorization header

4. **Sports dedup threshold**: 必須保持 0.50，不能調高。短標題 TF-IDF 相似度天生較低

5. **Summary 純事實**: summary prompt 明確禁止任何推論、分析、"this matters because"。Entity tier 乘數只影響排序，不影響摘要輸出

6. **history JSON 不能刪**: `sports_history.json` / `news_history.json` 是 workflow 自動 commit 的狀態檔，必須留在 repo root

---

## How to Make Changes

1. Edit `.py` file locally
2. `git add <file> && git commit -m "..." && git push`
3. 無需 deploy — GitHub Actions 下次觸發時自動用最新 `main`

手動測試：GitHub → Actions → 選 workflow → "Run workflow"

---

## Model & Cost

- **Model**: `claude-haiku-4-5-20251001`, temperature=0.1
- **Tasks**: relevance scoring (1-10) + bilingual headline translation + factual summary
- **Cost**: ~$0.03/day 三個 engine 合計
