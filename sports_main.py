#!/usr/bin/env python3
"""
Daily US Sports News Engine — v1.1
PRD Owner: Alvin Chen
Schedule: 17:00 ET daily via cron-job.org → workflow_dispatch
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
SOURCES = [
    {"name": "ESPN",           "url": "https://www.espn.com/espn/rss/news",                                                         "W_site": 92},
    {"name": "ESPN NBA",       "url": "https://www.espn.com/espn/rss/nba/news",                                                     "W_site": 92},
    {"name": "ESPN MLB",       "url": "https://www.espn.com/espn/rss/mlb/news",                                                     "W_site": 88},
    {"name": "ESPN NFL",       "url": "https://www.espn.com/espn/rss/nfl/news",                                                     "W_site": 85},
    {"name": "Yahoo Sports",   "url": "https://sports.yahoo.com/rss/",                                                              "W_site": 85},
    {"name": "CBS Sports",     "url": "https://www.cbssports.com/rss/headlines/",                                                   "W_site": 82},
    {"name": "GNews NBA",      "url": "https://news.google.com/rss/search?q=NBA&hl=en-US&gl=US&ceid=US:en",                         "W_site": 85},
    {"name": "GNews MLB",      "url": "https://news.google.com/rss/search?q=MLB+baseball&hl=en-US&gl=US&ceid=US:en",               "W_site": 83},
    {"name": "GNews NFL",      "url": "https://news.google.com/rss/search?q=NFL+football&hl=en-US&gl=US&ceid=US:en",               "W_site": 83},
    {"name": "GNews NHL",      "url": "https://news.google.com/rss/search?q=NHL+hockey&hl=en-US&gl=US&ceid=US:en",                 "W_site": 83},
]

REDDIT_NBA_URL = "https://www.reddit.com/r/nba/hot.json?limit=100"

AGGREGATOR_PATTERNS = [
    r"power rankings?", r"power poll",
    r"top \d+ (plays?|dunks?|moments?|trades?|players?|games?)",
    r"weekly recap", r"week in review", r"week that was",
    r"awards? predictions?", r"mock draft",
    r"roundup", r"wrap-?up", r"recap",
    r"best of", r"biggest of",
    r"\d+ things you (need to know|missed)",
    r"morning tip", r"daily digest",
    r"stock up[,\s]+stock down", r"winners and losers",
]

SPORT_TIERS = {
    "tier_1": {
        "multiplier": 1.25,
        "sport": "NBA",
        "keywords": [
            "nba", "lakers", "celtics", "warriors", "knicks", "bulls", "heat",
            "bucks", "76ers", "sixers", "nets", "clippers", "suns", "nuggets",
            "timberwolves", "thunder", "pacers", "cavaliers", "cavs", "magic",
            "hawks", "hornets", "pistons", "raptors", "wizards", "spurs",
            "pelicans", "rockets", "mavericks", "mavs", "grizzlies", "jazz",
            "trail blazers", "blazers", "kings", "lebron", "curry", "durant",
            "giannis", "luka", "jokic", "tatum", "jaylen brown", "kawhi",
            "embiid", "wembanyama", "basketball",
        ],
    },
    "tier_2": {
        "multiplier": 1.15,
        "sport": "NCAA/MLB",
        "keywords": [
            "ncaa", "march madness", "final four", "college basketball",
            "mlb", "yankees", "dodgers", "red sox", "cubs", "mets", "giants",
            "braves", "astros", "cardinals", "phillies", "padres", "mariners",
            "world series", "baseball", "ohtani", "aaron judge",
        ],
    },
    "tier_3": {
        "multiplier": 1.10,
        "sport": "Other",
        "keywords": [
            "nfl", "super bowl", "quarterback", "nhl", "stanley cup",
            "epl", "premier league", "champions league", "uefa",
            "wimbledon", "us open", "australian open", "french open", "roland garros",
            "ufc", "ppv", "grand slam",
        ],
    },
    "suppressed": {
        "multiplier": 0.85,
        "sport": "Suppressed",
        "keywords": [
            "nascar", "mls", "esports", "fantasy football", "fantasy basketball",
            "start sit", "waiver wire", "pga tour", "golf",
            "college football regular", "bowl game",
        ],
    },
}

INFO_GAIN_KEYWORDS = [
    "career-high", "all-time", "first since", "record",
    "traded", "trade", "signed", "signing", "extension",
    "mvp", "dpoy", "mip", "roy", "finals mvp",
    "championship", "playoff", "finals", "title",
    "triple-double", "50-point", "60-point",
    "max contract", "supermax", "qualifying offer",
]

STORY_TYPE_PATTERNS = [
    ("Trade",           [r"trad(e|ed|ing)"]),
    ("Signing",         [r"sign(ed|s|ing)", r"extension", r"max contract", r"supermax"]),
    ("Injury",          [r"injur(y|ied)", r"\bout\b", r"questionable", r"doubtful", r"ruled out"]),
    ("Award",           [r"\bmvp\b", r"\bdpoy\b", r"\bmip\b", r"\broy\b", r"award", r"honor"]),
    ("Coaching Change", [r"head coach", r"\bfired\b", r"\bhired\b"]),
    ("Draft",           [r"\bdraft(ed|ing|pick)?\b", r"\bselected\b"]),
    ("Suspension",      [r"suspend(ed|sion)"]),
    ("Record",          [r"career-?high", r"all-time", r"first since", r"\brecord\b"]),
    ("Retirement",      [r"retir(e|ed|ement|ing)"]),
    ("Game Result",     [r"\bbeats?\b", r"\bdefeats?\b", r"overtime", r"\bscore\b"]),
]

QUALITY_FLOOR     = 75
MAX_STORIES       = 5
WINDOW_HOURS      = 48
CLUSTER_THRESHOLD = 0.50
MAX_PER_SPORT     = 2
SLOW_NEWS_TAG     = "📭 Quiet sports day."
HISTORY_FILE      = Path(__file__).parent / "sports_history.json"
HISTORY_DAYS      = 7

ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_SPORTS_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


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
                continue
            items.append({
                "title":        entry.get("title", "").strip(),
                "url":          entry.get("link", ""),
                "published_at": datetime(*pub[:6], tzinfo=timezone.utc),
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
# STEP 4 — r/nba Signal
# ─────────────────────────────────────────────────────────────
def fetch_rnba_ranks() -> dict:
    try:
        resp = requests.get(
            REDDIT_NBA_URL, timeout=10,
            headers={"User-Agent": "sports-news-bot/1.1"}
        )
        data = resp.json()
        ranks = {}
        for i, post in enumerate(data["data"]["children"]):
            ranks[post["data"]["title"].lower()] = i + 1
        log.info(f"r/nba: {len(ranks)} posts fetched")
        return ranks
    except Exception as e:
        log.warning(f"r/nba unavailable: {e}")
        return {}


def rnba_score(item: dict, rnba_ranks: dict) -> int:
    words = set(re.findall(r"\b\w{4,}\b", item["title"].lower()))
    for reddit_title, rank in rnba_ranks.items():
        reddit_words = set(re.findall(r"\b\w{4,}\b", reddit_title))
        if len(words) and len(words & reddit_words) / len(words) >= 0.5:
            if rank <= 10:  return 25
            if rank <= 30:  return 15
            if rank <= 100: return 8
    return 0


# ─────────────────────────────────────────────────────────────
# STEP 5 — Scoring
# ─────────────────────────────────────────────────────────────
def w_fresh(published_at: datetime) -> int:
    age_h = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
    if age_h < 12: return 100
    if age_h < 24: return 95
    return 80


def w_rank(item: dict, rnba_ranks: dict, use_rnba: bool) -> float:
    pos  = item["rss_position"]
    hero = 40 if pos == 1 else (25 if pos <= 3 else 10)
    rss  = 30 if pos == 1 else (20 if pos <= 3 else (10 if pos <= 10 else 0))
    if use_rnba:
        r = rnba_score(item, rnba_ranks)
        return hero * 0.50 + rss * 0.20 + r * 0.30
    return hero * 0.71 + rss * 0.29


def get_sport_tier(title: str, summary: str) -> tuple:
    text = (title + " " + summary).lower()
    for kw in SPORT_TIERS["suppressed"]["keywords"]:
        if kw in text:
            return SPORT_TIERS["suppressed"]["multiplier"], "suppressed", SPORT_TIERS["suppressed"]["sport"]
    for tier in ["tier_1", "tier_2", "tier_3"]:
        for kw in SPORT_TIERS[tier]["keywords"]:
            if kw in text:
                return SPORT_TIERS[tier]["multiplier"], tier, SPORT_TIERS[tier]["sport"]
    return 1.0, "none", "General Sports"


def get_info_gain(title: str, summary: str) -> float:
    text = (title + " " + summary).lower()
    return 1.20 if any(kw in text for kw in INFO_GAIN_KEYWORDS) else 1.0


def get_story_type(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    for story_type, patterns in STORY_TYPE_PATTERNS:
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            return story_type
    return "Analysis"


def w_cross_value(count: int) -> int:
    if count >= 3: return 100
    if count == 2: return 90
    return 80


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


def score_all(items: list, rnba_ranks: dict, use_rnba: bool) -> list:
    for item in items:
        ws = item["W_site"]
        wf = w_fresh(item["published_at"])
        wr = w_rank(item, rnba_ranks, use_rnba)
        wc = w_cross_value(item["cross_count"])

        base = ws * 0.30 + wr * 0.20 + wf * 0.20 + wc * 0.30

        tier_mult, tier_key, sport = get_sport_tier(item["title"], item["summary_raw"])
        ig_mult = get_info_gain(item["title"], item["summary_raw"])

        item["score"]      = round(base * tier_mult * ig_mult, 1)
        item["sport_tier"] = tier_key
        item["sport"]      = sport
        item["story_type"] = get_story_type(item["title"], item["summary_raw"])
    return items


# ─────────────────────────────────────────────────────────────
# STEP 6 — Recurrence Penalty (7-day lookback)
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
            if max_sim >= 0.85:
                item["score"] = 0   # hard exclude
            elif max_sim >= 0.70:
                item["score"] = round(item["score"] * 0.60, 1)
    except Exception:
        pass
    result = [i for i in items if i["score"] > 0]
    log.info(f"Recurrence filter: {len(items)} → {len(result)}")
    return result


# ─────────────────────────────────────────────────────────────
# STEP 7 — Cluster & Dedup
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
# STEP 8 — Diversity + Quality Floor
# ─────────────────────────────────────────────────────────────
def apply_quality_floor(items: list) -> list:
    passed = [i for i in items if i["score"] >= QUALITY_FLOOR]
    log.info(f"Quality floor ≥{QUALITY_FLOOR}: {len(items)} → {len(passed)}")
    return passed


def apply_diversity(items: list) -> list:
    sport_counts = {}
    result = []
    for item in sorted(items, key=lambda x: -x["score"]):
        sport = item.get("sport", "General Sports")
        sport_counts[sport] = sport_counts.get(sport, 0) + 1
        if sport_counts[sport] <= MAX_PER_SPORT:
            result.append(item)
    return result


# ─────────────────────────────────────────────────────────────
# STEP 9 — Claude Haiku: Bilingual Output
# ─────────────────────────────────────────────────────────────
haiku_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def haiku(prompt: str, max_tokens: int = 300) -> str:
    msg = haiku_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def translate_headline(en_title: str) -> str:
    return haiku(
        f"Translate to Traditional Chinese (zh-TW). "
        f"Keep ALL player names, team names, league names (NBA, MLB, NFL, NHL, EPL, NCAA), "
        f"acronyms (MVP, DPOY, MIP, ROY, Finals MVP), contract terms "
        f"(max contract, supermax, qualifying offer), and dollar amounts in English. "
        f"Only translate natural-language verbs, common nouns, and connectives.\n\n"
        f"Headline: {en_title}\n\n"
        f"Output: zh-TW translation only, no explanation.",
        max_tokens=200,
    )


def generate_en_summary(title: str, raw: str) -> str:
    return haiku(
        f"Write a 30–40 word English summary of this US sports story. "
        f"One sentence: what happened, who's involved, why it matters. No filler.\n\n"
        f"Title: {title}\n"
        f"Context: {raw[:400]}\n\n"
        f"Output: summary only.",
        max_tokens=100,
    )


def translate_summary(en_summary: str) -> str:
    return haiku(
        f"Translate to Traditional Chinese (zh-TW). "
        f"Keep ALL player names, team names, league names, acronyms, "
        f"and dollar amounts in English.\n\n"
        f"{en_summary}\n\n"
        f"Output: zh-TW translation only.",
        max_tokens=120,
    )


def age_label(published_at: datetime) -> str:
    hours = int((datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
    return f"{hours}h ago"


def format_story(rank: int, item: dict) -> str:
    zh_headline = translate_headline(item["title"])
    en_summary  = generate_en_summary(item["title"], item["summary_raw"])
    zh_summary  = translate_summary(en_summary)

    cross = item.get("cross_count", 1)
    cross_tag  = f"+ {cross - 1} sources" if cross > 1 else ""
    source_tag = f"{item['source_name']} {cross_tag} | {age_label(item['published_at'])} | Tag: {item['sport']} / {item['story_type']}"

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
# STEP 10 — Telegram Delivery
# ─────────────────────────────────────────────────────────────
def send_telegram(text: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(retries):
        try:
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_SPORTS_CHAT_ID,
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
    log.info("=== Daily Sports News Engine v1.1 starting ===")

    history = load_history()
    log.info(f"Loaded {len(history)} history entries (last {HISTORY_DAYS} days)")

    raw = fetch_all_sources()           # Step 1
    raw = filter_by_age(raw)            # Step 2
    raw = filter_aggregators(raw)       # Step 3

    rnba_ranks = fetch_rnba_ranks()     # Step 4
    use_rnba   = bool(rnba_ranks)

    raw = compute_cross_counts(raw)         # Step 5a
    raw = score_all(raw, rnba_ranks, use_rnba)  # Step 5b
    raw.sort(key=lambda x: -x["score"])

    raw   = apply_recurrence_penalty(raw, history)  # Step 6
    raw   = cluster_and_dedup(raw)          # Step 7
    raw   = apply_quality_floor(raw)        # Step 8a
    raw   = apply_diversity(raw)            # Step 8b
    final = raw[:MAX_STORIES]              # Top 5

    log.info(f"Stories to deliver: {len(final)}")

    if not final:
        send_telegram("📭 No sports stories passed the quality floor today.")
        save_history(history, [])
        return

    for rank, item in enumerate(final, 1):
        log.info(f"Sending #{rank}: {item['title'][:70]}")
        try:
            message = format_story(rank, item)
            ok = send_telegram(message)
            log.info(f"  {'✅' if ok else '❌'} #{rank}")
        except Exception as e:
            log.error(f"  ❌ Error on #{rank}: {e}")
        time.sleep(2)

    if len(final) < 3:
        send_telegram(SLOW_NEWS_TAG)

    save_history(history, [item["title"] for item in final])
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
