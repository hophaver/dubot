"""News persistence: subscriptions, seen articles, feedback preferences, config (model, quiet hours, custom feeds).

User keys are ``discord:<id>``; bare ``<id>`` keys from older versions are still read and migrated on write.
"""
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from utils import home_log
from utils.json_store import CachedJsonFile
from utils.paths import data_path

DATA_DIR = data_path("news")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "subscriptions.json")
SEEN_FILE = os.path.join(DATA_DIR, "seen_articles.json")
PREFERENCES_FILE = os.path.join(DATA_DIR, "preferences.json")
CONFIG_FILE = os.path.join(DATA_DIR, "news_config.json")
DELIVERED_FILE = os.path.join(DATA_DIR, "delivered.json")

MAX_SEEN_ARTICLES = 5000
MAX_QUIET_QUEUE = 100  # the digest only summarizes the first 30
MAX_DELIVERED = 500  # expandable messages whose buttons keep working across restarts

_KEY_PREFIX = "discord:"

_subs_store = CachedJsonFile(SUBSCRIPTIONS_FILE, default={})
_prefs_store = CachedJsonFile(PREFERENCES_FILE, default={})
_config_store = CachedJsonFile(
    CONFIG_FILE,
    default={
        "model_type": "local",
        "model_name": None,
        "cloud_history": [],
        "quiet_times": {},
        "custom_topic_feeds": {},
    },
)
_seen_store = CachedJsonFile(SEEN_FILE, default={"hashes": [], "details": {}})
_delivered_store = CachedJsonFile(DELIVERED_FILE, default={"articles": {}, "sources": {}})


# ---------------------------------------------------------------------------
# User keys
# ---------------------------------------------------------------------------

def user_key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{int(user_id)}"


def _lookup_keys(user_id: int) -> List[str]:
    return [user_key(user_id), str(int(user_id))]


def user_id_from_key(uid_key: str) -> Optional[int]:
    """Discord user id from a stored key; None for keys from other platforms or garbage."""
    if ":" in uid_key:
        platform, raw = uid_key.split(":", 1)
        if platform != "discord":
            return None
    else:
        raw = uid_key
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _lookup(mapping: Dict, user_id: int) -> Any:
    for k in _lookup_keys(user_id):
        if k in mapping:
            return mapping[k]
    return None


# ---------------------------------------------------------------------------
# Subscriptions: {user_key: {"topics": [...], "active": bool}}
# ---------------------------------------------------------------------------

def get_subscriptions() -> Dict:
    return _subs_store.get()


def save_subscriptions(data: Dict) -> None:
    _subs_store.save(data, indent=2)


def _get_user_entry(subs: Dict, user_id: int) -> Dict:
    merged_topics: Set[str] = set()
    active = False
    for key in _lookup_keys(user_id):
        entry = subs.get(key)
        if not isinstance(entry, dict):
            continue
        active = active or bool(entry.get("active", False))
        for t in entry.get("topics", []):
            if isinstance(t, str) and t.strip():
                merged_topics.add(t.lower().strip())
    return {"topics": sorted(merged_topics), "active": active}


def subscribe_user(user_id: int, topics: List[str]) -> List[str]:
    """Add topics for user. Returns the full topic list after merge."""
    subs = get_subscriptions()
    entry = _get_user_entry(subs, user_id)
    entry["topics"] = sorted(set(entry["topics"]) | {t.lower().strip() for t in topics})
    entry["active"] = True
    subs[user_key(user_id)] = entry
    subs.pop(str(int(user_id)), None)
    save_subscriptions(subs)
    return entry["topics"]


def unsubscribe_user(user_id: int, topics: Optional[List[str]] = None) -> List[str]:
    """Remove specific topics or all. Returns remaining topics."""
    subs = get_subscriptions()
    entry = _lookup(subs, user_id)
    if not isinstance(entry, dict):
        return []
    if topics is None:
        entry["topics"] = []
        entry["active"] = False
    else:
        remove_set = {t.lower().strip() for t in topics}
        entry["topics"] = [t for t in entry.get("topics", []) if t.lower() not in remove_set]
        if not entry["topics"]:
            entry["active"] = False
    subs[user_key(user_id)] = entry
    subs.pop(str(int(user_id)), None)
    save_subscriptions(subs)
    return entry["topics"]


def get_user_topics(user_id: int) -> List[str]:
    entry = _get_user_entry(get_subscriptions(), user_id)
    return entry["topics"] if entry.get("active") else []


