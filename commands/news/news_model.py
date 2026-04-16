"""Admin command to change the LLM used for news summarization."""

from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ui import Button, Select, View
from whitelist import is_admin
from models import model_manager
from services.news_service import set_news_model, get_news_model, get_news_recent_cloud_models

PAGE_SIZE = 25


def _news_model_embed(
    current_provider: str,
    current_model: Optional[str],
    selected: Optional[Tuple[str, str]],
    page: int,
    total_items: int,
) -> discord.Embed:
    display_current = current_model or "default (same as chat model)"
    embed = discord.Embed(
        title="📰 News Model",
        description="Pick a model from the menu, then confirm to switch.",
        color=0x5865F2,
    )
    embed.add_field(name="Current", value=f"`{display_current}` (`{current_provider}`)", inline=False)
    if selected:
        provider, model_name = selected
        embed.add_field(name="Selected (confirm to switch)", value=f"`{model_name}` (`{provider}`)", inline=False)
    total_pages = max(1, ((total_items - 1) // PAGE_SIZE) + 1)
    embed.set_footer(text=f"Page {page + 1}/{total_pages} • Local + previously used cloud models.")
    return embed


class NewsModelSelectView(View):
    def __init__(
        self,
        user_id: int,
        current_provider: str,
        current_model: Optional[str],
        local_models: List[str],
        cloud_models: List[str],
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.current_provider = current_provider
        self.current_model = current_model
        self.local_models = sorted(local_models or [])
        self.cloud_models = list(dict.fromkeys([m for m in (cloud_models or []) if m]))
        if self.current_provider == "cloud" and self.current_model and self.current_model not in self.cloud_models:
            self.cloud_models.insert(0, self.current_model)
        self.options: List[Tuple[str, str]] = [("local", m) for m in self.local_models]
        self.options.extend([("cloud", m) for m in self.cloud_models])
        self.page = 0
        self.selected: Optional[Tuple[str, str]] = None
        self._rebuild_components()

    def _total_pages(self) -> int:
        return max(1, ((len(self.options) - 1) // PAGE_SIZE) + 1)

    def _page_slice(self) -> List[Tuple[int, Tuple[str, str]]]:
        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        return list(enumerate(self.options[start:end], start=start))

    def _current_embed(self) -> discord.Embed:
        return _news_model_embed(
            current_provider=self.current_provider,
            current_model=self.current_model,
            selected=self.selected,
            page=self.page,
            total_items=len(self.options),
        )

    def _rebuild_components(self) -> None:
        self.clear_items()
        options: List[discord.SelectOption] = []
        for idx, (provider, model_name) in self._page_slice():
            is_current = provider == self.current_provider and model_name == self.current_model
            options.append(
                discord.SelectOption(
                    label=f"[{provider}] {model_name}"[:100],
                    value=str(idx),
                    description="Current news model" if is_current else None,
                )
            )
        if options:
            sel = Select(placeholder="Select a model from this page", options=options)
            sel.callback = self._on_select
            self.add_item(sel)

        prev_btn = Button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="news_model_prev")
        prev_btn.disabled = self.page <= 0
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        next_btn = Button(label="Next", style=discord.ButtonStyle.secondary, custom_id="news_model_next")
        next_btn.disabled = self.page >= (self._total_pages() - 1)
        next_btn.callback = self._on_next
        self.add_item(next_btn)

        confirm = Button(label="Confirm switch", style=discord.ButtonStyle.success, custom_id="news_model_confirm")
        confirm.callback = self._on_confirm
        self.add_item(confirm)

    async def _refresh_message(self, interaction: discord.Interaction):
        self._rebuild_components()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        selected_value = interaction.data.get("values", [None])[0]
        if not selected_value:
            await interaction.response.defer_update()
            return
        try:
            selected_idx = int(selected_value)
        except (TypeError, ValueError):
            await interaction.response.defer_update()
            return
        if selected_idx < 0 or selected_idx >= len(self.options):
            await interaction.response.defer_update()
            return
        self.selected = self.options[selected_idx]
        await self._refresh_message(interaction)

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        await self._refresh_message(interaction)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if self.page < self._total_pages() - 1:
            self.page += 1
        await self._refresh_message(interaction)

    async def _on_confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        target_provider, target_model = self.selected or (self.current_provider, self.current_model or "")
        if not target_model:
            await interaction.response.send_message("No model selected.", ephemeral=True)
            return
        set_news_model(target_provider, target_model)
        self.current_provider = target_provider
        self.current_model = target_model
        self.cloud_models = get_news_recent_cloud_models()
        self.options = [("local", m) for m in self.local_models]
        self.options.extend([("cloud", m) for m in self.cloud_models])
        self.selected = None
        self.page = min(self.page, max(0, self._total_pages() - 1))
        self._rebuild_components()
        await interaction.response.edit_message(content=f"✅ News model set to `{target_model}` ({target_provider}).", embed=self._current_embed(), view=self)


def register(client: discord.Client):
    @client.tree.command(
        name="news-model",
        description="[Admin] Set the LLM model for news summarization",
    )
    @app_commands.describe(
        provider="Model provider: local (Ollama) or cloud",
        model_name="Model name (e.g. qwen2.5:7b, llama3.2:3b, openai/gpt-4o-mini)",
    )
    @app_commands.choices(provider=[
        app_commands.Choice(name="local (Ollama)", value="local"),
        app_commands.Choice(name="cloud", value="cloud"),
    ])
    async def news_model(
        interaction: discord.Interaction,
        provider: Optional[app_commands.Choice[str]] = None,
        model_name: Optional[str] = None,
    ):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        if provider:
            if not model_name or not model_name.strip():
                await interaction.response.send_message("Usage: `/news-model local|cloud model_name`", ephemeral=True)
                return
            set_news_model(provider.value, model_name.strip())
            embed = discord.Embed(
                title="📰 News Model Updated",
                color=0x2ECC71,
            )
            embed.add_field(name="Provider", value=provider.name, inline=True)
            embed.add_field(name="Model", value=f"`{model_name.strip()}`", inline=True)
            embed.set_footer(text="This model will be used for all news summarization")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        current_provider, current_model = get_news_model()
        local_models = model_manager.list_all_models(refresh_local=True)
        cloud_models = get_news_recent_cloud_models()
        if not local_models and not cloud_models:
            await interaction.response.send_message(
                "No models available in menu yet. Install a local model with `/pull-model` → type **local (Ollama)** "
                "or set a cloud model directly with `/news-model cloud model_name`.",
                ephemeral=True,
            )
            return

        view = NewsModelSelectView(
            user_id=interaction.user.id,
            current_provider=current_provider,
            current_model=current_model,
            local_models=local_models,
            cloud_models=cloud_models,
        )
        await interaction.response.send_message(
            embed=_news_model_embed(
                current_provider=current_provider,
                current_model=current_model,
                selected=None,
                page=0,
                total_items=len(local_models) + len(cloud_models),
            ),
            view=view,
            ephemeral=True,
        )

    @client.tree.command(
        name="news-model-info",
        description="Show the current news summarization model",
    )
    async def news_model_info(interaction: discord.Interaction):
        model_type, model_name = get_news_model()
        display_model = model_name or "default (same as chat)"
        embed = discord.Embed(
            title="📰 News Model",
            color=0x5865F2,
        )
        embed.add_field(name="Provider", value=model_type, inline=True)
        embed.add_field(name="Model", value=f"`{display_model}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
