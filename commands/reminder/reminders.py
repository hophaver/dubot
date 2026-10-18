import discord
from services.reminder_service import reminder_manager
from commands.shared import require_user


def register(client: discord.Client):
    @client.tree.command(name="reminders", description="List your active reminders")
    async def reminders(interaction: discord.Interaction):
        if not await require_user(interaction):
            return
        user_reminders = reminder_manager.get_user_reminders(interaction.user.id)
        if not user_reminders:
            await interaction.response.send_message("📭 You have no active reminders.")
            return
        embed = discord.Embed(title="⏰ Your Active Reminders", color=discord.Color.blue())
        for reminder in user_reminders[:10]:
            time_str = f"<t:{int(reminder.trigger_time.timestamp())}:R>"
            location = "DMs" if reminder.is_dm else f"<#{reminder.channel_id}>"
            msg_preview = (reminder.message[:100] + "...") if len(reminder.message) > 100 else reminder.message
            embed.add_field(
                name=f"ID: `{reminder.id}` - {time_str}",
                value=f"**Message:** {msg_preview}\n**Location:** {location}",
                inline=False,
            )
        if len(user_reminders) > 10:
            embed.set_footer(text=f"And {len(user_reminders) - 10} more reminders...")
        await interaction.response.send_message(embed=embed)
