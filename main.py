#!/usr/bin/env python3
"""
Daily US Tech News Engine — v3.5
PRD Owner: Alvin Chen
Schedule: 13:00 ET daily via cron-job.org → workflow_dispatch
"""

import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import anthropic

# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Config  (PRD §8)
# ─────────────────────────────────────────────────────────────
SOURCES = [
    {"name": "The Information", "url": "https://www.theinformation.com/feed",            "W_site": 95},
    {"name": "Bloomberg Tech",  "url": "https://feeds.bloomberg.com/technology/news.rss", "W_site": 94},
    {"name": "Stratechery",     "url": "https://stratechery.com/feed/",                   "W_site": 93},
    {"name": "TechCrunch",      "url": "https://techcrunch.com/feed/",                    "W_site": 92},
    {"name": "PYMNTS",          "url": "https://www.pymnts.com/feed/",                    "W_site": 90},
    {"name": "VentureBeat",     "url": "https://venturebeat.com/feed/",                   "W_site": 90},
    {"name": "The Verge",       "url": "https://www.theverge.com/rss/index.xml",          "W_site": 88},
    {"name": "Ars Technica",    "url": "https://feeds.arstechnica.com/arstechnica/index", "W_site": 68},
    {"name": "Retail Dive",     "url": "https://www.retaildive.com/feeds/news/",          "W_site": 86},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/",          "W_site": 85},
    {"name": "Rest of World",   "url": "https://restofworld.org/feed/",                   "W_site": 85},
    {"name": "Recode",          "url": "https://www.vox.com/rss/index.xml",               "W_site": 84},
    {"name": "Wired",           "url": "https://www.wired.com/feed/rss",                  "W_site": 82},
    {"name": "Engadget",        "url": "https://www.engadget.com/rss.xml",                "W_site": 75},
]

MEMEORANDUM_URL = "https://www.memeorandum.com/"

AGGREGATOR_PATTERNS = [
    r"top \d+ stor", r"top \d+ news", r"top \d+ pick",
    r"weekly", r"this week in", r"week in review",
    r"roundup", r"wrap-up", r"recap",
    r"best of", r"biggest of",
    r"\d+ things you", r"\d+ stor(ies|y) you",
    r"daily digest", r"daily brief", r"newsletter", r"morning brief",
]

ENTITY_TIERS = {
    "tier_1": {
        "multiplier": 1.25,
        "keywords": ["payments", "marketplace", "e-commerce", "ecommerce", "m&a", "sea tech", "proptech"],
    },
    "tier_2": {
        "multiplier": 1.15,
        "keywords": ["ai b2b", "fintech", "taiwan", "ai commerce"],
    },
    "tier_3": {
        "multiplier": 1.10,
        "keywords": ["artificial intelligence", " ai ", "tech policy", "regulation"],
    },
    "anti": {
        "multiplier": 0.85,
        "keywords": [
            # hardware / gadget reviews
            "gaming hardware", "consumer electronics review", "gadget review",
            "headphones review", "hardware review", "game review",
            # entertainment / culture
            "movie review", "film review", "box office", "celebrity",
            "music video", "album review", "award show", "oscars", "grammy", "emmys",
            "turns 40", "turns 50", "turns 30", "anniversary",
            # off-topic
            "automotive", "esports", "recipe", "travel guide",
        ],
    },
}

INFO_GAIN_KEYWORDS = [
    "revenue", "yoy", "funding", "valuation",
    "series a", "series b", "series c", "series d",
    "gmv", "take rate", "arr", "churn", "ipo", "ebitda",
]

SCARCITY = {
    "The Information": {"multiplier": 1.35, "additive": 15, "constraint": "always"},
    "TechCrunch":      {"multiplier": 1.15, "additive":  8, "constraint": "tier_1_2"},
}

QUALITY_FLOOR    = 75
MAX_STORIES      = 7
WINDOW_HOURS     = 48
CLUSTER_THRESHOLD = 0.70
MAX_PER_ENTITY   = 2
SLOW_NEWS_TAG    = "📭 Slow news day."

ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]


# ─────────────────────────────────────────────────────────────
# STEP 1 — Fetch RSS
# ─────────────────────────────────────────────────────────────
def fetch_rss(source: dict) -> list:
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for i, entry in enumerate(feed.entries):
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub:
                log.info(f"[{source['name']}] No pubDate, skipping: {entry.get('title','?')[:60]}")
                continue
            published_at = datetime(*pub[:6], tzinfo=timezone.utc)
            items.append({
                "title":        entry.get("title", "").strip(),
                "url":          entry.get("link", ""),
                "published_at": published_at,
                "rss_position": i + 1,
                "source_name":  source["name"],
                "W_site":       source["W_site"],
                "summary_raw":  entry.get("summary", ""),
            })
        log.info(f"[{source['name']}] fetched {len(items)} items")
        return items
    except Exception as e:
        log.warning(f"[{source['name']}] fetch failed: {e}")
        return []


