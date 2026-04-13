"""Admin command to manage custom RSS sources by topic."""

from typing import Optional

import discord
from discord import app_commands

from whitelist import is_admin
from services.news_service import (
    add_custom_topic_feed,
    get_custom_topic_feeds,
    remove_custom_topic_feed,
)


def _build_sources_embed(topic_filter: Optional[str] = None) -> discord.Embed:
    custom = get_custom_topic_feeds()
    embed = discord.Embed(
        title="🧷 Custom News Sources",
        description="Manually managed RSS sources by topic.",
        color=0x5865F2,
    )

    if not custom:
        embed.add_field(
            name="No custom sources",
            value="Use `/news-source action:add topic:<topic> url:<rss_url> source:<name>`.",
            inline=False,
        )
        return embed

    target = (topic_filter or "").strip().lower()
    topics = [target] if target else sorted(custom.keys())
    shown = 0
    for topic in topics:
        feeds = custom.get(topic, [])
        if not feeds:
            continue
        lines = [f"• **{source}** - {url}" for url, source in feeds[:10]]
        if len(feeds) > 10:
            lines.append(f"*...and {len(feeds) - 10} more*")
        embed.add_field(name=f"`{topic}` ({len(feeds)})", value="\n".join(lines)[:1024], inline=False)
        shown += 1

    if shown == 0:
        embed.add_field(name="No matches", value=f"No custom sources for `{target}`.", inline=False)
    return embed


def register(client: discord.Client):
    @client.tree.command(name="news-source", description="[Admin] Manage custom RSS sources for news topics")
    @app_commands.describe(
        action="list, add, or remove custom source",
        topic="Topic key (e.g. hltv, ai, startups)",
        url="RSS feed URL to add/remove",
        source="Display name for the source (used when adding)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="list", value="list"),
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ]
    )
    async def news_source(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        topic: Optional[str] = None,
        url: Optional[str] = None,
        source: Optional[str] = None,
    ):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        action_val = action.value
        topic_clean = (topic or "").strip().lower()
        url_clean = (url or "").strip()
        source_clean = (source or "").strip()

        if action_val == "list":
            embed = _build_sources_embed(topic_clean or None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if action_val == "add":
            if not topic_clean or not url_clean:
                await interaction.response.send_message(
                    "❌ For add: provide `topic` and `url` (and optionally `source`).",
                    ephemeral=True,
                )
                return
            ok, msg = add_custom_topic_feed(topic_clean, url_clean, source_clean or "Custom Source")
            prefix = "✅" if ok else "❌"
            await interaction.response.send_message(f"{prefix} {msg}", ephemeral=True)
            return

        # remove
        if not topic_clean or not url_clean:
            await interaction.response.send_message(
                "❌ For remove: provide both `topic` and `url`.",
                ephemeral=True,
            )
            return
        ok, msg = remove_custom_topic_feed(topic_clean, url_clean)
        prefix = "✅" if ok else "❌"
        await interaction.response.send_message(f"{prefix} {msg}", ephemeral=True)
