import io
import mimetypes

import discord
from discord import app_commands

from models import model_manager
from utils.llm_service import commentary_for_generated_image
from utils.openrouter_image import generate_openrouter_image_with_fallback
from whitelist import get_user_permission


def _ext_for_mime(mime: str) -> str:
    m = (mime or "image/png").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(m) or ".png"
    if ext == ".jpe":
        ext = ".jpg"
    return ext


def register(client: discord.Client):
    @client.tree.command(
        name="imagine",
        description="Generate an image (OpenRouter; set model in /llm-settings → Image generation)",
    )
    @app_commands.describe(idea="What to generate")
    async def imagine(interaction: discord.Interaction, idea: str):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        eff = model_manager.get_effective_model_for_function(interaction.user.id, "image_generation")
        model_name = str(eff.get("model") or "").strip()
        if not model_name:
            await interaction.response.send_message(
                "No **image generation** model set. An admin can run **`/pull-model`** → type **image generation (OpenRouter)** "
                "with your model id, then you (or they) pick it under **`/llm-settings`** → **Image generation**.",
                ephemeral=True,
            )
            return

        p = (idea or "").strip()
        if not p:
            await interaction.response.send_message("Send a non-empty **prompt**.", ephemeral=True)
            return

        await interaction.response.defer()
        img_bytes, mime, api_text, err = await generate_openrouter_image_with_fallback(model_name, p)
        if err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return
        if not img_bytes:
            await interaction.followup.send(
                "❌ Image generation returned no image. Try another model or a clearer prompt.",
                ephemeral=True,
            )
            return

        ext = _ext_for_mime(mime)
        filename = f"imagine{ext}"
        file = discord.File(io.BytesIO(img_bytes), filename=filename)

        caption = (api_text or "").strip()
        if len(caption) > 1900:
            caption = caption[:1890] + "…"

        note = await commentary_for_generated_image(
            interaction.user.id,
            p,
            f"OpenRouter `{model_name}`",
            img_bytes,
            mime,
        )
        parts = []
        if caption:
            parts.append(caption)
        if note:
            if parts:
                parts.append("")
            parts.append(note)
        content = "\n".join(parts).strip() or None

        await interaction.followup.send(content=content, file=file)
