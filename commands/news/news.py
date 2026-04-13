"""Subscribe to news topics delivered via DM."""

from typing import List, Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from services.news_service import (
    subscribe_user,
    unsubscribe_user,
    get_user_topics,
    TOPIC_FEEDS,
    CATEGORY_EMOJIS,
)

TOPIC_SUGGESTIONS = {
    "ai": "models, regulation, and practical adoption",
    "tech": "product launches and platform updates",
    "science": "research and major discoveries",
    "trade": "markets, policy, and business shifts",
    "global politics": "geopolitical developments",
    "europe": "regional policy and economy",
    "us": "national policy and industry news",
    "finland": "local policy and business updates",
    "crypto": "digital asset markets and regulation",
    "gaming": "industry releases and platform changes",
    "apple": "Apple devices, strategy, and ecosystem changes",
    "ios": "iPhone software releases and app ecosystem news",
    "macos": "Mac platform updates, tools, and performance changes",
    "valve": "Steam, platform policy, and Valve product updates",
    "hltv": "Counter-Strike scene news, rankings, and tournament results",
    "esports": "major tournaments, teams, and league updates",
    "startups": "funding rounds and product momentum",
    "cybersecurity": "threats, vulnerabilities, and incident response",
}

RELATED_TOPICS = {
    "ai": ["tech", "science", "startups"],
    "tech": ["ai", "apple", "cybersecurity"],
    "apple": ["ios", "macos", "tech"],
    "ios": ["apple", "macos", "tech"],
    "macos": ["apple", "ios", "tech"],
    "gaming": ["valve", "hltv", "esports"],
    "valve": ["gaming", "hltv", "esports"],
    "hltv": ["esports", "valve", "gaming"],
    "esports": ["hltv", "gaming", "valve"],
    "trade": ["global politics", "us", "europe"],
    "global politics": ["trade", "europe", "us"],
    "crypto": ["trade", "tech", "cybersecurity"],
}


def _build_topics_to_follow(limit: int = 8) -> str:
    topics = sorted(TOPIC_FEEDS.keys())[:limit]
    lines = []
    for topic in topics:
        note = TOPIC_SUGGESTIONS.get(topic, "relevant developments")
        lines.append(f"• `{topic}` - {note}")
    return "\n".join(lines)


def _recommend_topics_from_inputs(current_topics: List[str], newly_added_topics: List[str], limit: int = 6) -> str:
    current_set = {t.lower() for t in current_topics}
    candidates = []

    for topic in newly_added_topics:
        key = topic.lower().strip()
        for related in RELATED_TOPICS.get(key, []):
            if related in TOPIC_FEEDS and related not in current_set and related not in candidates:
                candidates.append(related)

    if len(candidates) < limit:
        for fallback in sorted(TOPIC_FEEDS.keys()):
            if fallback not in current_set and fallback not in candidates:
                candidates.append(fallback)
            if len(candidates) >= limit:
                break

    lines = []
    for topic in candidates[:limit]:
        note = TOPIC_SUGGESTIONS.get(topic, "relevant developments")
        lines.append(f"• `{topic}` - {note}")
    return "\n".join(lines) if lines else "• No additional recommendations right now."


