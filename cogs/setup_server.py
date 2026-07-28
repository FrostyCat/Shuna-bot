import os

import anthropic
import discord
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_STRUCTURE_TOOL = {
    "name": "propose_server_changes",
    "description": (
        "Propose changes to a Discord server: categories/channels/roles to delete, "
        "and categories/channels/roles to create, based on the user's request."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "delete_channel_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of existing channels or categories to delete, taken from the provided server listing. Deleting a category does not delete its channels — list them separately if they should also be deleted.",
            },
            "delete_role_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of existing roles to delete, taken from the provided server listing.",
            },
            "categories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Category name"},
                        "channels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Channel name, lowercase-with-dashes for text channels"},
                                    "type": {"type": "string", "enum": ["text", "voice"]},
                                },
                                "required": ["name", "type"],
                            },
                        },
                    },
                    "required": ["name", "channels"],
                },
            },
            "roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "color_hex": {"type": "string", "description": "Hex color like '#5865F2', or omit for default"},
                        "hoist": {"type": "boolean", "description": "Whether to display role members separately in the member list"},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["delete_channel_ids", "delete_role_ids", "categories", "roles"],
    },
}


def _hex_to_color(hex_str: str | None) -> discord.Color:
    if not hex_str:
        return discord.Color.default()
    try:
        return discord.Color(int(hex_str.lstrip("#"), 16))
    except ValueError:
        return discord.Color.default()


def _describe_guild(guild: discord.Guild) -> str:
    lines = ["Existing categories and channels (id: name):"]
    for category in guild.categories:
        lines.append(f"　📁 {category.id}: {category.name}")
        for ch in category.channels:
            icon = "🔊" if isinstance(ch, discord.VoiceChannel) else "#"
            lines.append(f"　　{icon} {ch.id}: {ch.name}")
    uncategorized = [ch for ch in guild.channels if ch.category is None and not isinstance(ch, discord.CategoryChannel)]
    if uncategorized:
        lines.append("　(no category)")
        for ch in uncategorized:
            icon = "🔊" if isinstance(ch, discord.VoiceChannel) else "#"
            lines.append(f"　　{icon} {ch.id}: {ch.name}")

    lines.append("\nExisting roles (id: name):")
    for role in guild.roles:
        if role.is_default():
            continue
        lines.append(f"　🎭 {role.id}: {role.name}")

    return "\n".join(lines)


def _summarize_changes(guild: discord.Guild, structure: dict) -> str:
    lines = []

    delete_channel_ids = set(structure.get("delete_channel_ids", []))
    delete_role_ids = set(structure.get("delete_role_ids", []))
    if delete_channel_ids or delete_role_ids:
        lines.append("**🗑️ To delete**")
        for cid in delete_channel_ids:
            obj = guild.get_channel(int(cid)) if cid.isdigit() else None
            name = obj.name if obj else f"(unknown id {cid})"
            icon = "📁" if isinstance(obj, discord.CategoryChannel) else "#"
            lines.append(f"　{icon} {name}")
        for rid in delete_role_ids:
            role = guild.get_role(int(rid)) if rid.isdigit() else None
            name = role.name if role else f"(unknown id {rid})"
            lines.append(f"　🎭 {name}")
        lines.append("")

    categories = structure.get("categories", [])
    roles = structure.get("roles", [])
    if categories or roles:
        lines.append("**✨ To create**")
        for cat in categories:
            lines.append(f"　📁 {cat['name']}")
            for ch in cat.get("channels", []):
                icon = "🔊" if ch.get("type") == "voice" else "#"
                lines.append(f"　　{icon} {ch['name']}")
        for r in roles:
            lines.append(f"　🎭 {r['name']}")

    return "\n".join(lines) or "*(no changes proposed)*"