def active_topic_subscribers() -> Dict[str, Set[int]]:
    """{topic: {user_id, ...}} for active subscriptions."""
    out: Dict[str, Set[int]] = {}
    for uid_key, entry in get_subscriptions().items():
        uid = user_id_from_key(uid_key)
        if uid is None or not isinstance(entry, dict) or not entry.get("active", False):
            continue
        for topic in entry.get("topics", []):
            out.setdefault(topic, set()).add(uid)
    return out


# ---------------------------------------------------------------------------
# Seen articles (loaded once per cycle as a SeenSet, written once at the end)
# ---------------------------------------------------------------------------

class SeenSet:
    def __init__(self):
        data = _seen_store.get()
        self.hashes: List[str] = list(data.get("hashes", []))
        self.details: Dict[str, Dict] = dict(data.get("details", {}))
        self._set = set(self.hashes)
        self._dirty = False

    def __contains__(self, article_hash: str) -> bool:
        return article_hash in self._set

    def add(self, article_hash: str, meta: Dict) -> None:
        if article_hash not in self._set:
            self.hashes.append(article_hash)
            self._set.add(article_hash)
        self.details[article_hash] = meta
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        if len(self.hashes) > MAX_SEEN_ARTICLES:
            for h in self.hashes[: len(self.hashes) - MAX_SEEN_ARTICLES]:
                self.details.pop(h, None)
            self.hashes = self.hashes[-MAX_SEEN_ARTICLES:]
        _seen_store.save({"hashes": self.hashes, "details": self.details}, indent=None)
        self._dirty = False


def seen_article_meta(article_hash: str) -> Dict:
    return _seen_store.get().get("details", {}).get(article_hash, {}) or {}


# ---------------------------------------------------------------------------
# Delivered messages: state the persistent buttons need after a restart
# ---------------------------------------------------------------------------

def remember_delivered(article_hash: str, topic: str, expanded_text: str) -> None:
    data = _delivered_store.get()
    arts = data.setdefault("articles", {})
    arts.pop(article_hash, None)
    arts[article_hash] = {"topic": topic, "expanded": expanded_text}
    while len(arts) > MAX_DELIVERED:
        arts.pop(next(iter(arts)))
    _delivered_store.save(data, indent=None)


def delivered_article(article_hash: str) -> Dict:
    return _delivered_store.get().get("articles", {}).get(article_hash, {}) or {}


def remember_source(source_id: str, topic: str, source_name: str) -> None:
    data = _delivered_store.get()
    sources = data.setdefault("sources", {})
    sources[source_id] = {"topic": topic, "source": source_name}
    while len(sources) > MAX_DELIVERED:
        sources.pop(next(iter(sources)))
    _delivered_store.save(data, indent=None)


def remembered_source(source_id: str) -> Dict:
    return _delivered_store.get().get("sources", {}).get(source_id, {}) or {}


# ---------------------------------------------------------------------------
# Preferences / feedback: {user_key: {topic: {...counters and keyword lists}}}
# ---------------------------------------------------------------------------

def _new_topic_prefs() -> Dict[str, Any]:
    return {
        "slop_count": 0,
        "more_count": 0,
        "not_critical_count": 0,
        "critical_count": 0,
        "keywords_boost": [],
        "keywords_suppress": [],
        "sources_boost": [],
        "sources_suppress": [],
        "sources_disabled": [],
    }


def get_preferences() -> Dict:
    return _prefs_store.get()


def save_preferences(data: Dict) -> None:
    _prefs_store.save(data, indent=2)


def topic_prefs(prefs: Dict, user_id: int, topic: str) -> Dict:
    """One user's feedback profile for a topic (from an already-loaded prefs dict)."""
    for k in _lookup_keys(user_id):
        tp = prefs.get(k, {}).get(topic)
        if isinstance(tp, dict) and tp:
            return tp
    return {}


def extract_keywords(text: str) -> List[str]:
    """Extract meaningful words from a title for preference tracking."""
    stop = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "and",
            "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
            "from", "as", "into", "about", "it", "its", "this", "that", "will",
            "has", "have", "had", "not", "no", "do", "does", "did", "can",
            "could", "would", "should", "may", "might", "shall", "new", "says",
            "said", "how", "what", "when", "where", "why", "who", "which", "s"}
    words = re.findall(r"[a-zA-Z]{3,}", (text or "").lower())
    return [w for w in words if w not in stop][:10]


def _append_capped(lst: List[str], items: List[str], cap: int) -> List[str]:
    for it in items:
        if it and it not in lst:
            lst.append(it)
    return lst[-cap:]


