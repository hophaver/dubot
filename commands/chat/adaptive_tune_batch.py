import discord
from discord import app_commands

from adaptive_dm import adaptive_dm_manager
from whitelist import get_user_permission


def register(client: discord.Client):
    @client.tree.command(
        name="adaptive-tune-batch",
        description="DMs: tune adaptive from newline-separated message samples (URLs ignored per line)",
    )
    @app_commands.describe(
        messages=(
            "One sample message per line — only message text; blank lines are skipped "
            "(Discord limit 4000 characters)."
        ),
    )
    async def adaptive_tune_batch(interaction: discord.Interaction, messages: str):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ DMs only.", ephemeral=True)
            return
        label = (getattr(interaction.user, "global_name", None) or interaction.user.name or "").strip()
        adaptive_dm_manager.touch_adaptive_sync_display_name(interaction.user.id, label)
        if not adaptive_dm_manager.is_enabled(interaction.user.id):
            await interaction.response.send_message(
                "Turn **adaptive** on first (`/adaptive` → enabled: on).",
                ephemeral=True,
            )
            return

        counts = adaptive_dm_manager.apply_batch_message_tune(interaction.user.id, messages)
        applied = int(counts.get("applied", 0) or 0)
        total_msgs = int(counts.get("messages", 0) or 0)
        if total_msgs == 0:
            await interaction.response.send_message(
                "No non-empty lines — paste one message per line.",
                ephemeral=True,
            )
            return
        if applied == 0:
            await interaction.response.send_message(
                "Nothing usable after filtering (lines too short or URL-only). "
                "Add real text on each line.",
                ephemeral=True,
            )
            return
        skipped = total_msgs - applied
        extra = f" ({skipped} line(s) skipped after filtering.)" if skipped else ""
        await interaction.response.send_message(
            f"✅ Applied **{applied}** message sample(s) to your adaptive profile.{extra}",
            ephemeral=True,
        )
