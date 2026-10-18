import discord
from discord import app_commands
from services.reminder_service import reminder_manager
from commands.shared import require_user


def register(client: discord.Client):
    @client.tree.command(name="cancel-reminder", description="Cancel a reminder by ID")
    @app_commands.describe(reminder_id="ID of the reminder to cancel")
    async def cancel_reminder(interaction: discord.Interaction, reminder_id: str):
        if not await require_user(interaction):
            return
        try:
            rem = reminder_manager.reminders.get(reminder_id)
            if rem and rem.user_id == interaction.user.id:
                if reminder_manager.remove_reminder(reminder_id):
                    await interaction.response.send_message(f"✅ Reminder `{reminder_id}` cancelled.")
                else:
                    await interaction.response.send_message("❌ Could not remove reminder.")
            else:
                await interaction.response.send_message("❌ Could not find reminder with that ID or you don't have permission.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)[:100]}")
