"""Subscribe to news topics delivered via DM."""

from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from services.news_service import (
    subscribe_user,
    unsubscribe_user,
    get_user_topics,
    get_subscriptions,
    TOPIC_FEEDS,
    CATEGORY_EMOJIS,
)


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
                available = sorted(TOPIC_FEEDS.keys())
                avail_str = ", ".join(f"`{t}`" for t in available)
                embed = discord.Embed(
                    title="📰 News Subscriptions",
                    description="You have no active subscriptions.",
                    color=0x5865F2,
                )
                embed.add_field(
                    name="Available topics",
                    value=avail_str,
                    inline=False,
                )
                embed.add_field(
                    name="How to subscribe",
                    value="Use `/news topics:tech, AI, finland` to subscribe.\n"
                          "You can use any topic — the bot will find the best sources automatically.",
                    inline=False,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            topic_lines = []
            for t in current:
                emoji = CATEGORY_EMOJIS.get(t.lower(), "📰")
                topic_lines.append(f"{emoji} **{t.capitalize()}**")

            embed = discord.Embed(
                title="📰 Your News Subscriptions",
                description="\n".join(topic_lines),
                color=0x2ECC71,
            )
            embed.add_field(
                name="Manage",
                value="`/news topics:tech action:unsubscribe` to remove a topic\n"
                      "`/news action:unsubscribe all` to clear everything",
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
            title="📰 Subscribed to News",
            description=f"Added: {new_topics}",
            color=0x2ECC71,
        )

        topic_lines = []
        for t in all_topics:
            emoji = CATEGORY_EMOJIS.get(t.lower(), "📰")
            has_feeds = t.lower() in TOPIC_FEEDS
            status = "✅ Known topic" if has_feeds else "🔍 Custom topic (general feeds)"
            topic_lines.append(f"{emoji} **{t.capitalize()}** — {status}")

        embed.add_field(
            name="All your topics",
            value="\n".join(topic_lines) if topic_lines else "None",
            inline=False,
        )
        embed.add_field(
            name="ℹ️ How it works",
            value="• I'll DM you when new articles appear\n"
                  "• Use the buttons on each message to calibrate\n"
                  "• `/news-time` to pause notifications\n"
                  "• `/news action:unsubscribe` to remove topics",
            inline=False,
        )
        embed.set_footer(text="News is checked every ~10 minutes")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Send a test DM to confirm DMs work
        try:
            user = await client.fetch_user(interaction.user.id)
            test_embed = discord.Embed(
                title="📰 News Subscription Active",
                description=f"You're now subscribed to: {', '.join(f'**{t}**' for t in topic_list)}\n\n"
                            "I'll send you news updates here. Make sure your DMs are open!\n\n"
                            "Use the buttons on each news message to fine-tune what you receive.",
                color=0x2ECC71,
            )
            await user.send(embed=test_embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I couldn't send you a DM. Please enable DMs from server members in your privacy settings.",
                ephemeral=True,
            )
        except Exception:
            pass