def register(client: discord.Client):
    @client.tree.command(
        name="news",
        description="Subscribe to news topics delivered to your DMs (comma-separated topics)",
    )
    @app_commands.describe(
        topics="Topics to subscribe to (e.g. 'tech, AI, finland') — leave blank to see your subscriptions",
        action="subscribe (default) or unsubscribe",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="subscribe", value="subscribe"),
        app_commands.Choice(name="unsubscribe", value="unsubscribe"),
        app_commands.Choice(name="unsubscribe all", value="unsubscribe_all"),
    ])
    async def news(
        interaction: discord.Interaction,
        topics: Optional[str] = None,
        action: Optional[app_commands.Choice[str]] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        action_val = action.value if action else "subscribe"

        # No topics → show current subscriptions
        if not topics and action_val == "subscribe":
            current = get_user_topics(interaction.user.id)
            if not current:
                embed = discord.Embed(
                    title="📰 News Preferences",
                    description="You are not following any topics yet.",
                    color=0x5865F2,
                )
                embed.add_field(
                    name="Topics you can follow",
                    value=_build_topics_to_follow(),
                    inline=False,
                )
                embed.add_field(
                    name="How to start",
                    value="Use `/news topics:ai, tech, finland` to subscribe.\n"
                          "You can also add custom topics and I will match them to relevant sources.",
                    inline=False,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            topic_lines = []
            for t in current:
                emoji = CATEGORY_EMOJIS.get(t.lower(), "📰")
                topic_lines.append(f"{emoji} **{t.capitalize()}**")

            embed = discord.Embed(
                title="📰 Your Followed Topics",
                description="\n".join(topic_lines),
                color=0x2ECC71,
            )
            embed.add_field(
                name="Manage subscriptions",
                value="`/news topics:tech action:unsubscribe` to remove one topic\n"
                      "`/news action:unsubscribe all` to clear all topics",
                inline=False,
            )
            embed.add_field(
                name="Possible topics to follow",
                value=_build_topics_to_follow(limit=6),
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Unsubscribe all
        if action_val == "unsubscribe_all":
            remaining = unsubscribe_user(interaction.user.id)
            await interaction.response.send_message(
                "✅ Unsubscribed from all news topics. You won't receive any more news DMs.",
                ephemeral=True,
            )
            return

        if not topics:
            await interaction.response.send_message(
                "Please provide topics to unsubscribe from, or use `unsubscribe all`.",
                ephemeral=True,
            )
            return

        topic_list = [t.strip() for t in topics.split(",") if t.strip()]
        if not topic_list:
            await interaction.response.send_message("Please provide at least one topic.", ephemeral=True)
            return

        if action_val == "unsubscribe":
            remaining = unsubscribe_user(interaction.user.id, topic_list)
            removed = ", ".join(f"`{t}`" for t in topic_list)
            embed = discord.Embed(
                title="📰 Unsubscribed",
                description=f"Removed: {removed}",
                color=0xE74C3C,
            )
            if remaining:
                embed.add_field(
                    name="Remaining subscriptions",
                    value=", ".join(f"`{t}`" for t in remaining),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Status",
                    value="No remaining subscriptions.",
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Subscribe
        all_topics = subscribe_user(interaction.user.id, topic_list)
        new_topics = ", ".join(f"`{t}`" for t in topic_list)

        embed = discord.Embed(
            title="📰 News Subscriptions Updated",
            description=f"Added topics: {new_topics}",
            color=0x2ECC71,
        )

        topic_lines = []
        for t in all_topics:
            emoji = CATEGORY_EMOJIS.get(t.lower(), "📰")
            has_feeds = t.lower() in TOPIC_FEEDS
            status = "✅ Known topic" if has_feeds else "🔍 Custom topic (general feeds)"
            topic_lines.append(f"{emoji} **{t.capitalize()}** — {status}")

        embed.add_field(
            name="Your current topics",
            value="\n".join(topic_lines) if topic_lines else "None",
            inline=False,
        )
        embed.add_field(
            name="How delivery works",
            value="• You receive DMs when new articles are found\n"
                  "• Use feedback buttons to tune relevance\n"
                  "• Set quiet hours with `/news-time`\n"
                  "• Remove topics anytime with `/news` and `action:unsubscribe`",
            inline=False,
        )
        embed.add_field(
            name="Possible topics to follow",
            value=_recommend_topics_from_inputs(all_topics, topic_list, limit=6),
            inline=False,
        )
        embed.set_footer(text="Feeds are checked about every 10 minutes")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Send a test DM to confirm DMs work
        try:
            user = await client.fetch_user(interaction.user.id)
            test_embed = discord.Embed(
                title="📰 News Subscription Active",
                description=f"Now following: {', '.join(f'**{t}**' for t in topic_list)}\n\n"
                            "News updates will be delivered here. Keep your DMs enabled to receive them.",
                color=0x2ECC71,
            )
            test_embed.add_field(
                name="Possible topics to follow",
                value=_recommend_topics_from_inputs(all_topics, topic_list, limit=5),
                inline=False,
            )
            await user.send(embed=test_embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I couldn't send you a DM. Please enable DMs from server members in your privacy settings.",
                ephemeral=True,
            )
        except Exception:
            pass
