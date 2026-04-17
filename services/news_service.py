"""Background news service: fetches RSS feeds, summarizes via LLM, sends DMs with feedback buttons."""

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import discord
import requests

from utils import home_log

InlineKeyboardButton = None
InlineKeyboardMarkup = None

DATA_DIR = os.path.join("data", "news")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "subscriptions.json")
SEEN_FILE = os.path.join(DATA_DIR, "seen_articles.json")
PREFERENCES_FILE = os.path.join(DATA_DIR, "preferences.json")
CONFIG_FILE = os.path.join(DATA_DIR, "news_config.json")

FETCH_INTERVAL_SECONDS = 600  # 10 minutes between fetches
MAX_SEEN_ARTICLES = 5000
MAX_ARTICLES_PER_CYCLE = 8  # per topic per cycle

# High-signal filtering: prioritize critical developments, suppress low-value chatter.
HIGH_IMPORTANCE_PHRASES = {
    "breakthrough", "major", "critical", "urgent", "warning", "lawsuit", "investigation",
    "regulation", "ban", "sanctions", "war", "ceasefire", "election", "policy",
    "acquisition", "merger", "bankruptcy", "layoffs", "breach", "vulnerability",
    "exploit", "zero-day", "outage", "recall", "approval", "launches", "releases",
    "earnings", "forecast", "downgrade", "upgrade", "tariff", "agreement",
}

LOW_SIGNAL_PHRASES = {
    "rumor", "rumour", "leak", "opinion", "editorial", "hands-on", "first look",
    "roundup", "recap", "highlights", "watch live", "live blog", "reaction",
    "best of", "top 10", "what we know", "might", "could", "maybe",
}


def _runtime_platform() -> str:
    return "discord"


def _platform_user_key(user_id: int) -> str:
    return f"{_runtime_platform()}:{int(user_id)}"


def _legacy_user_key(user_id: int) -> str:
    return str(int(user_id))


def _keys_for_lookup(user_id: int) -> List[str]:
    keys = [_platform_user_key(user_id)]
    if _runtime_platform() == "discord":
        keys.append(_legacy_user_key(user_id))
    return keys


def _platform_of_key(uid_key: str) -> str:
    if ":" in uid_key:
        return uid_key.split(":", 1)[0]
    return "discord"


def _user_id_from_key(uid_key: str) -> Optional[int]:
    raw = uid_key.split(":", 1)[1] if ":" in uid_key else uid_key
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _get_user_entry(subs: Dict, user_id: int) -> Dict:
    merged_topics = set()
    active = False
    for key in _keys_for_lookup(user_id):
        entry = subs.get(key)
        if not isinstance(entry, dict):
            continue
        active = active or bool(entry.get("active", False))
        for t in entry.get("topics", []):
            if isinstance(t, str) and t.strip():
                merged_topics.add(t.lower().strip())
    return {"topics": sorted(merged_topics), "active": active}

