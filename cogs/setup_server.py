import os

import anthropic
import discord
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_STRUCTURE_TOOL = {
    "name": "propose_server_structure",
    "description": "Propose a Discord server structure of categories, channels, and roles based on the user's request.",
    "input_schema": {
        "type": "object",
        "properties": {
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
        "required": ["categories", "roles"],
    },
}


def _hex_to_color(hex_str: str | None) -> discord.Color:
    if not hex_str:
        return discord.Color.default()
    try:
        return discord.Color(int(hex_str.lstrip("#"), 16))
    except ValueError:
        return discord.Color.default()


def _summarize_structure(structure: dict) -> str:
    lines = []
    for cat in structure.get("categories", []):
        lines.append(f"**📁 {cat['name']}**")
        for ch in cat.get("channels", []):
            icon = "🔊" if ch.get("type") == "voice" else "#"
            lines.append(f"　{icon} {ch['name']}")
    roles = structure.get("roles", [])
    if roles:
        lines.append("")
        lines.append("**🎭 Roles**")
        for r in roles:
            lines.append(f"　• {r['name']}")
    return "\n".join(lines) or "*(empty proposal)*"


class ConfirmSetupView(discord.ui.View):
    def __init__(self, structure: dict, author_id: int):
        super().__init__(timeout=300)
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

    @discord.ui.button(label="✅ Create", style=discord.ButtonStyle.success)
    async def confirm(self, _button: discord.ui.Button, interaction: discord.Interaction):
        self.decided = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="⏳ Creating structure...", embed=None, view=self)

        guild = interaction.guild
        created_categories = 0
        created_channels = 0
        created_roles = 0
        errors = []

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
            f"✅ Done — created {created_categories} categories, {created_channels} channels, {created_roles} roles."
        )
        if errors:
            result += "\n\n⚠️ Some items failed:\n" + "\n".join(f"• {e}" for e in errors[:10])
        await interaction.edit_original_response(content=result, view=None)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, _button: discord.ui.Button, interaction: discord.Interaction):
        self.decided = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Cancelled — nothing was created.", embed=None, view=self)


class SetupServerCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="setup_server",
        description="Use Claude to propose a server structure (categories, channels, roles) — asks for confirmation before creating anything",
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def setup_server(
        self,
        ctx: discord.ApplicationContext,
        description: discord.Option(str, "Describe what kind of server structure you want"),
    ):
        await ctx.defer()

        response = await _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            tools=[_STRUCTURE_TOOL],
            tool_choice={"type": "tool", "name": "propose_server_structure"},
            messages=[{
                "role": "user",
                "content": (
                    "Propose a Discord server structure for the following request. "
                    "Keep it reasonably sized (max ~8 categories, ~6 channels per category, ~10 roles) "
                    "unless the request clearly needs more.\n\n"
                    f"Request: {description}"
                ),
            }],
        )

        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if not tool_use:
            await ctx.followup.send("❌ Claude didn't return a valid structure. Try rephrasing your request.")
            return

        structure = tool_use.input
        summary = _summarize_structure(structure)

        embed = discord.Embed(
            title="📋 Proposed Server Structure",
            description=summary[:4000],
            color=0x5865F2,
        )
        embed.set_footer(text="Review carefully — this will create real channels and roles on your server.")

        view = ConfirmSetupView(structure, ctx.author.id)
        view.message = await ctx.followup.send(embed=embed, view=view)


def setup(bot: discord.Bot):
    bot.add_cog(SetupServerCog(bot))
