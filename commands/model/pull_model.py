import asyncio
import json
import time
import aiohttp
import discord
from discord import app_commands
from commands.shared import require_admin
from models import model_manager
from utils.http import aio_session
from utils.ollama import base_url as ollama_base_url
from utils.llm_service import validate_and_set_model, validate_and_set_image_generation_model


PULL_TIMEOUT_SECONDS = 1800
PROGRESS_EDIT_INTERVAL = 2.0  # seconds between progress edits (Discord rate limits)


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
    @client.tree.command(
        name="pull-model",
        description="Install local model, validate cloud chat model, or validate OpenRouter image model",
    )
    @app_commands.describe(
        type="Model type: local pull, cloud chat validate, or OpenRouter image-generation validate",
        model_name="Model name (e.g. llama3.2:3b, llava, openai/gpt-4o-mini, google/gemini-2.5-flash-image)",
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="local (Ollama)", value="local"),
        app_commands.Choice(name="cloud (OpenRouter)", value="cloud"),
        app_commands.Choice(name="image generation (OpenRouter)", value="image_generation"),
    ])
    async def pull_model(
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        model_name: str,
    ):
        if not await require_admin(interaction):
            return
        await interaction.response.defer()
        try:
            model = model_name.strip()
            if type.value == "image_generation":
                success, msg = await validate_and_set_image_generation_model(interaction.user.id, model)
                if success:
                    await interaction.edit_original_response(
                        content=(
                            f"✅ {msg}\n"
                            "Users can generate with **`/imagine`**. Per-user override: **`/llm-settings`** → **Image generation**."
                        )
                    )
                else:
                    await interaction.edit_original_response(content=f"❌ {msg}")
                return
            if type.value == "cloud":
                success, msg = await validate_and_set_model(interaction.user.id, "cloud", model)
                if success:
                    local_runtime = model_manager.get_last_local_model(interaction.user.id, refresh_local=True)
                    await interaction.edit_original_response(
                        content=(
                            f"✅ {msg}\n"
                            "Cloud models do not need downloading. Access was validated and the active chat model was updated.\n"
                            f"Basic interactions still run on local Ollama model: `{local_runtime}`."
                        )
                    )
                else:
                    await interaction.edit_original_response(content=f"❌ {msg}")
                return

            # type.value == "local": stream Ollama's pull progress without blocking the loop.
            lines = [f"⏳ **Pulling `{model}`**\n"]
            await interaction.edit_original_response(content=lines[0])
            last_edit = time.monotonic()
            timeout = aiohttp.ClientTimeout(total=PULL_TIMEOUT_SECONDS)
            async with aio_session().post(f"{ollama_base_url()}/api/pull", json={"name": model}, timeout=timeout) as response:
                if response.status != 200:
                    await interaction.edit_original_response(content=f"❌ Pull failed: HTTP {response.status}")
                    return
                async for raw in response.content:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "status" in data:
                        lines.append(_status_line(data))
                    if "error" in data:
                        lines.append(f"❌ {data['error']}")
                        break
                    if time.monotonic() - last_edit >= PROGRESS_EDIT_INTERVAL:
                        try:
                            await interaction.edit_original_response(content="\n".join(lines[-15:])[-1900:])
                        except discord.NotFound:
                            pass
                        last_edit = time.monotonic()
            final = "\n".join(lines[-20:])[-1900:]
            from utils.llm_service import clear_vision_model_cache
            clear_vision_model_cache()
            await model_manager.refresh_async()
            if "error" in final.lower():
                await interaction.edit_original_response(content=final)
            else:
                await interaction.edit_original_response(
                    content=final.rstrip() + "\n\n✅ **Done.** Use **/llm-settings** to pick this model for chat or another function."
                )
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Pull timed out. Try again or check Ollama.")
