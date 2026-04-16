import discord
from discord import app_commands
from whitelist import get_user_permission
from llm_function_prefs import get_function_persona_name
from models import model_manager
from personas import persona_manager
from utils.llm_service import _try_models_with_fallback
from commands.shared import send_long_message


async def do_translate(user_id: int, text: str, target_language: str = "English") -> str:
    eff = model_manager.get_effective_model_for_function(user_id, "translate")
    model_name = eff.get("model") or model_manager.get_last_local_model(user_id, refresh_local=True)
    provider = eff.get("provider", "local")
    persona_key = get_function_persona_name("translate")
    base = persona_manager.get_persona(persona_key)
    system = (
        f"{base}\n\n"
        "You are a translator. Translate the user's message into the requested language. "
        "Output only the translation, no explanations or quotes. "
        "If the text is already in that language, return it unchanged or lightly normalized."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"Translate the following into {target_language}:\n\n{text}"}]
    try:
        _, out = await _try_models_with_fallback(model_name, messages, images=False, provider=provider)
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
        await send_long_message(interaction, result)
