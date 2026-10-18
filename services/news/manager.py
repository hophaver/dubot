"""News delivery loop: fetch → filter → summarize → DM, as an asyncio task on the bot's event loop."""
import asyncio
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import discord

from utils import home_log

from . import scoring, store, views
from .feeds import fetch_articles_for_topic
from .summarize import summarize_article, summarize_quiet_time_batch

FETCH_INTERVAL_SECONDS = 600  # 10 minutes between fetches
STARTUP_DELAY_SECONDS = 30
NEW_ARTICLES_PER_TOPIC = 5
SOURCE_ERROR_COOLDOWN_SECONDS = 12 * 3600
MAX_DELIVERY_ATTEMPTS = 3


def _fallback_summary(article: Dict, topic: str) -> str:
    return (
        f"**HEADLINE:** {article['title']}\n"
        f"• Development reported by {article.get('source', 'the source')} in `{topic}`.\n"
        "• The update may influence short-term decisions and planning.\n"
        "• Review the source article for full context and constraints.\n"
        "• Monitor follow-up announcements for scope and timeline changes.\n"
        f"**Follow topics:** {topic}, market impact, policy updates, competitor response"
    )


class NewsManager:
    def __init__(self):
        self.client: Optional[discord.Client] = None
        self._task: Optional[asyncio.Task] = None
        self._source_error_last_notice: Dict[str, float] = {}
        # (user_id, topic, article, attempts) whose DM failed; retried next cycle instead of being lost.
        self._retry: List[Tuple[int, str, Dict, int]] = []

    def start(self, client: discord.Client) -> None:
        """Start the delivery loop on the running event loop (call from setup_hook)."""
        self.client = client
        client.add_dynamic_items(*views.DYNAMIC_ITEMS)
        store.migrate_legacy_quiet_entries()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="news")
            home_log.log_sync("✅ News service started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                if self.client and self.client.is_ready():
                    await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                home_log.log_sync(f"⚠️ News cycle error: {e}")
                traceback.print_exc()
            await asyncio.sleep(FETCH_INTERVAL_SECONDS)

    async def _cycle(self) -> None:
        """One fetch-summarize-send cycle for all subscribed users."""
        await self._flush_daily_quiet_digests()
        subscribers = store.active_topic_subscribers()
        if not subscribers and not self._retry:
            return

        prefs = store.get_preferences()  # loaded once per cycle
        cfg = store.get_news_config()
        seen = store.SeenSet()
        summaries: Dict[Tuple[str, str], str] = {}  # (hash, detail) -> summary, shared across users

        retries, self._retry = self._retry, []
        for uid, topic, article, attempts in retries:
            if not await self._deliver_article(uid, article, topic, prefs, cfg, summaries, skip_filters=True):
                self._requeue(uid, topic, article, attempts)

        try:
            for topic, user_ids in subscribers.items():
                await self._process_topic(topic, sorted(user_ids), prefs, cfg, seen, summaries)
        finally:
            seen.save()

    async def _process_topic(
        self, topic: str, user_ids: List[int], prefs: Dict, cfg: Dict, seen: store.SeenSet, summaries: Dict
    ) -> None:
        try:
            articles, failed_sources = await asyncio.to_thread(fetch_articles_for_topic, topic)
        except Exception as e:
            home_log.log_sync(f"⚠️ Fetch failed for topic '{topic}': {e}")
            return
        if failed_sources:
            await self._notify_source_errors(topic, user_ids, failed_sources)

        new_articles = [a for a in articles if a["hash"] not in seen][:NEW_ARTICLES_PER_TOPIC]
        for article in new_articles:
            seen.add(article["hash"], {
                "title": article["title"],
                "link": article.get("link", ""),
                "source": article.get("source", ""),
                "topic": topic,
                "sent_at": datetime.now().isoformat(),
            })
        if not new_articles:
            return

        for uid in user_ids:
            tp = store.topic_prefs(prefs, uid, topic)
            ranked = sorted(
                new_articles,
                key=lambda a: (scoring.importance_score(a), scoring.relevance_score(tp, a)),
                reverse=True,
            )
            sent = 0
            for article in ranked:
                if sent >= scoring.quota(tp):
                    break
                try:
                    if await self._deliver_article(uid, article, topic, prefs, cfg, summaries):
                        sent += 1
                except Exception as e:
                    home_log.log_sync(f"⚠️ Deliver error user={uid} topic={topic}: {e}")
            await asyncio.sleep(1)  # avoid bursts between users

    def _requeue(self, uid: int, topic: str, article: Dict, attempts: int) -> None:
        if attempts + 1 < MAX_DELIVERY_ATTEMPTS:
            self._retry.append((uid, topic, article, attempts + 1))

    async def _notify_source_errors(self, topic: str, user_ids: List[int], failed_sources: List[Tuple[str, str]]) -> None:
        """Notify users of persistent source issues and offer one-click source disable."""
        if not self.client:
            return
        source_name, reason = failed_sources[0]
        now_ts = time.time()
        # Drop expired cooldown entries so the map doesn't grow forever.
        self._source_error_last_notice = {
            k: v for k, v in self._source_error_last_notice.items() if now_ts - v < SOURCE_ERROR_COOLDOWN_SECONDS
        }
        for uid in user_ids:
            key = f"{uid}|{topic}|{source_name.lower()}"
            if key in self._source_error_last_notice:
                continue
            self._source_error_last_notice[key] = now_ts
            message = (
                f"⚠️ I could not fetch updates from **{source_name}** for `{topic}`.\n"
                f"Error: `{reason[:180]}`\n\n"
                "If this keeps failing, you can disable this source for this topic."
            )
            try:
                user = await self.client.fetch_user(uid)
                await user.send(content=message, view=views.source_issue_view(topic, source_name))
            except Exception:
                continue

    async def _deliver_article(
        self,
        user_id: int,
        article: Dict,
        topic: str,
        prefs: Dict,
        cfg: Dict,
        summaries: Dict,
        skip_filters: bool = False,
    ) -> bool:
        """DM one article. Returns True if sent. A failed DM is queued for retry on the next cycle."""
        if not self.client:
            return False
        if store.user_in_quiet_window(user_id, cfg=cfg):
            store.add_quiet_time_article(user_id, {
                "title": article["title"],
                "link": article.get("link", ""),
                "source": article.get("source", ""),
                "summary": article.get("summary", ""),
                "topic": topic,
                "hash": article["hash"],
            })
            return False
        tp = store.topic_prefs(prefs, user_id, topic)
        if not skip_filters and scoring.should_skip(tp, article):
            return False

        detail = scoring.detail_level(tp)
        cache_key = (article["hash"], detail)
        if cache_key not in summaries:
            summaries[cache_key] = await summarize_article(article, detail_level=detail, topic=topic) or _fallback_summary(article, topic)
        summary = summaries[cache_key]

        compact_text = views.build_compact_news_text(summary, article)
        expanded_text = views.build_expanded_news_text(summary, article)
        source_url = (article.get("link") or "").strip()
        if source_url:
            expanded_text = f"{expanded_text}\n\n{source_url}"
        store.remember_delivered(article["hash"], topic, expanded_text[:2000])
        try:
            user = await self.client.fetch_user(user_id)
            await user.send(content=compact_text[:2000], view=views.compact_view(article["hash"]), suppress_embeds=True)
            return True
        except discord.Forbidden:
            home_log.log_sync(f"⚠️ Cannot DM user {user_id} (DMs disabled)")
            return False
        except Exception as e:
            home_log.log_sync(f"⚠️ DM send error for user {user_id}: {e}")
            if not skip_filters:
                self._requeue(user_id, topic, article, 0)
            return False

    async def _flush_daily_quiet_digests(self) -> None:
        """If user is outside their daily quiet window but has queued articles, send digest."""
        if not self.client:
            return
        cfg = store.get_news_config()
        now = datetime.now()
        for uid_key, qt_data in list(cfg.get("quiet_times", {}).items()):
            uid = store.user_id_from_key(uid_key)
            if uid is None or not isinstance(qt_data, dict) or not qt_data.get("articles"):
                continue
            if store.get_daily_quiet_schedule(uid, cfg) is None or store.user_in_quiet_window(uid, now, cfg):
                continue
            articles = store.pop_queued_articles_only(uid)
            if not articles:
                continue
            summary = await summarize_quiet_time_batch(articles) or (
                f"You had {len(articles)} articles queued. Could not generate summary."
            )
            try:
                user = await self.client.fetch_user(uid)
                await user.send(
                    content="⏰ **You're outside your daily quiet window** — here's what stacked up:",
                    embed=views.build_digest_embed(articles, summary),
                )
            except Exception as e:
                home_log.log_sync(f"⚠️ Quiet digest send error user={uid}: {e}")


news_manager = NewsManager()
