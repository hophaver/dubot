import discord
from discord import app_commands

from whitelist import is_admin
from commands.shared import bot_embed_thumbnail_url
from utils import home_log
from utils import reliability_telemetry


def _build_reliability_embed(client: discord.Client, title: str) -> discord.Embed:
    data = reliability_telemetry.snapshot()
    embed = discord.Embed(
        title=title,
        description="Telemetry for retries, timeouts, and failures.",
        color=discord.Color.dark_gold(),
    )
    _thumb = bot_embed_thumbnail_url(client.user)
    if _thumb:
        embed.set_thumbnail(url=_thumb)
    embed.add_field(name="LLM retries", value=str(data.get("llm_retries", 0)), inline=True)
    embed.add_field(name="LLM timeouts", value=str(data.get("llm_timeouts", 0)), inline=True)
    embed.add_field(name="LLM errors", value=str(data.get("llm_errors", 0)), inline=True)
    embed.add_field(name="Discord send retries", value=str(data.get("discord_send_retries", 0)), inline=True)
    embed.add_field(name="Discord send errors", value=str(data.get("discord_send_errors", 0)), inline=True)
    embed.add_field(name="Message handler errors", value=str(data.get("message_handler_errors", 0)), inline=True)
    embed.set_footer(text="Use /reliability action:reset to clear counters")
    return embed


def register(client):
    @client.tree.command(name="reliability", description="View or reset reliability telemetry counters (admin)")
    @app_commands.describe(
        action="Choose whether to view or reset counters",
        post_to_home="Also post the current counters to the home channel",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="view", value="view"),
            app_commands.Choice(name="reset", value="reset"),
        ]
    )
    async def reliability(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        post_to_home: bool = False,
    ):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        if action.value == "reset":
            reliability_telemetry.reset()
            embed = _build_reliability_embed(client, "🧹 Reliability Counters Reset")
        else:
            embed = _build_reliability_embed(client, "📈 Reliability Counters")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        if post_to_home:
            text = reliability_telemetry.format_snapshot("📈 Reliability snapshot")
            await home_log.send_to_home(text)
