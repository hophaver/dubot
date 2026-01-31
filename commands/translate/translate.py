import discord
from discord import app_commands
from whitelist import get_user_permission
from utils.llm_service import _try_models_with_fallback
from models import model_manager


async def do_translate(user_id: int, text: str, target_language: str = "English") -> str:
    model_info = model_manager.get_user_model_info(user_id)
    model_name = model_info.get("model", "llama3.2:3b")
    system = "You are a translator. Translate the user's message into the requested language. Output only the translation, no explanations or quotes. If the text is already in that language, return it unchanged or lightly normalized."
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"Translate the following into {target_language}:\n\n{text}"}]
    try:
        _, out = await _try_models_with_fallback(model_name, messages, images=False)
        return (out or "").strip()
    except Exception:
        return ""


def register(client: discord.Client):
    @client.tree.command(name="translate", description="Translate text into the requested language")
    @app_commands.describe(text="Text to translate", language="Target language (default: English)")
    async def translate(interaction: discord.Interaction, text: str, language: str = "English"):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        result = await do_translate(interaction.user.id, text, language)
        if not result:
            await interaction.followup.send("❌ Translation failed. Try again or check your model.")
            return
        if len(result) > 1900:
            chunks = [result[i : i + 1900] for i in range(0, len(result), 1900)]
            await interaction.followup.send(chunks[0])
            for c in chunks[1:]:
                await interaction.channel.send(c)
        else:
            await interaction.followup.send(result)
