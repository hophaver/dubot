"""Compatibility facade for the news package (services/news/)."""
from services.news.feeds import TOPIC_FEEDS
from services.news.manager import news_manager
from services.news.store import (
    add_custom_topic_feed,
    clear_quiet_time,
    format_minutes_as_clock,
    get_custom_topic_feeds,
    get_daily_quiet_schedule,
    get_news_model,
    get_news_recent_cloud_models,
    get_user_topics,
    parse_time_of_day,
    remove_custom_topic_feed,
    set_daily_quiet_schedule,
    set_news_model,
    subscribe_user,
    unsubscribe_user,
    user_in_quiet_window,
)
from services.news.views import CATEGORY_EMOJIS

__all__ = [
    "CATEGORY_EMOJIS",
    "TOPIC_FEEDS",
    "add_custom_topic_feed",
    "clear_quiet_time",
    "format_minutes_as_clock",
    "get_custom_topic_feeds",
    "get_daily_quiet_schedule",
    "get_news_model",
    "get_news_recent_cloud_models",
    "get_user_topics",
    "news_manager",
    "parse_time_of_day",
    "remove_custom_topic_feed",
    "set_daily_quiet_schedule",
    "set_news_model",
    "subscribe_user",
    "unsubscribe_user",
    "user_in_quiet_window",
]
