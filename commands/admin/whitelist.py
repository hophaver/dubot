from typing import List, Tuple, Optional
import discord
from discord.ui import View, Select, Button, Modal, TextInput
from whitelist import is_admin, remove_user_from_whitelist, set_user_role, load_whitelist


async def build_whitelist_embed_and_view(client: discord.Client) -> Tuple[discord.Embed, "WhitelistView"]:
    data = load_whitelist()
    role_config = [("admin", "👑 Admin"), ("himas", "🏠 Himas"), ("user", "👤 User")]
    all_ids = list(dict.fromkeys(uid for key, _ in role_config for uid in data.get(key, []) or []))
    names = {}
    for uid in all_ids[:50]:
        try:
            u = await client.fetch_user(int(uid))
            names[uid] = u.display_name or str(uid)
        except Exception:
            names[uid] = f"ID {uid}"
    user_list = [(int(uid), names.get(uid, f"ID {uid}"), key) for key, _ in role_config for uid in data.get(key, []) or []]
    embed = discord.Embed(
        title="Whitelist",
        description="Manage roles. Select a user and role, then **Assign** or **Remove**.",
        color=discord.Color.dark_blue(),
    )
    embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
    for key, label in role_config:
        ids = data.get(key, []) or []
        if not ids:
            embed.add_field(name=f"{label} — 0", value="*No members*", inline=False)
        else:
            value = "\n".join(f"• {names.get(uid, str(uid))}" for uid in ids[:20])
            if len(ids) > 20:
                value += f"\n*… and {len(ids) - 20} more*"
            embed.add_field(name=f"{label} — {len(ids)}", value=value, inline=False)
    embed.set_footer(text="Assign: set role for selected user · Remove: take off whitelist · Add: by User ID")
    return embed, WhitelistView(client, user_list)


class AddUserModal(Modal, title="Add user"):
    uid_input = TextInput(label="User ID", placeholder="Discord user ID (numeric)", required=True, max_length=20)
    role_input = TextInput(label="Role", placeholder="user, himas, or admin", required=True, max_length=10)

    def __init__(self, client: discord.Client, message: discord.Message):
        super().__init__()
        self.client = client
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        try:
            uid = int(self.uid_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ User ID must be a number.", ephemeral=True)
            return
        role = self.role_input.value.strip().lower()
        if role not in ("user", "himas", "admin"):
            await interaction.response.send_message("❌ Role must be: user, himas, or admin.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if set_user_role(uid, role):
            embed, view = await build_whitelist_embed_and_view(self.client)
            view.message = self.message
            await self.message.edit(embed=embed, view=view)
            await interaction.followup.send(f"✅ Added <@{uid}> as **{role}**.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not add (invalid ID or protected user).", ephemeral=True)


class WhitelistView(View):
    def __init__(self, client: discord.Client, user_list: List[Tuple[int, str, str]], timeout: float = 120):
        super().__init__(timeout=timeout)
        self.client = client
        self.message: Optional[discord.Message] = None
        self.user_list = user_list
        self.selected_uid: Optional[int] = None
        self.selected_role: Optional[str] = None
        options = [discord.SelectOption(label=(name[:80] or f"ID {uid}"), value=str(uid), description=role) for uid, name, role in user_list[:25]]
        if not options:
            options = [discord.SelectOption(label="(No users — use Add user)", value="0")]
        sel = Select(placeholder="Select user", options=options)
        sel.callback = self._on_user_select
        self.add_item(sel)
        role_sel = Select(placeholder="Role", options=[discord.SelectOption(label="User", value="user"), discord.SelectOption(label="Himas", value="himas"), discord.SelectOption(label="Admin", value="admin")])
        role_sel.callback = self._on_role_select
        self.add_item(role_sel)
        assign_btn = Button(label="Assign role", style=discord.ButtonStyle.primary, custom_id="wl_assign")
        assign_btn.callback = self._on_assign
        self.add_item(assign_btn)
        remove_btn = Button(label="Remove", style=discord.ButtonStyle.danger, custom_id="wl_remove")
        remove_btn.callback = self._on_remove
        self.add_item(remove_btn)
        add_btn = Button(label="Add user", style=discord.ButtonStyle.success, custom_id="wl_add")
        add_btn.callback = self._on_add
        self.add_item(add_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return False
        return True

    async def _on_user_select(self, interaction: discord.Interaction):
        val = interaction.data.get("values", ["0"])[0]
        self.selected_uid = int(val) if val != "0" else None
        await interaction.response.defer_update()

    async def _on_role_select(self, interaction: discord.Interaction):
        self.selected_role = interaction.data.get("values", [None])[0]
        await interaction.response.defer_update()

    async def _on_assign(self, interaction: discord.Interaction):
        if self.selected_uid is None or self.selected_uid == 0 or self.selected_role is None:
            await interaction.response.send_message("❌ Select a user and a role first.", ephemeral=True)
            return
        if set_user_role(self.selected_uid, self.selected_role):
            await interaction.response.defer()
            embed, view = await build_whitelist_embed_and_view(self.client)
            view.message = self.message
            await self.message.edit(embed=embed, view=view)
            await interaction.followup.send(f"✅ <@{self.selected_uid}> set to **{self.selected_role}**.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not update (protected user).", ephemeral=True)

    async def _on_remove(self, interaction: discord.Interaction):
        if self.selected_uid is None or self.selected_uid == 0:
            await interaction.response.send_message("❌ Select a user first.", ephemeral=True)
            return
        if remove_user_from_whitelist(self.selected_uid):
            await interaction.response.defer()
            embed, view = await build_whitelist_embed_and_view(self.client)
            view.message = self.message
            await self.message.edit(embed=embed, view=view)
            await interaction.followup.send(f"✅ <@{self.selected_uid}> removed from whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Cannot remove that user.", ephemeral=True)

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddUserModal(self.client, interaction.message))


def register(client: discord.Client):
    @client.tree.command(name="whitelist", description="View and manage whitelist (roles and users)")
    async def whitelist(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer()
        embed, view = await build_whitelist_embed_and_view(client)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg
