#!/usr/bin/env python3
"""
Daily US General News Engine — v1.0
PRD Owner: Alvin Chen
Schedule: 09:00 ET daily via cron-job.org → workflow_dispatch
"""

import os
import re
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
US_SOURCES = [
    {"name": "AP",        "url": "https://feeds.apnews.com/rss/apf-topnews",                      "W_site": 95, "dc_source": False},
    {"name": "Reuters US","url": "https://feeds.reuters.com/reuters/topNews",                     "W_site": 93, "dc_source": False},
    {"name": "NYT",       "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",     "W_site": 91, "dc_source": False},
    {"name": "WaPo",      "url": "https://feeds.washingtonpost.com/rss/national",                 "W_site": 90, "dc_source": False},
    {"name": "WSJ",       "url": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",     "W_site": 88, "dc_source": False},
    {"name": "NPR",       "url": "https://feeds.npr.org/1001/rss.xml",                           "W_site": 86, "dc_source": False},
    {"name": "Axios",     "url": "https://api.axios.com/feed/",                                   "W_site": 85, "dc_source": False},
    {"name": "Politico",  "url": "https://www.politico.com/rss/politicopicks.xml",               "W_site": 84, "dc_source": False},
    {"name": "CNN",       "url": "http://rss.cnn.com/rss/cnn_topstories.rss",                   "W_site": 80, "dc_source": False},
    {"name": "USA Today", "url": "http://rssfeeds.usatoday.com/usatoday-NewsTopStories",         "W_site": 78, "dc_source": False},
    {"name": "Fox News",  "url": "https://moxie.foxnews.com/google-publisher/latest.xml",        "W_site": 73, "dc_source": False},
    {"name": "DCist",     "url": "https://dcist.com/feed/",                                     "W_site": 78, "dc_source": True},
]

INTL_SOURCES = [
    {"name": "BBC World",     "url": "http://feeds.bbci.co.uk/news/world/rss.xml",                "W_site": 90},
    {"name": "The Economist", "url": "https://www.economist.com/rss/the_economist_full_rss.xml", "W_site": 88},
    {"name": "FT",            "url": "https://www.ft.com/rss/home",                              "W_site": 87},
    {"name": "The Guardian",  "url": "https://www.theguardian.com/us/rss",                       "W_site": 82},
    {"name": "Al Jazeera",    "url": "https://www.aljazeera.com/xml/rss/all.xml",               "W_site": 80},
]

AGGREGATOR_PATTERNS = [
    r"top \d+ ", r"this week in", r"week in review", r"weekly",
    r"roundup", r"wrap-?up", r"recap",
    r"best of", r"biggest of",
    r"daily brief", r"newsletter", r"morning brief",
]

DC_KEYWORDS = [
    "washington dc", "washington, dc", " d.c.", "district of columbia",
    "capitol hill", "maryland", "virginia", " dmv ", "northern virginia",
    "arlington", "alexandria", "montgomery county", "prince george", "fairfax",
]

TOPIC_TIERS = {
    "dc_local": {
        "multiplier": 1.35,
        "keywords": DC_KEYWORDS,
    },
    "tier_1": {
        "multiplier": 1.25,
        "keywords": [
            "national security", "economy", "economic", "markets", "stock market",
            "supreme court", "disaster", "earthquake", "hurricane", "tornado",
            "shooting", "attack", "terrorism", "nuclear", "military action",
            "recession", "inflation", "federal reserve", "fed rate",
        ],
    },
    "tier_2": {
        "multiplier": 1.15,
        "keywords": [
            "foreign policy", "immigration", "border", "public health", "pandemic",
            "federal policy", "legislation", "senate", "house of representatives",
            "signed into law", "executive order", "regulation", "fda", "cdc", "epa",
        ],
    },
    "tier_3": {
        "multiplier": 1.10,
        "keywords": [
            "election", "campaign", "poll", "ballot",
            "indicted", "indictment", "arrested", "charged", "convicted",
            "investigation", "probe", "hearing",
        ],
    },
    "anti": {
        "multiplier": 0.80,
        "keywords": [
            "celebrity", "kardashian", "taylor swift", "beyoncé", "oscars",
            "grammy", "emmys", "box office", "reality tv", "dating show",
        ],
    },
}

INTL_US_KEYWORDS = [
    "united states", " u.s.", "america", "american",
    "white house", "pentagon", "state department", "congress",
    "washington", "biden", "trump", "administration",
    "sanctions", "nato", "trade war", "tariff",
    "us-china", "us-russia", "us-europe",
]

INTL_REGIONS = {
    "middle_east": ["israel", "iran", "iraq", "saudi", "qatar", "turkey", "egypt",
                    "lebanon", "syria", "gaza", "middle east", "persian gulf"],
    "europe":      ["europe", "european union", " eu ", "ukraine", "russia", "germany",
                    "france", " uk ", "britain", "london", "paris", "berlin"],
    "asia_pacific": ["china", "japan", "korea", "taiwan", "india", "australia",
                     "southeast asia", "pacific", "beijing", "tokyo"],
}

WINDOW_HOURS      = 24
US_SLOTS          = 5
INTL_SLOTS        = 3
QUALITY_FLOOR     = 65
QUALITY_FALLBACK  = 55
DC_MIN_SCORE      = 65
CLUSTER_THRESHOLD = 0.65
HISTORY_DAYS      = 2
HISTORY_FILE      = Path(__file__).parent / "news_history.json"

ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]


# ─────────────────────────────────────────────────────────────
# STEP 1 — Fetch RSS
# ─────────────────────────────────────────────────────────────
def fetch_rss(source: dict, pool: str) -> list:
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for i, entry in enumerate(feed.entries):
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub:
                continue
            items.append({
                "title":        entry.get("title", "").strip(),
                "url":          entry.get("link", ""),
                "published_at": datetime(*pub[:6], tzinfo=timezone.utc),
                "rss_position": i + 1,
                "source_name":  source["name"],
                "W_site":       source["W_site"],
                "summary_raw":  entry.get("summary", ""),
                "pool":         pool,
                "dc_source":    source.get("dc_source", False),
            })
        log.info(f"[{source['name']}] fetched {len(items)} items")
        return items
    except Exception as e:
        log.warning(f"[{source['name']}] fetch failed: {e}")
        return []


def fetch_all_sources() -> tuple:
    us_items, intl_items = [], []
    for src in US_SOURCES:
        us_items.extend(fetch_rss(src, "us"))
    for src in INTL_SOURCES:
        intl_items.extend(fetch_rss(src, "intl"))
    log.info(f"Raw items — US: {len(us_items)}, INTL: {len(intl_items)}")
    return us_items, intl_items


# ─────────────────────────────────────────────────────────────
# STEP 2 — 24h Age Filter
# ─────────────────────────────────────────────────────────────
def filter_by_age(items: list) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    fresh = [i for i in items if i["published_at"] >= cutoff]
    log.info(f"Age filter: {len(items)} → {len(fresh)}")
    return fresh


# ─────────────────────────────────────────────────────────────
# STEP 3 — Aggregator Filter
# ─────────────────────────────────────────────────────────────
def filter_aggregators(items: list) -> list:
    compiled = [re.compile(p, re.IGNORECASE) for p in AGGREGATOR_PATTERNS]
    result = [i for i in items if not any(p.search(i["title"]) for p in compiled)]
    log.info(f"Aggregator filter: {len(items)} → {len(result)}")
    return result


# ─────────────────────────────────────────────────────────────
# STEP 4 — Cross-Source Counts (global, across both pools)
# ─────────────────────────────────────────────────────────────
def compute_cross_counts(items: list) -> list:
    titles = [i["title"] for i in items]
    if len(titles) < 2:
        for item in items:
            item["cross_count"] = 1
        return items
    try:
        tfidf = TfidfVectorizer(stop_words="english", max_features=500).fit_transform(titles)
        sims  = cosine_similarity(tfidf)
        for idx, item in enumerate(items):
            extra = {
                items[j]["source_name"]
                for j in range(len(items))
                if j != idx and sims[idx][j] >= 0.50
            }
            item["cross_count"] = 1 + len(extra)
    except Exception:
        for item in items:
            item["cross_count"] = 1
    return items


# ─────────────────────────────────────────────────────────────
# STEP 5 — Scoring
# ─────────────────────────────────────────────────────────────
def w_fresh(published_at: datetime) -> int:
    age_h = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
    if age_h < 4:   return 100
    if age_h < 12:  return 90
    if age_h < 24:  return 75
    return 0


def w_cross(count: int) -> int:
    if count >= 4: return 100
    if count == 3: return 95
    if count == 2: return 88
    return 78


def w_rank(rss_position: int) -> int:
    if rss_position == 1:    return 90
    if rss_position <= 3:    return 75
    if rss_position <= 10:   return 55
    return 40


def get_topic_multiplier(item: dict) -> tuple:
    text = (item["title"] + " " + item.get("summary_raw", "")).lower()

    # DC Local is highest priority
    if item.get("dc_source") or any(kw in text for kw in DC_KEYWORDS):
        item["is_dc"] = True
        return TOPIC_TIERS["dc_local"]["multiplier"], "dc_local"

    item["is_dc"] = False

    # Anti-topic demote
    if any(kw in text for kw in TOPIC_TIERS["anti"]["keywords"]):
        return TOPIC_TIERS["anti"]["multiplier"], "anti"

    for tier in ["tier_1", "tier_2", "tier_3"]:
        if any(kw in text for kw in TOPIC_TIERS[tier]["keywords"]):
            return TOPIC_TIERS[tier]["multiplier"], tier

    return 1.0, "none"


def is_us_relevant(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in INTL_US_KEYWORDS)


def get_intl_region(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    for region, keywords in INTL_REGIONS.items():
        if any(kw in text for kw in keywords):
            return region
    return "other"


def score_all(items: list) -> list:
    for item in items:
        ws = item["W_site"]
        wf = w_fresh(item["published_at"])
        wc = w_cross(item["cross_count"])
        wr = w_rank(item["rss_position"])

        base = ws * 0.30 + wf * 0.25 + wc * 0.30 + wr * 0.15

        topic_mult, topic_tier = get_topic_multiplier(item)

        intl_mult = 1.0
        if item["pool"] == "intl":
            if not is_us_relevant(item["title"], item.get("summary_raw", "")):
                intl_mult = 0.60
            item["region"] = get_intl_region(item["title"], item.get("summary_raw", ""))

        item["score"]       = round(base * topic_mult * intl_mult, 1)
        item["topic_tier"]  = topic_tier
    return items


# ─────────────────────────────────────────────────────────────
# STEP 6 — Recurrence Penalty (yesterday's stories)
# ─────────────────────────────────────────────────────────────
def load_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
        return [e for e in data if datetime.fromisoformat(e["date"]) >= cutoff]
    except Exception:
        return []


def save_history(history: list, new_titles: list):
    today = datetime.now(timezone.utc).isoformat()
    for title in new_titles:
        history.append({"title": title, "date": today})
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"Could not save history: {e}")


def apply_recurrence_penalty(items: list, history: list) -> list:
    if not history:
        return items
    history_titles = [h["title"] for h in history]
    all_titles = history_titles + [i["title"] for i in items]
    try:
        tfidf = TfidfVectorizer(stop_words="english").fit_transform(all_titles)
        sims  = cosine_similarity(tfidf)
        n_hist = len(history_titles)
        for idx, item in enumerate(items):
            sims_to_hist = [sims[n_hist + idx][j] for j in range(n_hist)]
            max_sim = max(sims_to_hist) if sims_to_hist else 0
            if max_sim >= 0.80:
                item["score"] = 0
            elif max_sim >= 0.65:
                item["score"] = round(item["score"] * 0.65, 1)
    except Exception:
        pass
    result = [i for i in items if i["score"] > 0]
    log.info(f"Recurrence filter: {len(items)} → {len(result)}")
    return result


# ─────────────────────────────────────────────────────────────
# STEP 7 — Cluster & Dedup (global, 0.65)
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
# STEP 8 — Quality Floor & Diversity
# ─────────────────────────────────────────────────────────────
def apply_quality_floor(items: list, floor: int) -> list:
    passed = [i for i in items if i["score"] >= floor]
    log.info(f"Quality floor ≥{floor}: {len(items)} → {len(passed)}")
    return passed


def apply_us_diversity(items: list, n: int) -> list:
    """Max 2 stories per Tier 1 topic."""
    tier1_counts = {}
    result = []
    for item in sorted(items, key=lambda x: -x["score"]):
        tier = item.get("topic_tier", "none")
        if tier == "tier_1":
            tier1_counts[tier] = tier1_counts.get(tier, 0) + 1
            if tier1_counts[tier] > 2:
                continue
        result.append(item)
        if len(result) >= n * 2:
            break
    return result


def apply_intl_diversity(items: list, n: int) -> list:
    """Max 2 stories per region."""
    region_counts = {}
    result = []
    for item in sorted(items, key=lambda x: -x["score"]):
        region = item.get("region", "other")
        region_counts[region] = region_counts.get(region, 0) + 1
        if region_counts[region] > 2:
            continue
        result.append(item)
        if len(result) >= n * 2:
            break
    return result


def apply_dc_guarantee(us_final: list, us_pool_all: list) -> tuple:
    """Ensure at least 1 DC story in US top 5. Returns (final_list, dc_slow_day)."""
    has_dc = any(item.get("is_dc", False) for item in us_final)
    if has_dc:
        return us_final, False

    used_urls = {item["url"] for item in us_final}
    dc_candidates = sorted(
        [i for i in us_pool_all if i.get("is_dc", False) and i["url"] not in used_urls],
        key=lambda x: -x["score"],
    )
    if dc_candidates and dc_candidates[0]["score"] >= DC_MIN_SCORE:
        us_final[-1] = dc_candidates[0]
        return us_final, False

    return us_final, True


# ─────────────────────────────────────────────────────────────
# STEP 9 — Claude Haiku: Bilingual Output
# ─────────────────────────────────────────────────────────────
haiku_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def haiku(prompt: str, max_tokens: int = 200) -> str:
    msg = haiku_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def translate_headline(en_title: str) -> str:
    return haiku(
        f"Translate to Traditional Chinese (zh-TW). "
        f"Keep ALL proper nouns and acronyms in English: "
        f"FBI, CIA, NATO, Fed, Supreme Court, Senate, Congress, White House, Pentagon, "
        f"IRS, DOJ, EPA, CDC, FDA, IMF, WTO, OPEC, UN, EU, G7, G20. "
        f"Only translate natural-language verbs, nouns, and connectives.\n\n"
        f"Headline: {en_title}\n\n"
        f"Output: zh-TW translation only, no explanation.",
        max_tokens=150,
    )


def generate_en_summary(title: str, raw: str) -> str:
    return haiku(
        f"Write a 30–40 word English summary. "
        f"One sentence: what happened, who is involved, why it matters to a US news reader. No filler.\n\n"
        f"Title: {title}\n"
        f"Context: {raw[:400]}\n\n"
        f"Output: summary only.",
        max_tokens=100,
    )


def translate_summary(en_summary: str) -> str:
    return haiku(
        f"Translate to Traditional Chinese (zh-TW). "
        f"Keep ALL proper nouns, government agencies, and acronyms in English.\n\n"
        f"{en_summary}\n\n"
        f"Output: zh-TW translation only.",
        max_tokens=120,
    )