def fetch_all_sources() -> list:
    all_items = []
    for src in SOURCES:
        all_items.extend(fetch_rss(src))
    log.info(f"Total raw items: {len(all_items)}")
    return all_items


# ─────────────────────────────────────────────────────────────
# STEP 2 — 48h Age Filter
# ─────────────────────────────────────────────────────────────
def filter_by_age(items: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    fresh = [i for i in items if i["published_at"] >= cutoff]
    log.info(f"Age filter: {len(items)} → {len(fresh)}")
    return fresh


# ─────────────────────────────────────────────────────────────
# STEP 3 — Aggregator / Roundup Filter
# ─────────────────────────────────────────────────────────────
def filter_aggregators(items: list) -> list:
    compiled = [re.compile(p, re.IGNORECASE) for p in AGGREGATOR_PATTERNS]
    result = [i for i in items if not any(p.search(i["title"]) for p in compiled)]
    log.info(f"Aggregator filter: {len(items)} → {len(result)}")
    return result


# ─────────────────────────────────────────────────────────────
# STEP 4 — Memeorandum Signal
# ─────────────────────────────────────────────────────────────
def fetch_memeorandum_ranks() -> dict:
    try:
        resp = requests.get(
            MEMEORANDUM_URL, timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        ranks = {}
        rank = 1
        for a in soup.select("article.clus cite a, strong a"):
            href = a.get("href", "")
            if href.startswith("http"):
                ranks[href] = rank
                rank += 1
        log.info(f"Memeorandum: {len(ranks)} links")
        return ranks
    except Exception as e:
        log.warning(f"Memeorandum unavailable: {e}")
        return {}


def memo_score(item: dict, memo_ranks: dict) -> int:
    url = item["url"]
    for href, rank in memo_ranks.items():
        if url in href or href in url:
            if rank <= 10:  return 25
            if rank <= 30:  return 15
            if rank <= 100: return 8
    return 0


# ─────────────────────────────────────────────────────────────
# STEP 5 — Scoring
# ─────────────────────────────────────────────────────────────
def w_fresh(published_at: datetime) -> int:
    age_h = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
    if age_h < 8:  return 100
    if age_h < 24: return 90
    return 70


def w_rank(item: dict, memo_ranks: dict, use_memo: bool) -> float:
    pos = item["rss_position"]
    hero = 40 if pos == 1 else (25 if pos <= 3 else 10)
    rss  = 30 if pos == 1 else (20 if pos <= 3 else (10 if pos <= 10 else 0))
    if use_memo:
        m = memo_score(item, memo_ranks)
        return hero * 0.50 + rss * 0.20 + m * 0.30
    return hero * 0.71 + rss * 0.29


def get_entity_tier(title: str, summary: str) -> tuple:
    text = (title + " " + summary).lower()
    for kw in ENTITY_TIERS["anti"]["keywords"]:
        if kw in text:
            return ENTITY_TIERS["anti"]["multiplier"], "anti"
    for tier in ["tier_1", "tier_2", "tier_3"]:
        for kw in ENTITY_TIERS[tier]["keywords"]:
            if kw in text:
                return ENTITY_TIERS[tier]["multiplier"], tier
    return 1.0, "none"


def get_info_gain(title: str, summary: str) -> float:
    text = (title + " " + summary).lower()
    return 1.20 if any(kw in text for kw in INFO_GAIN_KEYWORDS) else 1.0


def get_scarcity(source_name: str, entity_tier: str) -> tuple:
    if source_name not in SCARCITY:
        return 1.0, 0
    s = SCARCITY[source_name]
    if s["constraint"] == "always":
        return s["multiplier"], s["additive"]
    if s["constraint"] == "tier_1_2" and entity_tier in ("tier_1", "tier_2"):
        return s["multiplier"], s["additive"]
    return 1.0, 0


def w_cross_value(count: int) -> int:
    if count >= 3: return 100
    if count == 2: return 90
    return 80


def compute_cross_counts(items: list) -> list:
    """Add cross_count field: how many unique sources cover a similar headline."""
    titles = [i["title"] for i in items]
    if len(titles) < 2:
        for item in items:
            item["cross_count"] = 1
        return items
    try:
        tfidf = TfidfVectorizer(stop_words="english", max_features=500).fit_transform(titles)
        sims  = cosine_similarity(tfidf)
        for idx, item in enumerate(items):
            extra_sources = {
                items[j]["source_name"]
                for j in range(len(items))
                if j != idx and sims[idx][j] >= 0.50
            }
            item["cross_count"] = 1 + len(extra_sources)
    except Exception:
        for item in items:
            item["cross_count"] = 1
    return items


def score_all(items: list, memo_ranks: dict, use_memo: bool) -> list:
    for item in items:
        ws  = item["W_site"]
        wf  = w_fresh(item["published_at"])
        wr  = w_rank(item, memo_ranks, use_memo)
        wc  = w_cross_value(item["cross_count"])

        base = ws * 0.30 + wr * 0.20 + wf * 0.20 + wc * 0.30

        ent_mult, ent_tier = get_entity_tier(item["title"], item["summary_raw"])
        ig_mult            = get_info_gain(item["title"], item["summary_raw"])
        sc_mult, sc_add    = get_scarcity(item["source_name"], ent_tier)

        # anti-entity demotes; entity tiers promote — only one applies
        entity_m = ent_mult if ent_tier != "anti" else 1.0
        anti_m   = ENTITY_TIERS["anti"]["multiplier"] if ent_tier == "anti" else 1.0

        item["score"]       = round((base * entity_m * sc_mult * ig_mult * anti_m) + sc_add, 1)
        item["entity_tier"] = ent_tier
    return items


# ─────────────────────────────────────────────────────────────
# STEP 6 — Cluster & Dedup
# ─────────────────────────────────────────────────────────────
def cluster_and_dedup(items: list) -> list:
    if len(items) < 2:
        return items
    titles = [i["title"] for i in items]
    try:
        tfidf = TfidfVectorizer(stop_words="english").fit_transform(titles)
        sims  = cosine_similarity(tfidf)
    except Exception:
        return items

    visited  = set()
    clusters = []
    for i in range(len(items)):
        if i in visited:
            continue
        cluster = [i]
        for j in range(i + 1, len(items)):
            if j not in visited and sims[i][j] >= CLUSTER_THRESHOLD:
                cluster.append(j)
                visited.add(j)
        visited.add(i)
        best = max(cluster, key=lambda x: items[x]["score"])
        clusters.append(items[best])

    log.info(f"Cluster+dedup: {len(items)} → {len(clusters)}")
    return clusters


# ─────────────────────────────────────────────────────────────
# STEP 7 — Diversity + Quality Floor
# ─────────────────────────────────────────────────────────────
def apply_diversity(items: list) -> list:
    tier_counts = {}
    result = []
    for item in sorted(items, key=lambda x: -x["score"]):
        tier = item.get("entity_tier", "none")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if tier_counts[tier] <= MAX_PER_ENTITY:
            result.append(item)
    return result


def apply_quality_floor(items: list) -> list:
    passed = [i for i in items if i["score"] >= QUALITY_FLOOR]
    log.info(f"Quality floor ≥{QUALITY_FLOOR}: {len(items)} → {len(passed)}")
    return passed


# ─────────────────────────────────────────────────────────────
# STEP 8 — Claude Haiku: Bilingual Output
# ─────────────────────────────────────────────────────────────
haiku_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def haiku(prompt: str, max_tokens: int = 300) -> str:
    msg = haiku_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def relevance_score(title: str, raw: str) -> int:
    """Ask Haiku to score tech relevance 1-10. Returns integer."""
    try:
        result = haiku(
            f"Rate relevance to a tech news reader interested in: AI, software, startups, "
            f"funding rounds, e-commerce, payments, fintech, digital platforms, tech policy.\n\n"
            f"10 = Core tech business (AI launch, funding, M&A, payments news)\n"
            f"7-9 = Relevant tech (company strategy, digital trends, regulation)\n"
            f"4-6 = Borderline (general business with tech angle)\n"
            f"1-3 = Not relevant (entertainment, sports, culture, gadget review, lifestyle)\n\n"
            f"Title: {title}\n"
            f"Context: {raw[:200]}\n\n"
            f"Output: single integer 1-10 only.",
            max_tokens=5,
        )
        return int(result.strip())
    except Exception:
        return 5  # neutral fallback


def filter_by_relevance(items: list, top_n: int = 25) -> list:
    """Run Haiku relevance check on top_n candidates. Drop score < 5."""
    candidates = sorted(items, key=lambda x: -x["score"])[:top_n]
    rest       = items[top_n:]  # below top_n → pass through without extra call
    checked    = []
    for item in candidates:
        r = relevance_score(item["title"], item.get("summary_raw", ""))
        item["relevance"] = r
        if r >= 5:
            checked.append(item)
        else:
            log.info(f"  ✂️ Relevance {r}/10 → dropped: {item['title'][:60]}")
    log.info(f"Relevance filter (top {top_n}): {len(candidates)} → {len(checked)}")
    return checked + rest


def translate_headline(en_title: str) -> str:
    return haiku(
        f"Translate to Traditional Chinese (zh-TW). "
        f"Keep ALL proper nouns, company names, and finance acronyms "
        f"(IPO, Series B, GMV, ARR, M&A, BNPL, POS, SKU, LLM, etc.) in English. "
        f"Only translate natural-language verbs, common nouns, and connectives.\n\n"
        f"Headline: {en_title}\n\n"
        f"Output: zh-TW translation only, no explanation.",
        max_tokens=200,
    )


def generate_en_summary(title: str, raw: str) -> str:
    return haiku(
        f"Write a 40–55 word factual English summary of this news story. "
        f"State what happened, who is involved, and the key outcome. "
        f"No opinion, no analysis, no 'this matters because'. Facts only.\n\n"
        f"Title: {title}\n"
        f"Context: {raw[:400]}\n\n"
        f"Output: summary only.",
        max_tokens=120,
    )


def translate_summary(en_summary: str) -> str:
    return haiku(
        f"Translate to Traditional Chinese (zh-TW). "
        f"Keep ALL proper nouns, company names, and finance acronyms in English.\n\n"
        f"{en_summary}\n\n"
        f"Output: zh-TW translation only.",
        max_tokens=300,
    )


def age_label(published_at: datetime) -> str:
    hours = int((datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
    return f"{hours}h ago"


TIER_LABELS = {
    "tier_1": "Payments / E-commerce / Marketplace",
    "tier_2": "AI B2B / FinTech / Taiwan",
    "tier_3": "AI / Tech Policy",
    "anti":   "Off-domain",
    "none":   "General Tech",
}


def format_story(rank: int, item: dict) -> str:
    zh_headline = translate_headline(item["title"])
    en_summary  = generate_en_summary(item["title"], item["summary_raw"])
    zh_summary  = translate_summary(en_summary)

    tag        = TIER_LABELS.get(item.get("entity_tier", "none"), "General Tech")
    source_tag = f"{item['source_name']} | {age_label(item['published_at'])} | Tag: {tag}"

    return (
        f"[#{rank} — Score: {item['score']}]\n\n"
        f"EN:  {item['title']}\n"
        f"中:  {zh_headline}\n\n"
        f"Source: {source_tag}\n\n"
        f"Summary (EN):\n{en_summary}\n\n"
        f"摘要（中）：\n{zh_summary}\n\n"
        f"🔗 {item['url']}"
    )


# ─────────────────────────────────────────────────────────────
# STEP 9 — Telegram Delivery
# ─────────────────────────────────────────────────────────────
def send_telegram(text: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(retries):
        try:
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": False,
            }, timeout=15)
            if resp.status_code == 200:
                return True
            log.warning(f"Telegram attempt {attempt + 1} failed: {resp.text[:200]}")
        except Exception as e:
            log.warning(f"Telegram attempt {attempt + 1} error: {e}")
        if attempt < retries - 1:
            time.sleep(60)
    return False


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    log.info("=== Daily Tech News Engine v3.5 starting ===")

    raw = fetch_all_sources()           # Step 1
    raw = filter_by_age(raw)            # Step 2
    raw = filter_aggregators(raw)       # Step 3

    memo_ranks = fetch_memeorandum_ranks()  # Step 4
    use_memo   = bool(memo_ranks)

    raw = compute_cross_counts(raw)     # Step 5a (cross-source)
    raw = score_all(raw, memo_ranks, use_memo)  # Step 5b (scoring)
    raw.sort(key=lambda x: -x["score"])

    raw   = filter_by_relevance(raw)    # Step 5c (Haiku relevance pre-filter)
    raw   = cluster_and_dedup(raw)      # Step 6
    raw   = apply_quality_floor(raw)    # Step 7a
    raw   = apply_diversity(raw)        # Step 7b
    final = raw[:MAX_STORIES]           # Top 7

    log.info(f"Stories to deliver: {len(final)}")

    if not final:
        send_telegram("📭 No stories passed the quality floor today.")
        return

    for rank, item in enumerate(final, 1):
        log.info(f"Sending #{rank}: {item['title'][:70]}")
        try:
            message = format_story(rank, item)
            ok = send_telegram(message)
            log.info(f"  {'✅' if ok else '❌'} #{rank}")
        except Exception as e:
            log.error(f"  ❌ Error on #{rank}: {e}")
        time.sleep(2)  # brief pause between messages

    if len(final) < 3:
        send_telegram(SLOW_NEWS_TAG)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