def record_feedback(user_id: int, article_hash: str, feedback_type: str, topic: str) -> None:
    """Record user feedback on an article to calibrate future delivery."""
    prefs = get_preferences()
    user_prefs = prefs.setdefault(user_key(user_id), {})
    tp = user_prefs.setdefault(topic, _new_topic_prefs())
    meta = seen_article_meta(article_hash)
    title_words = extract_keywords(meta.get("title", ""))
    source_name = (meta.get("source", "") or "").strip().lower()

    if feedback_type == "slop":
        tp["slop_count"] = tp.get("slop_count", 0) + 1
        tp["keywords_suppress"] = _append_capped(tp.get("keywords_suppress", []), title_words, 50)
        tp["sources_suppress"] = _append_capped(tp.get("sources_suppress", []), [source_name], 20)
    elif feedback_type == "more":
        tp["more_count"] = tp.get("more_count", 0) + 1
        tp["keywords_boost"] = _append_capped(tp.get("keywords_boost", []), title_words, 50)
        tp["sources_boost"] = _append_capped(tp.get("sources_boost", []), [source_name], 20)
    elif feedback_type == "not_critical":
        tp["not_critical_count"] = tp.get("not_critical_count", 0) + 1
    elif feedback_type == "critical":
        tp["critical_count"] = tp.get("critical_count", 0) + 1
    save_preferences(prefs)


def disable_source_for_user(user_id: int, topic: str, source_name: str) -> None:
    """Disable a specific source for a user+topic without affecting other curation signals."""
    source_key = (source_name or "").strip().lower()
    if not source_key:
        return
    prefs = get_preferences()
    tp = prefs.setdefault(user_key(user_id), {}).setdefault(topic, _new_topic_prefs())
    tp["sources_disabled"] = _append_capped(tp.get("sources_disabled", []), [source_key], 50)
    # If a source is explicitly disabled, remove existing positive/negative source bias.
    for k in ("sources_boost", "sources_suppress"):
        tp[k] = [s for s in tp.get(k, []) if (s or "").strip().lower() != source_key]
    save_preferences(prefs)


# ---------------------------------------------------------------------------
# News config: model, custom feeds, quiet hours
# ---------------------------------------------------------------------------

def get_news_config() -> Dict:
    return _config_store.get()


def save_news_config(data: Dict) -> None:
    _config_store.save(data, indent=2)


def _normalize_topic(topic: str) -> str:
    return (topic or "").strip().lower()


def _validate_rss_url(url: str) -> bool:
    normalized = (url or "").strip().lower()
    if not (normalized.startswith("http://") or normalized.startswith("https://")):
        return False
    return "rss" in normalized or "feed" in normalized or normalized.endswith(".xml")


def get_custom_topic_feeds() -> Dict[str, List[Tuple[str, str]]]:
    raw = get_news_config().get("custom_topic_feeds", {})
    if not isinstance(raw, dict):
        return {}
    cleaned: Dict[str, List[Tuple[str, str]]] = {}
    for topic, entries in raw.items():
        topic_key = _normalize_topic(str(topic))
        if not topic_key or not isinstance(entries, list):
            continue
        row = [
            ((e.get("url") or "").strip(), (str(e.get("source", "")).strip() or "Custom Source"))
            for e in entries
            if isinstance(e, dict) and (e.get("url") or "").strip()
        ]
        if row:
            cleaned[topic_key] = row
    return cleaned


def add_custom_topic_feed(topic: str, url: str, source: str) -> Tuple[bool, str]:
    topic_key = _normalize_topic(topic)
    url_norm = (url or "").strip()
    source_norm = (source or "").strip() or "Custom Source"
    if not topic_key:
        return False, "Topic is required."
    if not _validate_rss_url(url_norm):
        return False, "URL must be a valid RSS/feed link (http/https, usually containing rss/feed/xml)."

    cfg = get_news_config()
    custom = cfg.get("custom_topic_feeds")
    if not isinstance(custom, dict):
        custom = cfg["custom_topic_feeds"] = {}
    entries = custom.get(topic_key)
    if not isinstance(entries, list):
        entries = custom[topic_key] = []
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
    entries = custom.get(topic_key) if isinstance(custom, dict) else None
    if not isinstance(entries, list):
        return False, "Topic has no custom sources."
    filtered = [e for e in entries if isinstance(e, dict) and (e.get("url") or "").strip().lower() != url_norm]
    if len(filtered) == len([e for e in entries if isinstance(e, dict)]):
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
    history = cfg.get("cloud_history", [])
    history = [str(m).strip() for m in history if str(m).strip()] if isinstance(history, list) else []
    if normalized_type == "cloud" and normalized_name:
        history = [normalized_name] + [m for m in history if m != normalized_name]
    cfg["model_type"] = normalized_type
    cfg["model_name"] = normalized_name or None
    cfg["cloud_history"] = history[:25]
    save_news_config(cfg)


