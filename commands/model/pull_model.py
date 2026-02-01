import json
import requests
import discord
from discord import app_commands
from whitelist import is_admin
from integrations import OLLAMA_URL
from models import model_manager


def _status_line(data: dict) -> str:
    """Format one Ollama pull stream line for display."""
    status = data.get("status", "")
    if "completed" in data and "total" in data:
        c, t = data["completed"], data["total"]
        if t and t > 0:
            pct = int(100 * c / t)
            return f"{status}… {pct}%"
    return status


def register(client: discord.Client):
    @client.tree.command(name="pull-model", description="Download an Ollama model")
    @app_commands.describe(model="Model name to pull (e.g. llama3.2:3b, llava)")
    async def pull_model(interaction: discord.Interaction, model: str):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            url = f"{OLLAMA_URL}/api/pull"
            response = requests.post(url, json={"name": model}, stream=True, timeout=600)
            if response.status_code != 200:
                await interaction.followup.send(f"❌ Pull failed: HTTP {response.status_code}")
                return
            lines = [f"⏳ **Pulling `{model}`**\n"]
            await interaction.edit_original_response(content=lines[0])
            last_update = 0
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "status" in data:
                        lines.append(_status_line(data))
                    if "error" in data:
                        lines.append(f"❌ {data['error']}")
                        break
                except json.JSONDecodeError:
                    pass
                if len(lines) >= 2 and (len(lines) - last_update) >= 3:
                    msg = "\n".join(lines[-15:])
                    if len(msg) > 1900:
                        msg = msg[-1900:]
                    try:
                        await interaction.edit_original_response(content=msg)
                    except discord.NotFound:
                        pass
                    last_update = len(lines)
            final = "\n".join(lines[-20:])
            if len(final) > 1900:
                final = final[-1900:]
            from utils.llm_service import clear_vision_model_cache
            clear_vision_model_cache()
            model_manager.list_all_models(refresh_local=True)
            if "error" in final.lower():
                await interaction.edit_original_response(content=final)
            else:
                await interaction.edit_original_response(
                    content=final.rstrip() + "\n\n✅ **Done.** Use **/model** to switch to this model."
                )
        except requests.exceptions.Timeout:
            await interaction.followup.send("❌ Pull timed out. Try again or check Ollama.")
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
