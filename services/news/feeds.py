"""RSS feed catalogue and fetching."""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from utils import home_log
from utils.http import session

from .store import get_custom_topic_feeds

MAX_ARTICLES_PER_FEED = 8
_FETCH_WORKERS = 6
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DuBot/1.0; +https://github.com/dubot)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

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


def hash_article(title: str, link: str) -> str:
    raw = f"{title.strip().lower()}|{link.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def resolve_feeds_for_topic(topic: str) -> List[Tuple[str, str]]:
    """Return list of (feed_url, source_name) for a user topic string."""
    topic_lower = topic.lower().strip()
    selected: List[Tuple[str, str]] = list(TOPIC_FEEDS.get(topic_lower, []))
    if not selected:
        # Fuzzy match: check if topic is substring of any key or vice-versa
        for key, feeds in TOPIC_FEEDS.items():
            if topic_lower in key or key in topic_lower:
                selected = list(feeds)
                break
    if not selected:
        selected = TOPIC_FEEDS["tech"][:2] + TOPIC_FEEDS["global politics"][:2]

    custom = get_custom_topic_feeds()
    custom_feeds = list(custom.get(topic_lower, []))
    if not custom_feeds:
        for key, feeds in custom.items():
            if topic_lower in key or key in topic_lower:
                custom_feeds.extend(feeds)

    seen_urls = {url.strip().lower() for url, _ in selected}
    for url, source in custom_feeds:
        key = (url or "").strip().lower()
        if key and key not in seen_urls:
            selected.append((url, source))
            seen_urls.add(key)
    return selected


def _fetch_feed(url: str, timeout: int = 15) -> Tuple[List[Dict], Optional[str]]:
    """Fetch and parse an RSS feed (blocking). Returns (articles, error_message)."""
    try:
        import feedparser
    except ImportError:
        home_log.log_sync("⚠️ feedparser not installed – news service cannot fetch RSS")
        return [], "feedparser not installed"

    last_error = None
    feed = None
    for attempt in range(2):
        try:
            resp = session().get(url, timeout=timeout, headers=_HEADERS)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            if getattr(feed, "entries", None):
                break
            bozo_exc = getattr(feed, "bozo_exception", None)
            last_error = f"parse error: {bozo_exc}" if bozo_exc else "no entries in feed response"
        except Exception as e:
            last_error = str(e)
        if attempt == 0:
            time.sleep(1.0)

    if not feed or not getattr(feed, "entries", None):
        err = last_error or "unknown fetch/parse failure"
        home_log.log_sync(f"⚠️ RSS fetch error for {url}: {err}")
        return [], err

    articles = []
    for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        summary = re.sub(r"<[^>]+>", "", entry.get("summary", entry.get("description", "")).strip())
        articles.append({
            "title": title,
            "link": link,
            "summary": summary[:1500],
            "published": entry.get("published", entry.get("updated", "")),
        })
    return articles, None


def fetch_articles_for_topic(topic: str) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """Fetch a topic's feeds in parallel (blocking). Returns (articles, failed_sources[(source, reason)])."""
    feeds = resolve_feeds_for_topic(topic)
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, max(1, len(feeds)))) as pool:
        results = list(pool.map(lambda f: _fetch_feed(f[0]), feeds))
    all_articles: List[Dict] = []
    failed_sources: List[Tuple[str, str]] = []
    for (_, source), (articles, err) in zip(feeds, results):
        for a in articles:
            a["source"] = source
            a["topic"] = topic
            a["hash"] = hash_article(a["title"], a["link"])
        all_articles.extend(articles)
        if err:
            failed_sources.append((source, err))
    return all_articles, failed_sources
