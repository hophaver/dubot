"""Article ranking and filtering. Pure functions over a user's topic feedback profile (``tp``)."""
from typing import Dict

from .store import extract_keywords

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


def detail_level(tp: Dict) -> str:
    """'detailed', 'normal', or 'brief' based on accumulated critical/not-critical feedback."""
    critical = tp.get("critical_count", 0)
    not_critical = tp.get("not_critical_count", 0)
    if critical > not_critical + 2:
        return "detailed"
    if not_critical > critical + 2:
        return "brief"
    return "normal"


def _suppressed_by_keywords(tp: Dict, title: str) -> bool:
    """Suppress if title keywords heavily overlap with suppressed keywords (after enough 'slop' votes)."""
    if tp.get("slop_count", 0) < 3:
        return False
    kws = extract_keywords(title)
    if not kws:
        return False
    suppress = set(tp.get("keywords_suppress", []))
    boost = set(tp.get("keywords_boost", []))
    suppress_hits = sum(1 for w in kws if w in suppress)
    boost_hits = sum(1 for w in kws if w in boost)
    return suppress_hits / len(kws) > 0.4 and boost_hits == 0


def relevance_score(tp: Dict, article: Dict) -> float:
    """Score how relevant an article is for this user/topic based on button feedback."""
    if not tp:
        return 0.0
    keywords = extract_keywords(article.get("title", "")) + extract_keywords(article.get("summary", ""))
    boost = set(tp.get("keywords_boost", []))
    suppress = set(tp.get("keywords_suppress", []))
    sources_boost = {(s or "").strip().lower() for s in tp.get("sources_boost", [])}
    sources_suppress = {(s or "").strip().lower() for s in tp.get("sources_suppress", [])}

    score = 1.6 * sum(1 for w in keywords if w in boost) - 2.0 * sum(1 for w in keywords if w in suppress)
    source_name = (article.get("source", "") or "").strip().lower()
    if source_name in sources_boost:
        score += 1.0
    if source_name in sources_suppress:
        score -= 1.2

    score += min(int(tp.get("critical_count", 0)), 12) * 0.08
    score -= min(int(tp.get("not_critical_count", 0)), 12) * 0.06
    score += min(int(tp.get("more_count", 0)), 12) * 0.06
    score -= min(int(tp.get("slop_count", 0)), 12) * 0.08
    return score


def importance_score(article: Dict) -> float:
    """Estimate article importance from title+summary signal words."""
    title = (article.get("title", "") or "").lower()
    text = f"{title} {(article.get('summary', '') or '').lower()}"

    score = sum(1.4 for p in HIGH_IMPORTANCE_PHRASES if p in text)
    score -= sum(1.0 for p in LOW_SIGNAL_PHRASES if p in text)
    # Hard boosts for very high-impact events.
    if any(k in text for k in ("breach", "zero-day", "bankruptcy", "war", "sanctions", "recall")):
        score += 1.8
    if any(k in text for k in ("earnings", "regulation", "acquisition", "merger", "layoffs")):
        score += 1.0
    # Penalize uncertain/noise framing in titles.
    if any(k in title for k in ("rumor", "might", "could", "opinion", "recap", "highlights")):
        score -= 1.2
    return score


def should_skip(tp: Dict, article: Dict) -> bool:
    """Use accumulated feedback to suppress low-relevance content."""
    source_name = (article.get("source", "") or "").strip().lower()
    disabled_sources = {(s or "").strip().lower() for s in tp.get("sources_disabled", [])}
    if source_name and source_name in disabled_sources:
        return True
    if _suppressed_by_keywords(tp, article.get("title", "")):
        return True

    importance = importance_score(article)
    if importance < 1.0:
        return True
    score = relevance_score(tp, article)
    critical = int(tp.get("critical_count", 0))
    if score <= -1.0:
        return True
    if importance < 1.8 and critical <= 1:
        return True
    if int(tp.get("slop_count", 0)) >= 4 and int(tp.get("more_count", 0)) == 0 and score < 0.5:
        return True
    return False


def quota(tp: Dict) -> int:
    """Per-cycle article cap for this user/topic based on feedback."""
    if not tp:
        return 1
    critical = int(tp.get("critical_count", 0))
    not_critical = int(tp.get("not_critical_count", 0))
    more_count = int(tp.get("more_count", 0))
    slop_count = int(tp.get("slop_count", 0))
    if critical > not_critical + 3 or more_count > slop_count + 4:
        return 2
    return 1