def age_label(published_at: datetime) -> str:
    hours = int((datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
    return f"{hours}h ago"


def slot_emoji(item: dict, pool: str, rank: int) -> str:
    if pool == "intl":
        return f"🌍 INTL #{rank}"
    if item.get("is_dc", False):
        return f"🏛️ US #{rank}"
    return f"🇺🇸 US #{rank}"


TIER_LABELS = {
    "dc_local": "DC / Local",
    "tier_1":   "National Security / Economy",
    "tier_2":   "Federal Policy",
    "tier_3":   "Politics / Law",
    "anti":     "General",
    "none":     "General",
}


def format_story(item: dict, pool: str, rank: int) -> str:
    zh_headline = translate_headline(item["title"])
    en_summary  = generate_en_summary(item["title"], item.get("summary_raw", ""))
    zh_summary  = translate_summary(en_summary)

    cross = item.get("cross_count", 1)
    cross_tag  = f"+ {cross - 1} sources" if cross > 1 else ""
    tag        = TIER_LABELS.get(item.get("topic_tier", "none"), "General")
    source_tag = f"{item['source_name']} {cross_tag} | {age_label(item['published_at'])} | Tag: {tag}"
    header     = slot_emoji(item, pool, rank)

    return (
        f"{header} — Score: {item['score']}\n\n"
        f"EN:  {item['title']}\n"
        f"中:  {zh_headline}\n\n"
        f"Source: {source_tag}\n\n"
        f"Summary (EN):\n{en_summary}\n\n"
        f"摘要（繁中）：\n{zh_summary}\n\n"
        f"🔗 {item['url']}"
    )


# ─────────────────────────────────────────────────────────────
# STEP 10 — Telegram Delivery
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
    log.info("=== Daily US General News Engine v1.0 starting ===")

    history = load_history()
    log.info(f"Loaded {len(history)} history entries")

    us_raw, intl_raw = fetch_all_sources()              # Step 1

    all_raw = us_raw + intl_raw
    all_raw = filter_by_age(all_raw)                    # Step 2
    all_raw = filter_aggregators(all_raw)               # Step 3
    all_raw = compute_cross_counts(all_raw)             # Step 4
    all_raw = score_all(all_raw)                        # Step 5
    all_raw = apply_recurrence_penalty(all_raw, history) # Step 6
    all_raw = cluster_and_dedup(all_raw)                # Step 7

    # Split back into pools after global dedup
    us_pool   = [i for i in all_raw if i["pool"] == "us"]
    intl_pool = [i for i in all_raw if i["pool"] == "intl"]

    # US selection
    us_candidates = apply_quality_floor(us_pool, QUALITY_FLOOR)
    if len(us_candidates) < US_SLOTS:
        us_candidates = apply_quality_floor(us_pool, QUALITY_FALLBACK)
        log.info("US: falling back to quality floor 55")
    us_candidates = apply_us_diversity(us_candidates, US_SLOTS)
    us_final = sorted(us_candidates, key=lambda x: -x["score"])[:US_SLOTS]

    # DC guarantee
    us_final, dc_slow = apply_dc_guarantee(us_final, us_pool)

    # INTL selection
    intl_candidates = apply_quality_floor(intl_pool, QUALITY_FLOOR)
    if len(intl_candidates) < INTL_SLOTS:
        intl_candidates = apply_quality_floor(intl_pool, QUALITY_FALLBACK)
        log.info("INTL: falling back to quality floor 55")
    intl_candidates = apply_intl_diversity(intl_candidates, INTL_SLOTS)
    intl_final = sorted(intl_candidates, key=lambda x: -x["score"])[:INTL_SLOTS]

    log.info(f"Final — US: {len(us_final)}, INTL: {len(intl_final)}")

    if not us_final and not intl_final:
        send_telegram("📭 No stories passed the quality floor today.")
        return

    # Send US stories
    for rank, item in enumerate(us_final, 1):
        log.info(f"Sending US #{rank}: {item['title'][:70]}")
        try:
            ok = send_telegram(format_story(item, "us", rank))
            log.info(f"  {'✅' if ok else '❌'} US #{rank}")
        except Exception as e:
            log.error(f"  ❌ US #{rank}: {e}")
        time.sleep(2)

    if dc_slow:
        send_telegram("⚠️ DC slow day — no DC/DMV story met the quality threshold today.")

    # Send INTL stories
    for rank, item in enumerate(intl_final, 1):
        log.info(f"Sending INTL #{rank}: {item['title'][:70]}")
        try:
            ok = send_telegram(format_story(item, "intl", rank))
            log.info(f"  {'✅' if ok else '❌'} INTL #{rank}")
        except Exception as e:
            log.error(f"  ❌ INTL #{rank}: {e}")
        time.sleep(2)

    all_titles = [i["title"] for i in us_final + intl_final]
    save_history(history, all_titles)
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