# Reliable RSS sources by topic keyword. Each entry: (feed_url, source_name).
# The service matches user topics to these via keyword overlap.
TOPIC_FEEDS: Dict[str, List[Tuple[str, str]]] = {
    "tech": [
        ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
        ("https://techcrunch.com/feed/", "TechCrunch"),
        ("https://www.theverge.com/rss/index.xml", "The Verge"),
        ("https://news.ycombinator.com/rss", "Hacker News"),
        ("https://www.wired.com/feed/rss", "Wired"),
    ],
    "ai": [
        ("https://www.technologyreview.com/feed/", "MIT Technology Review"),
        ("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI"),
        ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge AI"),
        ("https://news.ycombinator.com/rss", "Hacker News"),
    ],
    "finland": [
        ("https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET", "YLE News"),
        ("https://www.hs.fi/rss/tuoreimmat.xml", "Helsingin Sanomat"),
        ("https://yle.fi/rss/uutiset.rss", "YLE"),
    ],
    "us": [
        ("https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "New York Times"),
        ("https://feeds.npr.org/1001/rss.xml", "NPR"),
        ("https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "BBC US & Canada"),
        ("https://rss.politico.com/politics-news.xml", "Politico"),
    ],
    "trade": [
        ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC Business"),
        ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "NYT Business"),
    ],
    "global politics": [
        ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "NYT World"),
        ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
        ("https://feeds.npr.org/1004/rss.xml", "NPR World"),
    ],
    "science": [
        ("https://rss.nytimes.com/services/xml/rss/nyt/Science.xml", "NYT Science"),
        ("https://www.newscientist.com/section/news/feed/", "New Scientist"),
        ("https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "BBC Science"),
    ],
    "gaming": [
        ("https://kotaku.com/rss", "Kotaku"),
        ("https://www.polygon.com/rss/index.xml", "Polygon"),
        ("https://www.ign.com/articles.rss", "IGN"),
    ],
    "apple": [
        ("https://www.macrumors.com/macrumors.xml", "MacRumors"),
        ("https://9to5mac.com/feed/", "9to5Mac"),
        ("https://appleinsider.com/rss/news/", "AppleInsider"),
    ],
    "ios": [
        ("https://9to5mac.com/guides/ios/feed/", "9to5Mac iOS"),
        ("https://www.macrumors.com/roundup/ios/feed/", "MacRumors iOS"),
        ("https://www.theverge.com/rss/apple/index.xml", "The Verge Apple"),
    ],
    "macos": [
        ("https://9to5mac.com/guides/macos/feed/", "9to5Mac macOS"),
        ("https://www.macrumors.com/roundup/macos/feed/", "MacRumors macOS"),
        ("https://www.theverge.com/rss/apple/index.xml", "The Verge Apple"),
    ],
    "valve": [
        ("https://www.gamingonlinux.com/article_rss.php", "GamingOnLinux"),
        ("https://store.steampowered.com/feeds/news.xml", "Steam News"),
        ("https://www.pcgamer.com/rss/", "PC Gamer"),
    ],
    "hltv": [
        ("https://www.hltv.org/rss/news", "HLTV"),
        ("https://esportsinsider.com/feed", "Esports Insider"),
    ],
    "esports": [
        ("https://esportsinsider.com/feed", "Esports Insider"),
        ("https://dotesports.com/feed", "Dot Esports"),
        ("https://www.dexerto.com/feed/", "Dexerto"),
    ],
    "startups": [
        ("https://techcrunch.com/startups/feed/", "TechCrunch Startups"),
        ("https://www.theverge.com/rss/index.xml", "The Verge"),
        ("https://www.sifted.eu/feed", "Sifted"),
    ],
    "cybersecurity": [
        ("https://www.darkreading.com/rss.xml", "Dark Reading"),
        ("https://krebsonsecurity.com/feed/", "Krebs on Security"),
        ("https://www.bleepingcomputer.com/feed/", "BleepingComputer"),
    ],
    "crypto": [
        ("https://cointelegraph.com/rss", "CoinTelegraph"),
        ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
    ],
    "europe": [
        ("https://www.euronews.com/rss", "Euronews"),
        ("https://feeds.bbci.co.uk/news/world/europe/rss.xml", "BBC Europe"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml", "NYT Europe"),
    ],
}


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _hash_article(title: str, link: str) -> str:
    raw = f"{title.strip().lower()}|{link.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: str, data: Any) -> None:
    _ensure_data_dir()
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Subscriptions: {user_id_str: {"topics": [...], "active": bool}}
# ---------------------------------------------------------------------------

def get_subscriptions() -> Dict:
    return _load_json(SUBSCRIPTIONS_FILE, {})


def save_subscriptions(data: Dict) -> None:
    _save_json(SUBSCRIPTIONS_FILE, data)


def subscribe_user(user_id: int, topics: List[str]) -> List[str]:
    """Add topics for user. Returns the full topic list after merge."""
    subs = get_subscriptions()
    uid = _platform_user_key(user_id)
    entry = _get_user_entry(subs, user_id)
    entry["active"] = True
    existing = set(t.lower() for t in entry.get("topics", []))
    for t in topics:
        existing.add(t.lower().strip())
    entry["topics"] = sorted(existing)
    entry["active"] = True
    subs[uid] = entry
    if _runtime_platform() == "discord":
        subs.pop(_legacy_user_key(user_id), None)
    save_subscriptions(subs)
    return entry["topics"]


def unsubscribe_user(user_id: int, topics: Optional[List[str]] = None) -> List[str]:
    """Remove specific topics or all. Returns remaining topics."""
    subs = get_subscriptions()
    uid = _platform_user_key(user_id)
    entry = subs.get(uid)
    if not entry and _runtime_platform() == "discord":
        legacy = subs.get(_legacy_user_key(user_id))
        if legacy:
            entry = legacy
    if not isinstance(entry, dict):
        return []
    if topics is None:
        entry["topics"] = []
        entry["active"] = False
    else:
        remove_set = set(t.lower().strip() for t in topics)
        entry["topics"] = [t for t in entry["topics"] if t.lower() not in remove_set]
        if not entry["topics"]:
            entry["active"] = False
    subs[uid] = entry
    if _runtime_platform() == "discord":
        subs.pop(_legacy_user_key(user_id), None)
    save_subscriptions(subs)
    return entry["topics"]


def get_user_topics(user_id: int) -> List[str]:
    subs = get_subscriptions()
    entry = _get_user_entry(subs, user_id)
    if not entry.get("active", False):
        return []
    return entry.get("topics", [])


# ---------------------------------------------------------------------------
# Seen articles
# ---------------------------------------------------------------------------

def _load_seen() -> Dict:
    return _load_json(SEEN_FILE, {"hashes": [], "details": {}})


def _save_seen(data: Dict) -> None:
    _save_json(SEEN_FILE, data)


def _is_seen(article_hash: str) -> bool:
    return article_hash in _load_seen().get("hashes", [])


def _mark_seen(article_hash: str, meta: Dict) -> None:
    data = _load_seen()
    hashes = data.get("hashes", [])
    if article_hash not in hashes:
        hashes.append(article_hash)
        if len(hashes) > MAX_SEEN_ARTICLES:
            removed = hashes[:len(hashes) - MAX_SEEN_ARTICLES]
            hashes = hashes[-MAX_SEEN_ARTICLES:]
            for h in removed:
                data.get("details", {}).pop(h, None)
    data["hashes"] = hashes
    data.setdefault("details", {})[article_hash] = meta
    _save_seen(data)


# ---------------------------------------------------------------------------
# User preferences / feedback weights: {user_id_str: {topic: {weight_adjustments}}}
# ---------------------------------------------------------------------------

def get_preferences() -> Dict:
    return _load_json(PREFERENCES_FILE, {})


def save_preferences(data: Dict) -> None:
    _save_json(PREFERENCES_FILE, data)


def record_feedback(user_id: int, article_hash: str, feedback_type: str, topic: str) -> None:
    """Record user feedback on an article to calibrate future delivery."""
    prefs = get_preferences()
    uid = _platform_user_key(user_id)
    user_prefs = prefs.setdefault(uid, {})
    topic_prefs = user_prefs.setdefault(topic, {
        "slop_count": 0,
        "more_count": 0,
        "not_critical_count": 0,
        "critical_count": 0,
        "keywords_boost": [],
        "keywords_suppress": [],
        "sources_boost": [],
        "sources_suppress": [],
        "sources_disabled": [],
    })
    seen = _load_seen()
    article_meta = seen.get("details", {}).get(article_hash, {})
    title_words = _extract_keywords(article_meta.get("title", ""))
    source_name = (article_meta.get("source", "") or "").strip().lower()

    if feedback_type == "slop":
        topic_prefs["slop_count"] = topic_prefs.get("slop_count", 0) + 1
        for w in title_words:
            if w not in topic_prefs.get("keywords_suppress", []):
                topic_prefs.setdefault("keywords_suppress", []).append(w)
                # Cap list
                topic_prefs["keywords_suppress"] = topic_prefs["keywords_suppress"][-50:]
        if source_name and source_name not in topic_prefs.get("sources_suppress", []):
            topic_prefs.setdefault("sources_suppress", []).append(source_name)
            topic_prefs["sources_suppress"] = topic_prefs["sources_suppress"][-20:]
    elif feedback_type == "more":
        topic_prefs["more_count"] = topic_prefs.get("more_count", 0) + 1
        for w in title_words:
            if w not in topic_prefs.get("keywords_boost", []):
                topic_prefs.setdefault("keywords_boost", []).append(w)
                topic_prefs["keywords_boost"] = topic_prefs["keywords_boost"][-50:]
        if source_name and source_name not in topic_prefs.get("sources_boost", []):
            topic_prefs.setdefault("sources_boost", []).append(source_name)
            topic_prefs["sources_boost"] = topic_prefs["sources_boost"][-20:]
    elif feedback_type == "not_critical":
        topic_prefs["not_critical_count"] = topic_prefs.get("not_critical_count", 0) + 1
    elif feedback_type == "critical":
        topic_prefs["critical_count"] = topic_prefs.get("critical_count", 0) + 1

    prefs[uid] = user_prefs
    save_preferences(prefs)


def disable_source_for_user(user_id: int, topic: str, source_name: str) -> None:
    """Disable a specific source for a user+topic without affecting other curation signals."""
    source_key = (source_name or "").strip().lower()
    if not source_key:
        return

    prefs = get_preferences()
    uid = _platform_user_key(user_id)
    user_prefs = prefs.setdefault(uid, {})
    topic_prefs = user_prefs.setdefault(topic, {
        "slop_count": 0,
        "more_count": 0,
        "not_critical_count": 0,
        "critical_count": 0,
        "keywords_boost": [],
        "keywords_suppress": [],
        "sources_boost": [],
        "sources_suppress": [],
        "sources_disabled": [],
    })

    disabled = topic_prefs.setdefault("sources_disabled", [])
    if source_key not in disabled:
        disabled.append(source_key)
        topic_prefs["sources_disabled"] = disabled[-50:]

    # If a source is explicitly disabled, remove existing positive/negative source bias.
    topic_prefs["sources_boost"] = [s for s in topic_prefs.get("sources_boost", []) if (s or "").strip().lower() != source_key]
    topic_prefs["sources_suppress"] = [s for s in topic_prefs.get("sources_suppress", []) if (s or "").strip().lower() != source_key]

    prefs[uid] = user_prefs
    save_preferences(prefs)


def _extract_keywords(text: str) -> List[str]:
    """Extract meaningful words from a title for preference tracking."""
    stop = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "and",
            "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
            "from", "as", "into", "about", "it", "its", "this", "that", "will",
            "has", "have", "had", "not", "no", "do", "does", "did", "can",
            "could", "would", "should", "may", "might", "shall", "new", "says",
            "said", "how", "what", "when", "where", "why", "who", "which", "s"}
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [w for w in words if w not in stop][:10]


def get_user_detail_level(user_id: int, topic: str) -> str:
    """Return 'detailed', 'normal', or 'brief' based on accumulated feedback."""
    prefs = get_preferences()
    tp = prefs.get(_platform_user_key(user_id), {}).get(topic, {})
    if not tp and _runtime_platform() == "discord":
        tp = prefs.get(_legacy_user_key(user_id), {}).get(topic, {})
    critical = tp.get("critical_count", 0)
    not_critical = tp.get("not_critical_count", 0)
    if critical > not_critical + 2:
        return "detailed"
    elif not_critical > critical + 2:
        return "brief"
    return "normal"


def should_suppress_article(user_id: int, topic: str, title: str) -> bool:
    """Heuristic: suppress if title keywords heavily overlap with suppressed keywords."""
    prefs = get_preferences()
    tp = prefs.get(_platform_user_key(user_id), {}).get(topic, {})
    if not tp and _runtime_platform() == "discord":
        tp = prefs.get(_legacy_user_key(user_id), {}).get(topic, {})
    suppress = set(tp.get("keywords_suppress", []))
    boost = set(tp.get("keywords_boost", []))
    slop_count = tp.get("slop_count", 0)
    if slop_count < 3:
        return False
    kws = _extract_keywords(title)
    if not kws:
        return False
    suppress_hits = sum(1 for w in kws if w in suppress)
    boost_hits = sum(1 for w in kws if w in boost)
    ratio = suppress_hits / len(kws)
    return ratio > 0.4 and boost_hits == 0


def _get_user_topic_prefs(user_id: int, topic: str) -> Dict:
    prefs = get_preferences()
    tp = prefs.get(_platform_user_key(user_id), {}).get(topic, {})
    if not tp and _runtime_platform() == "discord":
        tp = prefs.get(_legacy_user_key(user_id), {}).get(topic, {})
    return tp if isinstance(tp, dict) else {}


def _article_relevance_score(user_id: int, topic: str, article: Dict) -> float:
    """Score how relevant an article is for this user/topic based on button feedback."""
    tp = _get_user_topic_prefs(user_id, topic)
    if not tp:
        return 0.0

    title_kws = _extract_keywords(article.get("title", ""))
    summary_kws = _extract_keywords(article.get("summary", ""))
    keywords = title_kws + summary_kws
    if not keywords:
        keywords = title_kws

    boost = set(tp.get("keywords_boost", []))
    suppress = set(tp.get("keywords_suppress", []))
    sources_boost = set((s or "").strip().lower() for s in tp.get("sources_boost", []))
    sources_suppress = set((s or "").strip().lower() for s in tp.get("sources_suppress", []))

    boost_hits = sum(1 for w in keywords if w in boost)
    suppress_hits = sum(1 for w in keywords if w in suppress)

    score = 0.0
    score += boost_hits * 1.6
    score -= suppress_hits * 2.0

    source_name = (article.get("source", "") or "").strip().lower()
    if source_name in sources_boost:
        score += 1.0
    if source_name in sources_suppress:
        score -= 1.2

    critical = int(tp.get("critical_count", 0))
    not_critical = int(tp.get("not_critical_count", 0))
    more_count = int(tp.get("more_count", 0))
    slop_count = int(tp.get("slop_count", 0))

    score += min(critical, 12) * 0.08
    score -= min(not_critical, 12) * 0.06
    score += min(more_count, 12) * 0.06
    score -= min(slop_count, 12) * 0.08

    return score


def _importance_score(article: Dict) -> float:
    """Estimate article importance from title+summary signal words."""
    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    text = f"{title} {summary}"

    score = 0.0
    score += sum(1.4 for p in HIGH_IMPORTANCE_PHRASES if p in text)
    score -= sum(1.0 for p in LOW_SIGNAL_PHRASES if p in text)

    # Hard boosts for very high-impact events.
    if any(k in text for k in {"breach", "zero-day", "bankruptcy", "war", "sanctions", "recall"}):
        score += 1.8
    if any(k in text for k in {"earnings", "regulation", "acquisition", "merger", "layoffs"}):
        score += 1.0

    # Penalize uncertain/noise framing in titles.
    if any(k in title for k in {"rumor", "might", "could", "opinion", "recap", "highlights"}):
        score -= 1.2

    return score


def _should_skip_article(user_id: int, topic: str, article: Dict) -> bool:
    """Use accumulated feedback to suppress low-relevance content."""
    tp = _get_user_topic_prefs(user_id, topic)
    source_name = (article.get("source", "") or "").strip().lower()
    disabled_sources = set((s or "").strip().lower() for s in tp.get("sources_disabled", []))
    if source_name and source_name in disabled_sources:
        return True

    if should_suppress_article(user_id, topic, article.get("title", "")):
        return True

    importance = _importance_score(article)
    if importance < 1.0:
        return True

    score = _article_relevance_score(user_id, topic, article)
    slop_count = int(tp.get("slop_count", 0))
    more_count = int(tp.get("more_count", 0))
    critical = int(tp.get("critical_count", 0))

    if score <= -1.0:
        return True
    if importance < 1.8 and critical <= 1:
        return True
    if slop_count >= 4 and more_count == 0 and score < 0.5:
        return True
    return False


def _article_quota_for_user(user_id: int, topic: str) -> int:
    """Dynamic per-cycle article cap based on user feedback profile."""
    tp = _get_user_topic_prefs(user_id, topic)
    if not tp:
        return 1

    critical = int(tp.get("critical_count", 0))
    not_critical = int(tp.get("not_critical_count", 0))
    more_count = int(tp.get("more_count", 0))
    slop_count = int(tp.get("slop_count", 0))

    if critical > not_critical + 3 or more_count > slop_count + 4:
        return 2
    if slop_count > more_count + 3 or not_critical > critical + 4:
        return 1
    return 1


# ---------------------------------------------------------------------------
# News config (model, quiet times)
# ---------------------------------------------------------------------------

def get_news_config() -> Dict:
    return _load_json(CONFIG_FILE, {
        "model_type": "local",
        "model_name": None,
        "cloud_history": [],
        "quiet_times": {},
        "custom_topic_feeds": {},
    })


def save_news_config(data: Dict) -> None:
    _save_json(CONFIG_FILE, data)


def _normalize_topic(topic: str) -> str:
    return (topic or "").strip().lower()


def _normalize_feed_source(source: str) -> str:
    return (source or "").strip() or "Custom Source"


def _validate_rss_url(url: str) -> bool:
    normalized = (url or "").strip().lower()
    if not (normalized.startswith("http://") or normalized.startswith("https://")):
        return False
    return "rss" in normalized or "feed" in normalized or normalized.endswith(".xml")


def get_custom_topic_feeds() -> Dict[str, List[Tuple[str, str]]]:
    cfg = get_news_config()
    raw = cfg.get("custom_topic_feeds", {})
    if not isinstance(raw, dict):
        return {}
    cleaned: Dict[str, List[Tuple[str, str]]] = {}
    for topic, entries in raw.items():
        topic_key = _normalize_topic(str(topic))
        if not topic_key:
            continue
        if not isinstance(entries, list):
            continue
        row: List[Tuple[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            url = (entry.get("url") or "").strip()
            source = _normalize_feed_source(str(entry.get("source", "")))
            if not url:
                continue
            row.append((url, source))
        if row:
            cleaned[topic_key] = row
    return cleaned


def add_custom_topic_feed(topic: str, url: str, source: str) -> Tuple[bool, str]:
    topic_key = _normalize_topic(topic)
    url_norm = (url or "").strip()
    source_norm = _normalize_feed_source(source)
    if not topic_key:
        return False, "Topic is required."
    if not _validate_rss_url(url_norm):
        return False, "URL must be a valid RSS/feed link (http/https, usually containing rss/feed/xml)."

    cfg = get_news_config()
    custom = cfg.setdefault("custom_topic_feeds", {})
    if not isinstance(custom, dict):
        custom = {}
        cfg["custom_topic_feeds"] = custom
    entries = custom.setdefault(topic_key, [])
    if not isinstance(entries, list):
        entries = []
        custom[topic_key] = entries

    for existing in entries:
        if isinstance(existing, dict) and (existing.get("url") or "").strip().lower() == url_norm.lower():
            existing["source"] = source_norm
            existing["url"] = url_norm
            save_news_config(cfg)
            return True, "Updated existing source for this topic."

    entries.append({"url": url_norm, "source": source_norm})
    save_news_config(cfg)
    return True, "Added source."


def remove_custom_topic_feed(topic: str, url: str) -> Tuple[bool, str]:
    topic_key = _normalize_topic(topic)
    url_norm = (url or "").strip().lower()
    cfg = get_news_config()
    custom = cfg.get("custom_topic_feeds", {})
    if not isinstance(custom, dict) or topic_key not in custom:
        return False, "Topic has no custom sources."
    entries = custom.get(topic_key, [])
    if not isinstance(entries, list):
        return False, "Topic has no custom sources."

    filtered = []
    removed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_url = (entry.get("url") or "").strip().lower()
        if entry_url == url_norm:
            removed = True
            continue
        filtered.append(entry)

    if not removed:
        return False, "Source URL not found for this topic."

    if filtered:
        custom[topic_key] = filtered
    else:
        custom.pop(topic_key, None)
    save_news_config(cfg)
    return True, "Removed source."


def set_news_model(model_type: str, model_name: str) -> None:
    cfg = get_news_config()
    normalized_type = (model_type or "local").strip().lower()
    if normalized_type not in {"local", "cloud"}:
        normalized_type = "local"
    normalized_name = (model_name or "").strip()
    cloud_history = cfg.get("cloud_history", [])
    if not isinstance(cloud_history, list):
        cloud_history = []
    cloud_history = [str(m).strip() for m in cloud_history if str(m).strip()]
    if normalized_type == "cloud" and normalized_name:
        cloud_history = [m for m in cloud_history if m != normalized_name]
        cloud_history.insert(0, normalized_name)
        cloud_history = cloud_history[:25]
    cfg["model_type"] = normalized_type
    cfg["model_name"] = normalized_name or None
    cfg["cloud_history"] = cloud_history
    save_news_config(cfg)


def get_news_model() -> Tuple[str, Optional[str]]:
    cfg = get_news_config()
    return cfg.get("model_type", "local"), cfg.get("model_name")


def get_news_recent_cloud_models() -> List[str]:
    cfg = get_news_config()
    history = cfg.get("cloud_history", [])
    if not isinstance(history, list):
        return []
    cleaned = [str(m).strip() for m in history if str(m).strip()]
    return list(dict.fromkeys(cleaned))


def _in_quiet_interval(now_minutes: int, pause_min: int, resume_min: int) -> bool:
    """True if now (minutes since midnight) falls in the daily quiet window [pause, resume).

    pause = when notifications stop, resume = when they start again (server local time).
    pause < resume: same calendar day (e.g. 1:00–9:00). pause > resume: crosses midnight.
    """
    if pause_min == resume_min:
        return False
    if pause_min < resume_min:
        return pause_min <= now_minutes < resume_min
    return now_minutes >= pause_min or now_minutes < resume_min


def parse_time_of_day(s: str) -> Optional[int]:
    """Parse '9.00', '9:00', '21:30', '9' into minutes since midnight (0–1439)."""
    s = (s or "").strip().lower().replace(".", ":")
    if not s:
        return None
    m = re.match(r"^(\d{1,2})\s*:\s*(\d{1,2})$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.match(r"^(\d{1,2})$", s)
        if not m2:
            return None
        h, mi = int(m2.group(1)), 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h * 60 + mi


def format_minutes_as_clock(mins: int) -> str:
    h, m = divmod(int(mins) % 1440, 60)
    return f"{h}:{m:02d}"


def get_daily_quiet_schedule(user_id: int) -> Optional[Tuple[int, int]]:
    """Return (pause_min, resume_min) if set, else None. Uses server local time."""
    cfg = get_news_config()
    qt_map = cfg.get("quiet_times", {})
    qt = qt_map.get(_platform_user_key(user_id))
    if not qt and _runtime_platform() == "discord":
        qt = qt_map.get(_legacy_user_key(user_id))
    if not qt:
        return None
    try:
        p = int(qt["pause_min"])
        r = int(qt["resume_min"])
        if not (0 <= p < 1440 and 0 <= r < 1440):
            return None
        return (p, r)
    except (KeyError, TypeError, ValueError):
        return None


def user_in_quiet_window(user_id: int, now: Optional[datetime] = None) -> bool:
    sched = get_daily_quiet_schedule(user_id)
    if not sched:
        return False
    pause_m, resume_m = sched
    now = now or datetime.now()
    now_min = now.hour * 60 + now.minute
    return _in_quiet_interval(now_min, pause_m, resume_m)


def set_daily_quiet_schedule(user_id: int, resume_min: int, pause_min: int) -> None:
    """resume = when notifications turn on again; pause = when they turn off (daily, server local time)."""
    cfg = get_news_config()
    qt = cfg.setdefault("quiet_times", {})
    uid = _platform_user_key(user_id)
    prev = qt.get(uid) if isinstance(qt.get(uid), dict) else {}
    articles = prev.get("articles") if isinstance(prev.get("articles"), list) else []
    qt[uid] = {"pause_min": pause_min, "resume_min": resume_min, "articles": articles}
    save_news_config(cfg)


def add_quiet_time_article(user_id: int, article: Dict) -> None:
    """Queue an article during quiet time for the summary later."""
    if get_daily_quiet_schedule(user_id) is None:
        return
    cfg = get_news_config()
    qt_map = cfg.get("quiet_times", {})
    qt = qt_map.get(_platform_user_key(user_id))
    if not qt and _runtime_platform() == "discord":
        qt = qt_map.get(_legacy_user_key(user_id))
    if qt:
        qt.setdefault("articles", []).append(article)
        save_news_config(cfg)


def pop_queued_articles_only(user_id: int) -> List[Dict]:
    """Return queued articles and clear the queue; keep the daily schedule."""
    cfg = get_news_config()
    qt_map = cfg.get("quiet_times", {})
    qt = qt_map.get(_platform_user_key(user_id))
    if not qt and _runtime_platform() == "discord":
        qt = qt_map.get(_legacy_user_key(user_id))
    if not qt:
        return []
    arts = list(qt.get("articles") or [])
    qt["articles"] = []
    save_news_config(cfg)
    return arts


def clear_quiet_time(user_id: int) -> None:
    cfg = get_news_config()
    qt = cfg.get("quiet_times", {})
    qt.pop(_platform_user_key(user_id), None)
    if _runtime_platform() == "discord":
        qt.pop(_legacy_user_key(user_id), None)
    save_news_config(cfg)


def migrate_legacy_quiet_entries() -> None:
    """Drop old duration-based quiet entries (only had 'until', no daily window)."""
    cfg = get_news_config()
    qt = cfg.get("quiet_times", {})
    changed = False
    for uid, data in list(qt.items()):
        if isinstance(data, dict) and "until" in data and "pause_min" not in data:
            qt.pop(uid, None)
            changed = True
    if changed:
        save_news_config(cfg)
        home_log.log_sync("🗑️ Removed legacy /news-time entries; set daily hours again with /news-time")


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def _resolve_feeds_for_topic(topic: str) -> List[Tuple[str, str]]:
    """Return list of (feed_url, source_name) for a user topic string."""
    topic_lower = topic.lower().strip()
    selected: List[Tuple[str, str]] = []
    if topic_lower in TOPIC_FEEDS:
        selected = list(TOPIC_FEEDS[topic_lower])
    else:
        # Fuzzy match: check if topic is substring of any key or vice-versa
        for key, feeds in TOPIC_FEEDS.items():
            if topic_lower in key or key in topic_lower:
                selected = list(feeds)
                break
        # Fallback: use multiple general feeds
        if not selected:
            selected = TOPIC_FEEDS.get("tech", [])[:2] + TOPIC_FEEDS.get("global politics", [])[:2]

    custom = get_custom_topic_feeds()
    custom_feeds = custom.get(topic_lower, [])
    if not custom_feeds:
        for key, feeds in custom.items():
            if topic_lower in key or key in topic_lower:
                custom_feeds.extend(feeds)

    merged = list(selected)
    seen_urls = {url.strip().lower() for url, _ in merged}
    for url, source in custom_feeds:
        key = (url or "").strip().lower()
        if not key or key in seen_urls:
            continue
        merged.append((url, source))
        seen_urls.add(key)

    return merged


def _fetch_feed(url: str, timeout: int = 15) -> Tuple[List[Dict], Optional[str]]:
    """Fetch and parse an RSS feed. Returns (articles, error_message)."""
    try:
        import feedparser
    except ImportError:
        home_log.log_sync("⚠️ feedparser not installed – news service cannot fetch RSS")
        return [], "feedparser not installed"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DuBot/1.0; +https://github.com/dubot)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    last_error = None
    feed = None
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            if getattr(feed, "entries", None):
                break
            bozo_exc = getattr(feed, "bozo_exception", None)
            if bozo_exc:
                last_error = f"parse error: {bozo_exc}"
            else:
                last_error = "no entries in feed response"
        except Exception as e:
            last_error = str(e)
        if attempt == 0:
            time.sleep(1.0)

    if not feed or not getattr(feed, "entries", None):
        err = last_error or "unknown fetch/parse failure"
        home_log.log_sync(f"⚠️ RSS fetch error for {url}: {err}")
        return [], err

    articles = []
    for entry in feed.entries[:MAX_ARTICLES_PER_CYCLE]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        summary = entry.get("summary", entry.get("description", "")).strip()
        # Strip HTML tags from summary
        summary = re.sub(r"<[^>]+>", "", summary)
        published = entry.get("published", entry.get("updated", ""))
        articles.append({
            "title": title,
            "link": link,
            "summary": summary[:1500],
            "published": published,
        })
    return articles, None


def fetch_articles_for_topic(topic: str) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """Fetch topic articles and return (articles, failed_sources[(source, reason)])."""
    feeds = _resolve_feeds_for_topic(topic)
    all_articles = []
    failed_sources: List[Tuple[str, str]] = []
    for url, source in feeds:
        articles, err = _fetch_feed(url)
        for a in articles:
            a["source"] = source
            a["topic"] = topic
            a["hash"] = _hash_article(a["title"], a["link"])
        all_articles.extend(articles)
        if err:
            failed_sources.append((source, err))
    return all_articles, failed_sources


# ---------------------------------------------------------------------------
# LLM summarization
# ---------------------------------------------------------------------------

async def _summarize_article(article: Dict, detail_level: str = "normal", topic: str = "") -> Optional[str]:
    """Summarize an article using the configured LLM."""
    from integrations import OLLAMA_URL
    from models import model_manager
    from utils.llm_service import _make_openrouter_request

    model_type, model_name = get_news_model()
    if not model_name:
        info = model_manager.get_user_model_info(0)
        model_type = info.get("provider", "local")
        model_name = info.get("model", "qwen2.5:7b")

    from utils.llm_service import get_enhanced_prompt

    is_finnish = topic.lower() == "finland"

    if detail_level == "detailed":
        length_instruction = "Keep it concise but complete: around 140-190 words."
    elif detail_level == "brief":
        length_instruction = "Keep it very concise: around 55-80 words."
    else:
        length_instruction = "Keep it concise and informative: around 90-130 words."

    language_note = ""
    if is_finnish:
        language_note = "If the article is in Finnish, keep the summary in Finnish. Otherwise translate to English."
    else:
        language_note = "The summary MUST be in English. Translate if necessary."

    prompt = f"""Summarize this news article for a busy professional. {length_instruction}

{language_note}

Write in a professional, coherent style.
Be factual and objective. Avoid hype, slang, and speculation.

Your response must follow this EXACT structure:
**HEADLINE:** [one-line headline, under 14 words]
[4-6 short bullet points that include: core facts, why this matters, likely implications, and practical context]
**Follow topics:** [3-6 short topic suggestions, comma-separated]

Article title: {article['title']}
Source: {article.get('source', 'Unknown')}
Content: {article.get('summary', 'No content preview available.')}

Do NOT add any introduction or conclusion outside the structure above."""

    messages = [
        {"role": "system", "content": "You are a professional news analyst. Summarize news articles clearly and objectively. Always follow the exact output format requested."},
        {"role": "user", "content": prompt},
    ]

    try:
        if (model_type or "local").strip().lower() == "cloud":
            response = await _make_openrouter_request(model_name, messages)
            if response and not response.startswith("Error:"):
                return response.strip()
            home_log.log_sync(f"⚠️ News cloud summarization failed: {response}")
            return None

        url = f"{OLLAMA_URL}/api/chat"
        data = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 800},
        }
        resp = await asyncio.to_thread(requests.post, url, json=data, timeout=90)
        if resp.status_code == 200:
            result = resp.json()
            return result.get("message", {}).get("content", "").strip()
    except Exception as e:
        home_log.log_sync(f"⚠️ News summarization error: {e}")
    return None


async def _summarize_quiet_time_batch(articles: List[Dict]) -> Optional[str]:
    """Produce a combined summary of articles queued during quiet time."""
    from integrations import OLLAMA_URL
    from models import model_manager
    from utils.llm_service import _make_openrouter_request

    if not articles:
        return None

    model_type, model_name = get_news_model()
    if not model_name:
        info = model_manager.get_user_model_info(0)
        model_type = info.get("provider", "local")
        model_name = info.get("model", "qwen2.5:7b")

    article_list = ""
    for i, a in enumerate(articles[:30], 1):
        article_list += f"\n{i}. [{a.get('topic', '?').upper()}] {a.get('title', 'Untitled')} ({a.get('source', '?')})\n   {a.get('summary', '')[:200]}\n"

    prompt = f"""You received {len(articles)} news articles while notifications were paused. Create a structured briefing.

Group them by topic/category. For each group:
- State the category with an emoji
- List the most important developments (combine similar stories)
- Note anything that needs immediate attention

Articles:
{article_list}

Keep the total summary concise but comprehensive. Use bullet points. End with a "🔴 Requires Attention" section if any story seems urgent."""

    messages = [
        {"role": "system", "content": "You are a news briefing assistant. Create clear, organized news summaries grouped by topic."},
        {"role": "user", "content": prompt},
    ]

    try:
        if (model_type or "local").strip().lower() == "cloud":
            response = await _make_openrouter_request(model_name, messages)
            if response and not response.startswith("Error:"):
                return response.strip()
            home_log.log_sync(f"⚠️ News cloud quiet-time summary failed: {response}")
            return None

        url = f"{OLLAMA_URL}/api/chat"
        data = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 2000},
        }
        resp = await asyncio.to_thread(requests.post, url, json=data, timeout=120)
        if resp.status_code == 200:
            result = resp.json()
            return result.get("message", {}).get("content", "").strip()
    except Exception as e:
        home_log.log_sync(f"⚠️ Quiet-time summary error: {e}")
    return None