def get_news_model() -> Tuple[str, Optional[str]]:
    cfg = get_news_config()
    return cfg.get("model_type", "local"), cfg.get("model_name")


def get_news_recent_cloud_models() -> List[str]:
    history = get_news_config().get("cloud_history", [])
    if not isinstance(history, list):
        return []
    return list(dict.fromkeys(str(m).strip() for m in history if str(m).strip()))


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
    m = re.match(r"^(\d{1,2})(?:\s*:\s*(\d{1,2}))?$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h * 60 + mi


def format_minutes_as_clock(mins: int) -> str:
    h, m = divmod(int(mins) % 1440, 60)
    return f"{h}:{m:02d}"


def _quiet_entry(cfg: Dict, user_id: int) -> Optional[Dict]:
    qt = _lookup(cfg.get("quiet_times", {}), user_id)
    return qt if isinstance(qt, dict) else None


def get_daily_quiet_schedule(user_id: int, cfg: Optional[Dict] = None) -> Optional[Tuple[int, int]]:
    """Return (pause_min, resume_min) if set, else None. Uses server local time."""
    qt = _quiet_entry(cfg if cfg is not None else get_news_config(), user_id)
    if not qt:
        return None
    try:
        p, r = int(qt["pause_min"]), int(qt["resume_min"])
    except (KeyError, TypeError, ValueError):
        return None
    return (p, r) if (0 <= p < 1440 and 0 <= r < 1440) else None


def user_in_quiet_window(user_id: int, now: Optional[datetime] = None, cfg: Optional[Dict] = None) -> bool:
    sched = get_daily_quiet_schedule(user_id, cfg)
    if not sched:
        return False
    now = now or datetime.now()
    return _in_quiet_interval(now.hour * 60 + now.minute, *sched)


def set_daily_quiet_schedule(user_id: int, resume_min: int, pause_min: int) -> None:
    """resume = when notifications turn on again; pause = when they turn off (daily, server local time)."""
    cfg = get_news_config()
    prev = _quiet_entry(cfg, user_id) or {}
    articles = prev.get("articles") if isinstance(prev.get("articles"), list) else []
    qt = cfg.setdefault("quiet_times", {})
    qt.pop(str(int(user_id)), None)
    qt[user_key(user_id)] = {"pause_min": pause_min, "resume_min": resume_min, "articles": articles}
    save_news_config(cfg)


def add_quiet_time_article(user_id: int, article: Dict) -> None:
    """Queue an article during quiet time for the summary later."""
    cfg = get_news_config()
    qt = _quiet_entry(cfg, user_id)
    if not qt or get_daily_quiet_schedule(user_id, cfg) is None:
        return
    queue = qt.setdefault("articles", [])
    queue.append(article)
    qt["articles"] = queue[-MAX_QUIET_QUEUE:]
    save_news_config(cfg)


def pop_queued_articles_only(user_id: int) -> List[Dict]:
    """Return queued articles and clear the queue; keep the daily schedule."""
    cfg = get_news_config()
    qt = _quiet_entry(cfg, user_id)
    if not qt:
        return []
    arts = list(qt.get("articles") or [])
    qt["articles"] = []
    save_news_config(cfg)
    return arts


def clear_quiet_time(user_id: int) -> None:
    cfg = get_news_config()
    qt = cfg.get("quiet_times", {})
    for k in _lookup_keys(user_id):
        qt.pop(k, None)
    save_news_config(cfg)


def migrate_legacy_quiet_entries() -> None:
    """Drop old duration-based quiet entries (only had 'until', no daily window). Run once at startup."""
    cfg = get_news_config()
    qt = cfg.get("quiet_times", {})
    stale = [uid for uid, d in qt.items() if isinstance(d, dict) and "until" in d and "pause_min" not in d]
    if stale:
        for uid in stale:
            qt.pop(uid, None)
        save_news_config(cfg)
        home_log.log_sync("🗑️ Removed legacy /news-time entries; set daily hours again with /news-time")
