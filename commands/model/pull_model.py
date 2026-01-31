import json
import discord
from discord import app_commands
from whitelist import is_admin
from integrations import OLLAMA_URL


def register(client: discord.Client):
    @client.tree.command(name="pull-model", description="Download an Ollama model")
    @app_commands.describe(model="Model name to pull (e.g. llama3.2:3b)")
    async def pull_model(interaction: discord.Interaction, model: str):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            import requests
            url = f"{OLLAMA_URL}/api/pull"
            response = requests.post(url, json={"name": model}, stream=True, timeout=300)
            if response.status_code == 200:
                msg = f"⏳ Pulling `{model}`...\n"
                await interaction.followup.send(msg)
                for line in response.iter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "status" in data:
                                msg += f"{data['status']}\n"
                                if len(msg.split("\n")) % 5 == 0:
                                    await interaction.edit_original_response(content=msg[:1900])
                        except json.JSONDecodeError:
                            pass
                msg += "✅ Done."
                await interaction.edit_original_response(content=msg[:1900])
            else:
                await interaction.followup.send(f"❌ Pull failed: HTTP {response.status_code}")
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:150]}")