# ---------------------------------------------------------------------------
# Discord message building
# ---------------------------------------------------------------------------

CATEGORY_EMOJIS = {
    "tech": "💻", "ai": "🤖", "finland": "🇫🇮", "us": "🇺🇸",
    "trade": "📈", "global politics": "🌍", "science": "🔬",
    "gaming": "🎮", "crypto": "₿", "europe": "🇪🇺",
    "apple": "🍎", "ios": "📱", "macos": "🖥️", "valve": "🕹️",
    "hltv": "🏆", "esports": "🎯", "startups": "🚀", "cybersecurity": "🛡️",
}


class NewsFeedbackView(discord.ui.View):
    """Persistent buttons for news feedback. Timeout=None so they survive restarts."""

    def __init__(self, article_hash: str, topic: str):
        super().__init__(timeout=None)
        self.article_hash = article_hash
        self.topic = topic

        slop_btn = discord.ui.Button(
            label="Slop", emoji="🗑️", style=discord.ButtonStyle.secondary,
            custom_id=f"news_slop_{article_hash}",
        )
        slop_btn.callback = self._slop_callback
        self.add_item(slop_btn)

        more_btn = discord.ui.Button(
            label="More like this", emoji="🔥", style=discord.ButtonStyle.success,
            custom_id=f"news_more_{article_hash}",
        )
        more_btn.callback = self._more_callback
        self.add_item(more_btn)

        notcrit_btn = discord.ui.Button(
            label="Not critical", emoji="📋", style=discord.ButtonStyle.secondary,
            custom_id=f"news_notcrit_{article_hash}",
        )
        notcrit_btn.callback = self._notcrit_callback
        self.add_item(notcrit_btn)

        crit_btn = discord.ui.Button(
            label="Critical", emoji="🚨", style=discord.ButtonStyle.danger,
            custom_id=f"news_crit_{article_hash}",
        )
        crit_btn.callback = self._crit_callback
        self.add_item(crit_btn)

    async def _slop_callback(self, interaction: discord.Interaction):
        record_feedback(interaction.user.id, self.article_hash, "slop", self.topic)
        await interaction.response.send_message("Got it — I'll send less of this type.", ephemeral=True)

    async def _more_callback(self, interaction: discord.Interaction):
        record_feedback(interaction.user.id, self.article_hash, "more", self.topic)
        await interaction.response.send_message("Noted — I'll find more content like this!", ephemeral=True)

    async def _notcrit_callback(self, interaction: discord.Interaction):
        record_feedback(interaction.user.id, self.article_hash, "not_critical", self.topic)
        await interaction.response.send_message("Understood — shorter summaries for this type going forward.", ephemeral=True)

    async def _crit_callback(self, interaction: discord.Interaction):
        record_feedback(interaction.user.id, self.article_hash, "critical", self.topic)
        await interaction.response.send_message("Marked as critical — I'll give more detail for news like this.", ephemeral=True)