class ConfirmSetupView(discord.ui.View):
    def __init__(self, guild: discord.Guild, structure: dict, author_id: int):
        super().__init__(timeout=300)
        self.guild = guild
        self.structure = structure
        self.author_id = author_id
        self.decided = False
        self.message: discord.Message | None = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the person who ran this command can confirm it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Apply", style=discord.ButtonStyle.success)
    async def confirm(self, _button: discord.ui.Button, interaction: discord.Interaction):
        self.decided = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="⏳ Applying changes...", embed=None, view=self)

        guild = self.guild
        deleted_channels = 0
        deleted_roles = 0
        created_categories = 0
        created_channels = 0
        created_roles = 0
        errors = []

        for cid in self.structure.get("delete_channel_ids", []):
            if not cid.isdigit():
                continue
            channel = guild.get_channel(int(cid))
            if channel is None:
                continue
            try:
                await channel.delete()
                deleted_channels += 1
            except discord.HTTPException as e:
                errors.append(f"Delete '{channel.name}': {e}")

        for rid in self.structure.get("delete_role_ids", []):
            if not rid.isdigit():
                continue
            role = guild.get_role(int(rid))
            if role is None:
                continue
            try:
                await role.delete()
                deleted_roles += 1
            except discord.HTTPException as e:
                errors.append(f"Delete role '{role.name}': {e}")

        for cat_data in self.structure.get("categories", []):
            try:
                category = await guild.create_category(cat_data["name"])
                created_categories += 1
            except discord.HTTPException as e:
                errors.append(f"Category '{cat_data['name']}': {e}")
                continue
            for ch_data in cat_data.get("channels", []):
                try:
                    if ch_data.get("type") == "voice":
                        await guild.create_voice_channel(ch_data["name"], category=category)
                    else:
                        await guild.create_text_channel(ch_data["name"], category=category)
                    created_channels += 1
                except discord.HTTPException as e:
                    errors.append(f"Channel '{ch_data['name']}': {e}")

        for role_data in self.structure.get("roles", []):
            try:
                await guild.create_role(
                    name=role_data["name"],
                    color=_hex_to_color(role_data.get("color_hex")),
                    hoist=role_data.get("hoist", False),
                )
                created_roles += 1
            except discord.HTTPException as e:
                errors.append(f"Role '{role_data['name']}': {e}")

        result = (
            f"✅ Done — deleted {deleted_channels} channels/categories, {deleted_roles} roles. "
            f"Created {created_categories} categories, {created_channels} channels, {created_roles} roles."
        )
        if errors:
            result += "\n\n⚠️ Some items failed:\n" + "\n".join(f"• {e}" for e in errors[:10])
        await interaction.edit_original_response(content=result, view=None)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, _button: discord.ui.Button, interaction: discord.Interaction):
        self.decided = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Cancelled — nothing was changed.", embed=None, view=self)


class SetupServerCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="setup_server",
        description="Use Claude to propose server changes (create/delete channels, roles) for confirmation",
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def setup_server(
        self,
        ctx: discord.ApplicationContext,
        description: discord.Option(str, "Describe what changes you want (e.g. 'delete X and Y, then create...')"),
    ):
        await ctx.defer()

        guild_listing = _describe_guild(ctx.guild)

        response = await _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            tools=[_STRUCTURE_TOOL],
            tool_choice={"type": "tool", "name": "propose_server_changes"},
            messages=[{
                "role": "user",
                "content": (
                    "Propose changes to this Discord server based on the user's request. "
                    "Only include delete_channel_ids/delete_role_ids for items the user explicitly asked to remove, "
                    "using the exact IDs from the listing below — never invent IDs. "
                    "Keep new structure reasonably sized (max ~8 categories, ~6 channels per category, ~10 roles) "
                    "unless the request clearly needs more.\n\n"
                    f"{guild_listing}\n\n"
                    f"Request: {description}"
                ),
            }],
        )

        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if not tool_use:
            await ctx.followup.send("❌ Claude didn't return a valid proposal. Try rephrasing your request.")
            return

        structure = tool_use.input
        summary = _summarize_changes(ctx.guild, structure)

        embed = discord.Embed(
            title="📋 Proposed Server Changes",
            description=summary[:4000],
            color=0x5865F2,
        )
        embed.set_footer(text="Review carefully — deletions are permanent and creations are real.")

        view = ConfirmSetupView(ctx.guild, structure, ctx.author.id)
        view.message = await ctx.followup.send(embed=embed, view=view)


def setup(bot: discord.Bot):
    bot.add_cog(SetupServerCog(bot))
