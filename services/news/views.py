"""Discord rendering for news DMs. Buttons are DynamicItems so they keep working after a restart."""
import hashlib
import re
from typing import Dict, List

import discord

from .store import (
    delivered_article,
    disable_source_for_user,
    record_feedback,
    remember_source,
    remembered_source,
    seen_article_meta,
)

CATEGORY_EMOJIS = {
    "tech": "💻", "ai": "🤖", "finland": "🇫🇮", "us": "🇺🇸",
    "trade": "📈", "global politics": "🌍", "science": "🔬",
    "gaming": "🎮", "crypto": "₿", "europe": "🇪🇺",
    "apple": "🍎", "ios": "📱", "macos": "🖥️", "valve": "🕹️",
    "hltv": "🏆", "esports": "🎯", "startups": "🚀", "cybersecurity": "🛡️",
}


def _clean_sentence(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return ""
    if s[-1] not in ".!?":
        s += "."
    return s


def build_compact_news_text(summary: str, article: Dict) -> str:
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


def build_expanded_news_text(summary: str, article: Dict) -> str:
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


# action -> (label, emoji, style, feedback recorded, ephemeral confirmation)
_FEEDBACK = {
    "gem": ("Gem", "💎", discord.ButtonStyle.success, "more", "Saved as gem — prioritizing similar stories."),
    "notcrit": ("Not critical", "📋", discord.ButtonStyle.secondary, "not_critical", "Noted — I will de-prioritize similar updates."),
    "crit": ("Critical", "🚨", discord.ButtonStyle.danger, "critical", "Marked critical — similar updates will be prioritized."),
}


def _topic_for(article_hash: str) -> str:
    return delivered_article(article_hash).get("topic") or seen_article_meta(article_hash).get("topic") or ""


class NewsButton(discord.ui.DynamicItem[discord.ui.Button], template=r"news:(?P<action>expand|slop|gem|notcrit|crit|del):(?P<h>[0-9a-f]*)"):
    def __init__(self, action: str, article_hash: str = ""):
        if action in _FEEDBACK:
            label, emoji, style = _FEEDBACK[action][:3]
        elif action == "expand":
            label, emoji, style = "Expand", "🔎", discord.ButtonStyle.primary
        elif action == "slop":
            label, emoji, style = "Slop", "🗑️", discord.ButtonStyle.secondary
        else:
            label, emoji, style = None, "❌", discord.ButtonStyle.danger
        super().__init__(
            discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"news:{action}:{article_hash}")
        )
        self.action = action
        self.article_hash = article_hash

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match):
        return cls(match["action"], match["h"])

    async def callback(self, interaction: discord.Interaction):
        h = self.article_hash
        if self.action == "del":
            # Deleting does not affect personalization/curation signals.
            await interaction.response.defer()
            await interaction.message.delete()
            return
        if self.action == "expand":
            expanded = delivered_article(h).get("expanded")
            if not expanded:
                await interaction.response.send_message("This story is too old to expand.", ephemeral=True)
                return
            await interaction.response.edit_message(content=expanded, view=expanded_view(h), suppress_embeds=False)
            return
        topic = _topic_for(h)
        if self.action == "slop":
            record_feedback(interaction.user.id, h, "slop", topic)
            content = (interaction.message.content if interaction.message else "") or ""
            await interaction.response.edit_message(content=f"~~{content}~~\n\n**SLOP**"[:2000], view=None)
            return
        _, _, _, feedback, reply = _FEEDBACK[self.action]
        record_feedback(interaction.user.id, h, feedback, topic)
        await interaction.response.send_message(reply, ephemeral=True)


class NewsSourceDisableButton(discord.ui.DynamicItem[discord.ui.Button], template=r"news:srcoff:(?P<sid>[0-9a-f]+)"):
    def __init__(self, source_id: str):
        super().__init__(
            discord.ui.Button(
                label="Disable source", emoji="🚫", style=discord.ButtonStyle.danger, custom_id=f"news:srcoff:{source_id}"
            )
        )
        self.source_id = source_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match):
        return cls(match["sid"])

    async def callback(self, interaction: discord.Interaction):
        info = remembered_source(self.source_id)
        topic, source_name = info.get("topic"), info.get("source")
        if not topic or not source_name:
            await interaction.response.send_message("This notice is too old; use `/news-source` instead.", ephemeral=True)
            return
        disable_source_for_user(interaction.user.id, topic, source_name)
        await interaction.response.edit_message(
            content=f"🚫 Source disabled for `{topic}`: **{source_name}**", view=None
        )


DYNAMIC_ITEMS = (NewsButton, NewsSourceDisableButton)


def _view(items: List[discord.ui.Item]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for it in items:
        view.add_item(it)
    return view


def compact_view(article_hash: str) -> discord.ui.View:
    return _view([NewsButton("expand", article_hash), NewsButton("slop", article_hash), NewsButton("del")])


def expanded_view(article_hash: str) -> discord.ui.View:
    return _view([NewsButton(a, article_hash) for a in ("gem", "notcrit", "crit")] + [NewsButton("del")])


def source_issue_view(topic: str, source_name: str) -> discord.ui.View:
    source_id = hashlib.sha256(f"{topic}|{source_name}".lower().encode()).hexdigest()[:12]
    remember_source(source_id, topic, source_name)
    return _view([NewsSourceDisableButton(source_id)])


def build_digest_embed(articles: List[Dict], summary: str) -> discord.Embed:
    from datetime import datetime, timezone

    embed = discord.Embed(
        title="📬 News Briefing — Quiet hours summary",
        description=summary[:4096],
        color=0xF39C12,
        timestamp=datetime.now(timezone.utc),
    )
    sources = {a.get("source", "") for a in articles[:15]}
    embed.add_field(name="📎 Sources", value=", ".join(s for s in sources if s)[:1024] or "Various", inline=False)
    links = [f"• [{a.get('title', 'Link')[:60]}]({a.get('link', '')})" for a in articles[:10]]
    if links:
        embed.add_field(name="🔗 Links", value="\n".join(links)[:1024], inline=False)
    embed.set_footer(text=f"{len(articles)} articles during quiet hours")
    return embed
