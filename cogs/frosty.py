import os

import anthropic
import discord
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_raw_roles = os.getenv("FROSTY_ALLOWED_ROLES", "")
_ALLOWED_ROLE_IDS: set[str] = {r.strip() for r in _raw_roles.split(",") if r.strip()}

_raw_users = os.getenv("FROSTY_ALLOWED_USERS", "")
_ALLOWED_USER_IDS: set[str] = {u.strip() for u in _raw_users.split(",") if u.strip()}


class FrostyCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    frosty = discord.SlashCommandGroup("frosty", "Frosty AI commands")

    @frosty.command(name="summarize", description="Summarize last 50 messages in this channel")
    async def summarize(self, ctx: discord.ApplicationContext):
        member = ctx.author
        is_admin = member.guild_permissions.administrator or member.guild_permissions.manage_guild
        has_role = bool({str(r.id) for r in member.roles} & _ALLOWED_ROLE_IDS)

        if not is_admin and not has_role:
            await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)
            return

        await ctx.defer()

        messages = []
        try:
            async for msg in ctx.channel.history(limit=50):
                if msg.content:
                    messages.append(f"{msg.author.display_name}: {msg.content}")
        except discord.Forbidden:
            await ctx.followup.send("❌ Bot doesn't have permission to read message history in this channel.")
            return
        messages.reverse()

        if not messages:
            await ctx.followup.send("No messages to summarize.")
            return

        transcript = "\n".join(messages)

        response = await _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": (
                    "Summarize the following Discord conversation briefly and concisely. "
                    "Focus on the main topics discussed.\n\n"
                    f"{transcript}"
                ),
            }],
        )

        summary = response.content[0].text
        embed = discord.Embed(title="Channel Summary", description=summary, color=0x5865F2)
        embed.set_footer(text=f"Based on last {len(messages)} messages")
        await ctx.followup.send(embed=embed)

    @frosty.command(name="tell", description="Ask Claude anything")
    async def tell(self, ctx: discord.ApplicationContext, message: str):
        if str(ctx.author.id) not in _ALLOWED_USER_IDS:
            await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)
            return

        await ctx.defer()

        response = await _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": message}],
        )

        answer = response.content[0].text
        embed = discord.Embed(description=answer, color=0x5865F2)
        embed.set_author(name=f"Asked by {ctx.author.display_name}")
        embed.set_footer(text=f"Prompt: {message[:100]}{'...' if len(message) > 100 else ''}")
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(FrostyCog(bot))
