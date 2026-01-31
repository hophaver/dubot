from typing import Optional, Dict, List
import discord
from discord import app_commands
from discord.ui import View, Select, Button
from whitelist import get_user_permission, is_admin
from personas import persona_manager


def _truncate_prompt(prompt: str, max_len: int = 1024) -> str:
    return prompt if len(prompt) <= max_len else prompt[: max_len - 3].rstrip() + "…"


def _persona_embed(current: str, selected: Optional[str], prompts: Dict[str, str], thumbnail_url: Optional[str]) -> discord.Embed:
    embed = discord.Embed(title="🎭 Persona", color=discord.Color.purple())
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    embed.add_field(name="Current", value=f"**{current}**", inline=True)
    if selected:
        embed.add_field(name="Selected (confirm to set)", value=f"**{selected}**", inline=True)
        if prompts.get(selected):
            embed.add_field(name="Prompt", value=_truncate_prompt(prompts[selected]), inline=False)
    embed.set_footer(text="Select persona → Confirm (admin) to set · Remove (admin) to delete")
    return embed


class PersonaSelectView(View):
    def __init__(self, client: discord.Client, user_id: int, current_persona: str, persona_names: List[str],
                 persona_prompts: Dict[str, str], timeout: float = 120):
        super().__init__(timeout=timeout)
        self.client = client
        self.user_id = user_id
        self.current_persona = current_persona
        self.persona_names = persona_names or []
        self.persona_prompts = persona_prompts or {}
        self.selected: Optional[str] = None
        options = [discord.SelectOption(label=name[:100], value=name, description="Current" if name == current_persona else None) for name in self.persona_names[:25]]
        if options:
            sel = Select(placeholder="Select a persona", options=options)
            sel.callback = self._on_select
            self.add_item(sel)
        confirm = Button(label="Confirm", style=discord.ButtonStyle.success, custom_id="persona_confirm")
        confirm.callback = self._on_confirm
        self.add_item(confirm)
        remove_btn = Button(label="Remove persona", style=discord.ButtonStyle.danger, custom_id="persona_remove")
        remove_btn.callback = self._on_remove
        self.add_item(remove_btn)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        self.selected = (interaction.data.get("values") or [None])[0]
        await interaction.response.edit_message(
            embed=_persona_embed(self.current_persona, self.selected, self.persona_prompts,
                self.client.user.display_avatar.url if self.client.user else None),
            view=self,
        )

    async def _on_confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        target = self.selected or self.current_persona
        if persona_manager.persona_exists(target):
            persona_manager.set_user_persona(interaction.user.id, target)
            self.current_persona = target
        await interaction.response.edit_message(
            content=f"✅ Persona set to **{target}**.",
            embed=_persona_embed(self.current_persona, None, self.persona_prompts,
                self.client.user.display_avatar.url if self.client.user else None),
            view=self,
        )

    async def _on_remove(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        target = self.selected or self.current_persona
        if target in ["default", "assistant", "helper"]:
            await interaction.response.send_message(f"❌ Cannot delete default persona '{target}'.", ephemeral=True)
            return
        if not persona_manager.persona_exists(target):
            await interaction.response.send_message(f"❌ Persona '{target}' not found.", ephemeral=True)
            return
        persona_manager.delete_persona(target)
        self.persona_names = persona_manager.list_personas()
        self.persona_prompts = {n: persona_manager.get_persona(n) for n in self.persona_names}
        self.current_persona = persona_manager.get_user_persona(interaction.user.id)
        self.selected = None
        await interaction.response.edit_message(
            content=f"✅ Persona **{target}** removed.",
            embed=_persona_embed(self.current_persona, None, self.persona_prompts,
                self.client.user.display_avatar.url if self.client.user else None),
            view=PersonaSelectView(self.client, self.user_id, self.current_persona, self.persona_names, self.persona_prompts, timeout=self.timeout),
        )


def register(client: discord.Client):
    @client.tree.command(name="persona", description="View and switch AI persona (Confirm/Remove: admin only)")
    async def persona(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        names = persona_manager.list_personas()
        if not names:
            await interaction.response.send_message("📝 No personas available. Use `/persona-create` to create one.")
            return
        current = persona_manager.get_user_persona(interaction.user.id)
        persona_prompts = {n: persona_manager.get_persona(n) for n in names}
        embed = _persona_embed(current, None, persona_prompts, interaction.client.user.display_avatar.url if interaction.client.user else None)
        view = PersonaSelectView(interaction.client, interaction.user.id, current, names, persona_prompts)
        await interaction.response.send_message(embed=embed, view=view)
