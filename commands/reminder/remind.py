from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from services.reminder_service import reminder_manager
from .parse_time import parse_time_string


def register(client: discord.Client):
    @client.tree.command(name="remind", description="Set a reminder")
    @app_commands.describe(
        time="When to remind (e.g., 'in 2 hours', 'tomorrow at 3pm')",
        message="Reminder message",
        channel="Channel to send reminder (optional)",
    )
    async def remind(
        interaction: discord.Interaction,
        time: str,
        message: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            reminder_time = parse_time_string(time)
            if not reminder_time:
                await interaction.followup.send("❌ Could not parse time. Try 'in 2 hours' or 'tomorrow at 3pm'")
                return
            target_channel = channel.id if channel else interaction.channel_id
            reminder_id = reminder_manager.add_timed_reminder(
                interaction.user.id, target_channel, message, reminder_time, is_dm=False
            )
            embed = discord.Embed(title="⏰ Reminder set", color=discord.Color.green())
            embed.add_field(name="When", value=f"<t:{int(reminder_time.timestamp())}:F> (<t:{int(reminder_time.timestamp())}:R>)", inline=False)
            embed.add_field(name="Message", value=message[:1024] or "*No message*", inline=False)
            embed.add_field(name="ID", value=f"`{reminder_id}`", inline=True)
            embed.set_footer(text="Use /cancel-reminder with this ID to cancel")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:100]}")