def _clean_sentence(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return ""
    if s[-1] not in ".!?":
        s += "."
    return s


def _build_compact_news_text(summary: str, article: Dict) -> str:
    """Build a 2-4 sentence compact preview without source link."""
    lines = [(ln or "").strip() for ln in (summary or "").splitlines() if (ln or "").strip()]
    headline = ""
    body_parts: List[str] = []

    for line in lines:
        if line.lower().startswith("**headline:**"):
            headline = line.split(":", 1)[1].strip().strip("* ")
            continue
        if line.startswith("•"):
            body_parts.append(line.lstrip("•").strip())

    if not headline:
        headline = article.get("title", "News update")

    selected = []
    for part in body_parts:
        clean = _clean_sentence(part)
        if clean:
            selected.append(clean)
        if len(selected) >= 3:
            break

    if len(selected) < 2:
        fallback = _clean_sentence((article.get("summary", "") or "")[:280])
        if fallback:
            selected.append(fallback)
    if len(selected) < 2:
        selected.append("This item passed the high-importance filter for your topic.")

    selected = selected[:3]
    body = "\n".join(selected).strip()
    source_url = (article.get("link") or "").strip()
    source_line = f"\n\nSource: {source_url}" if source_url else ""
    if body:
        return f"# **{headline}**\n\n{body}{source_line}"
    return f"# **{headline}**{source_line}"


def _build_expanded_news_text(summary: str, article: Dict) -> str:
    """Render expanded content with a visual headline (without 'HEADLINE:' label)."""
    lines = [(ln or "").strip() for ln in (summary or "").splitlines()]
    headline = ""
    body_lines: List[str] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            body_lines.append("")
            continue
        if line.lower().startswith("**headline:**"):
            headline = line.split(":", 1)[1].strip().strip("* ")
            continue
        body_lines.append(line)

    if not headline:
        headline = article.get("title", "News update")

    body = "\n".join(body_lines).strip()
    if body:
        return f"# **{headline}**\n\n{body}"
    return f"# **{headline}**"


class NewsExpandedView(discord.ui.View):
    def __init__(self, article_hash: str, topic: str):
        super().__init__(timeout=86400)
        self.article_hash = article_hash
        self.topic = topic

    @discord.ui.button(label="Gem", emoji="💎", style=discord.ButtonStyle.success)
    async def gem(self, interaction: discord.Interaction, button: discord.ui.Button):
        record_feedback(interaction.user.id, self.article_hash, "more", self.topic)
        await interaction.response.send_message("Saved as gem — prioritizing similar stories.", ephemeral=True)

    @discord.ui.button(label="Not critical", emoji="📋", style=discord.ButtonStyle.secondary)
    async def not_critical(self, interaction: discord.Interaction, button: discord.ui.Button):
        record_feedback(interaction.user.id, self.article_hash, "not_critical", self.topic)
        await interaction.response.send_message("Noted — I will de-prioritize similar updates.", ephemeral=True)

    @discord.ui.button(label="Critical", emoji="🚨", style=discord.ButtonStyle.danger)
    async def critical(self, interaction: discord.Interaction, button: discord.ui.Button):
        record_feedback(interaction.user.id, self.article_hash, "critical", self.topic)
        await interaction.response.send_message("Marked critical — similar updates will be prioritized.", ephemeral=True)

    @discord.ui.button(emoji="❌", style=discord.ButtonStyle.danger)
    async def delete_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Does not affect personalization/curation signals.
        await interaction.response.defer()
        await interaction.message.delete()


class NewsSourceIssueView(discord.ui.View):
    def __init__(self, topic: str, source_name: str):
        super().__init__(timeout=86400)
        self.topic = topic
        self.source_name = source_name

    @discord.ui.button(label="Disable source", emoji="🚫", style=discord.ButtonStyle.danger)
    async def disable_source(self, interaction: discord.Interaction, button: discord.ui.Button):
        disable_source_for_user(interaction.user.id, self.topic, self.source_name)
        await interaction.response.edit_message(
            content=f"🚫 Source disabled for `{self.topic}`: **{self.source_name}**",
            view=None,
        )


class NewsCompactView(discord.ui.View):
    def __init__(self, article_hash: str, topic: str, compact_text: str, expanded_text: str):
        super().__init__(timeout=86400)
        self.article_hash = article_hash
        self.topic = topic
        self.compact_text = compact_text
        self.expanded_text = expanded_text

    @discord.ui.button(label="Expand", emoji="🔎", style=discord.ButtonStyle.primary)
    async def expand(self, interaction: discord.Interaction, button: discord.ui.Button):
        expanded_view = NewsExpandedView(self.article_hash, self.topic)
        await interaction.response.edit_message(
            content=self.expanded_text,
            view=expanded_view,
            suppress_embeds=False,
        )

    @discord.ui.button(label="Slop", emoji="🗑️", style=discord.ButtonStyle.secondary)
    async def slop(self, interaction: discord.Interaction, button: discord.ui.Button):
        record_feedback(interaction.user.id, self.article_hash, "slop", self.topic)
        slop_text = f"~~{self.compact_text}~~\n\n**SLOP**"
        await interaction.response.edit_message(content=slop_text, view=None)

    @discord.ui.button(emoji="❌", style=discord.ButtonStyle.danger)
    async def delete_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Delete should not affect personalization/curation signals.
        await interaction.response.defer()
        await interaction.message.delete()


def build_news_embed(article: Dict, summary: str, topic: str) -> discord.Embed:
    """Build a nicely formatted embed for a news article."""
    emoji = CATEGORY_EMOJIS.get(topic.lower(), "📰")
    color_map = {
        "tech": 0x00B4D8, "ai": 0x7B2FF7, "finland": 0x003580, "us": 0xB22234,
        "trade": 0x2E8B57, "global politics": 0xDAA520, "science": 0x20B2AA,
        "gaming": 0x9B59B6, "crypto": 0xF7931A, "europe": 0x003399,
        "apple": 0x111111, "ios": 0x0A84FF, "macos": 0x6E6E73, "valve": 0x1B2838,
        "hltv": 0xFF8C00, "esports": 0x8A2BE2, "startups": 0x2ECC71, "cybersecurity": 0x2C3E50,
    }
    color = color_map.get(topic.lower(), 0x5865F2)

    embed = discord.Embed(
        title=f"{emoji} {article['title'][:250]}",
        url=article.get("link", ""),
        color=color,
        timestamp=datetime.utcnow(),
    )

    # Trim summary to fit embed field limits
    if len(summary) > 4000:
        summary = summary[:3997] + "..."
    embed.description = summary

    source = article.get("source", "Unknown")
    pub = article.get("published", "")
    footer_parts = [f"Source: {source}"]
    if pub:
        footer_parts.append(f"Published: {pub[:25]}")
    footer_parts.append(f"Topic: {topic.capitalize()}")
    embed.set_footer(text=" • ".join(footer_parts))

    return embed


def build_news_text(article: Dict, summary: str, topic: str) -> str:
    """Build plain text news message for DMs."""
    emoji = CATEGORY_EMOJIS.get(topic.lower(), "📰")
    source = article.get("source", "Unknown")
    link = article.get("link", "N/A")
    return (
        f"{emoji} {article.get('title', 'Untitled')}\n\n"
        f"{summary}\n\n"
        f"Source: {source}\n"
        f"Topic: {topic}\n"
        f"Link: {link}"
    )


def build_news_feedback_keyboard(article_hash: str, topic: str):
    if not InlineKeyboardButton or not InlineKeyboardMarkup:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑️ Slop", callback_data=f"news:slop:{article_hash}:{topic}"),
                InlineKeyboardButton("🔥 More like this", callback_data=f"news:more:{article_hash}:{topic}"),
            ],
            [
                InlineKeyboardButton("📋 Not critical", callback_data=f"news:not_critical:{article_hash}:{topic}"),
                InlineKeyboardButton("🚨 Critical", callback_data=f"news:critical:{article_hash}:{topic}"),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Main news manager
# ---------------------------------------------------------------------------

class NewsManager:
    def __init__(self):
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.client: Optional[discord.Client] = None
        self.platform = _runtime_platform()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._source_error_last_notice: Dict[str, float] = {}
        _ensure_data_dir()

    def set_client(self, client: discord.Client) -> None:
        self.client = client
        self.platform = "discord"
        self.loop = client.loop if client else None

    def start(self) -> None:
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            home_log.log_sync("✅ News service started")

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        home_log.log_sync("🛑 News service stopped")

    def _run(self) -> None:
        # Wait a bit for the bot to be ready
        time.sleep(30)
        while self.running:
            try:
                if self.loop and self.client and self.client.is_ready():
                    future = asyncio.run_coroutine_threadsafe(self._cycle(), self.loop)
                    future.result(timeout=300)
            except Exception as e:
                home_log.log_sync(f"⚠️ News cycle error: {e}")
                traceback.print_exc()
            time.sleep(FETCH_INTERVAL_SECONDS)

    async def _cycle(self) -> None:
        """One fetch-summarize-send cycle for all subscribed users."""
        subs = get_subscriptions()
        if not subs:
            return

        migrate_legacy_quiet_entries()
        # Flush queued digests when users are outside their daily quiet window
        await self._flush_daily_quiet_digests()

        # Collect all unique topics
        all_topics: Dict[str, set] = {}
        runtime = _runtime_platform()
        for uid_str, entry in subs.items():
            if _platform_of_key(uid_str) != runtime:
                continue
            uid_val = _user_id_from_key(uid_str)
            if uid_val is None:
                continue
            if not entry.get("active", False):
                continue
            for topic in entry.get("topics", []):
                all_topics.setdefault(topic, set()).add(uid_val)

        if not all_topics:
            return

        # Fetch articles per topic
        for topic, user_ids in all_topics.items():
            try:
                articles, failed_sources = await asyncio.to_thread(fetch_articles_for_topic, topic)
            except Exception as e:
                home_log.log_sync(f"⚠️ Fetch failed for topic '{topic}': {e}")
                continue

            if failed_sources:
                await self._notify_source_errors(topic, sorted(user_ids), failed_sources)

            # Filter already seen
            new_articles = [a for a in articles if not _is_seen(a["hash"])]
            if not new_articles:
                continue

            # Limit per cycle
            new_articles = new_articles[:5]

            for article in new_articles:
                _mark_seen(article["hash"], {
                    "title": article["title"],
                    "link": article.get("link", ""),
                    "source": article.get("source", ""),
                    "topic": topic,
                    "sent_at": datetime.now().isoformat(),
                })

            for uid in sorted(user_ids):
                ranked_articles = sorted(
                    new_articles,
                    key=lambda a: (
                        _importance_score(a),
                        _article_relevance_score(uid, topic, a),
                    ),
                    reverse=True,
                )
                quota = _article_quota_for_user(uid, topic)
                sent_count = 0

                for article in ranked_articles:
                    if sent_count >= quota:
                        break
                    try:
                        delivered = await self._deliver_article(uid, article, topic)
                        if delivered:
                            sent_count += 1
                    except Exception as e:
                        home_log.log_sync(f"⚠️ Deliver error user={uid} topic={topic}: {e}")

                # Small delay between users to avoid bursts
                await asyncio.sleep(1)

    async def _notify_source_errors(self, topic: str, user_ids: List[int], failed_sources: List[Tuple[str, str]]) -> None:
        """Notify users of persistent source issues and offer one-click source disable."""
        if self.platform != "discord" or not self.client or not failed_sources:
            return

        source_name, reason = failed_sources[0]
        now_ts = time.time()
        cooldown_seconds = 12 * 60 * 60

        for uid in user_ids:
            key = f"{uid}|{topic}|{source_name.lower()}"
            last_ts = self._source_error_last_notice.get(key, 0.0)
            if now_ts - last_ts < cooldown_seconds:
                continue
            self._source_error_last_notice[key] = now_ts

            message = (
                f"⚠️ I could not fetch updates from **{source_name}** for `{topic}`.\n"
                f"Error: `{reason[:180]}`\n\n"
                "If this keeps failing, you can disable this source for this topic."
            )
            view = NewsSourceIssueView(topic, source_name)
            try:
                user = await self.client.fetch_user(uid)
                await user.send(content=message, view=view)
            except Exception:
                continue

    async def _deliver_article(self, user_id: int, article: Dict, topic: str) -> bool:
        """Deliver a single article to a user via DM. Respects quiet time and preferences."""
        if not self.client:
            return False

        if user_in_quiet_window(user_id):
            add_quiet_time_article(user_id, {
                "title": article["title"],
                "link": article.get("link", ""),
                "source": article.get("source", ""),
                "summary": article.get("summary", ""),
                "topic": topic,
                "hash": article["hash"],
            })
            return False

        # Check user preferences for suppression
        if _should_skip_article(user_id, topic, article):
            return False

        # Determine detail level
        detail = get_user_detail_level(user_id, topic)

        # Summarize
        summary = await _summarize_article(article, detail_level=detail, topic=topic)
        if not summary:
            summary = (
                f"**HEADLINE:** {article['title']}\n"
                f"• Development reported by {article.get('source', 'the source')} in `{topic}`.\n"
                "• The update may influence short-term decisions and planning.\n"
                "• Review the source article for full context and constraints.\n"
                "• Monitor follow-up announcements for scope and timeline changes.\n"
                f"**Follow topics:** {topic}, market impact, policy updates, competitor response"
            )

        # Discord path: compact first, then expand in-place.
        compact_text = _build_compact_news_text(summary, article)
        source_url = (article.get("link") or "").strip()
        expanded_text = _build_expanded_news_text(summary, article)
        if source_url:
            expanded_text = f"{expanded_text}\n\n{source_url}"
        view = NewsCompactView(article["hash"], topic, compact_text, expanded_text)
        try:
            user = await self.client.fetch_user(user_id)
            await user.send(content=compact_text, view=view, suppress_embeds=True)
            return True
        except discord.Forbidden:
            home_log.log_sync(f"⚠️ Cannot DM user {user_id} (DMs disabled)")
        except Exception as e:
            home_log.log_sync(f"⚠️ DM send error for user {user_id}: {e}")
        return False

    async def _flush_daily_quiet_digests(self) -> None:
        """If user is outside their daily quiet window but has queued articles, send digest."""
        if not self.client:
            return
        cfg = get_news_config()
        qt_dict = cfg.get("quiet_times", {})
        now = datetime.now()
        runtime = _runtime_platform()

        for uid_str, qt_data in list(qt_dict.items()):
            if _platform_of_key(uid_str) != runtime:
                continue
            uid = _user_id_from_key(uid_str)
            if uid is None:
                continue
            if get_daily_quiet_schedule(uid) is None:
                continue
            if user_in_quiet_window(uid, now):
                continue
            articles = qt_data.get("articles") or []
            if not articles:
                continue

            articles = pop_queued_articles_only(uid)
            if not articles:
                continue

            summary = await _summarize_quiet_time_batch(articles)
            if not summary:
                summary = f"You had {len(articles)} articles queued. Could not generate summary."

            sources = set()
            for a in articles[:15]:
                sources.add(a.get("source", ""))

            embed = discord.Embed(
                title="📬 News Briefing — Quiet hours summary",
                description=summary[:4096],
                color=0xF39C12,
                timestamp=datetime.utcnow(),
            )
            links = []
            for a in articles[:15]:
                links.append(f"• [{a.get('title', 'Link')[:60]}]({a.get('link', '')})")
            embed.add_field(
                name="📎 Sources",
                value=", ".join(s for s in sources if s)[:1024] or "Various",
                inline=False,
            )
            if links:
                embed.add_field(
                    name="🔗 Links",
                    value="\n".join(links[:10])[:1024],
                    inline=False,
                )
            embed.set_footer(text=f"{len(articles)} articles during quiet hours")

            try:
                user = await self.client.fetch_user(uid)
                await user.send(
                    content="⏰ **You're outside your daily quiet window** — here's what stacked up:",
                    embed=embed,
                )
            except Exception as e:
                home_log.log_sync(f"⚠️ Quiet digest send error user={uid}: {e}")


# Global instance
news_manager = NewsManager()
