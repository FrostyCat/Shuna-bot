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

_TOOLS = [
    {
        "name": "get_channel_messages",
        "description": "Fetch recent messages from a Discord channel by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID (digits only)",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many messages to fetch (max 100)",
                    "default": 50,
                },
            },
            "required": ["channel_id"],
        },
    }
]


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

    async def _run_tool(self, name: str, tool_input: dict) -> str:
        if name == "get_channel_messages":
            channel_id = int(tool_input["channel_id"])
            limit = min(int(tool_input.get("limit", 50)), 100)
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                return "Error: channel not found or bot has no access."
            try:
                msgs = []
                async for msg in channel.history(limit=limit):
                    if msg.content:
                        msgs.append(f"{msg.author.display_name}: {msg.content}")
                msgs.reverse()
                return "\n".join(msgs) if msgs else "No messages found."
            except discord.Forbidden:
                return "Error: bot doesn't have Read Message History permission in that channel."
        return "Error: unknown tool."

    @frosty.command(name="tell", description="Ask Claude anything")
    async def tell(self, ctx: discord.ApplicationContext, message: str):
        if str(ctx.author.id) not in _ALLOWED_USER_IDS:
            await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)
            return

        await ctx.defer()

        api_messages = [{"role": "user", "content": message}]

        while True:
            response = await _client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                tools=_TOOLS,
                messages=api_messages,
            )

            if response.stop_reason == "tool_use":
                # append assistant message with tool calls
                api_messages.append({"role": "assistant", "content": response.content})

                # execute each tool and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await self._run_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                api_messages.append({"role": "user", "content": tool_results})

            else:
                # final answer
                answer = next(b.text for b in response.content if hasattr(b, "text"))
                embed = discord.Embed(description=answer, color=0x5865F2)
                embed.set_author(name=f"Asked by {ctx.author.display_name}")
                embed.set_footer(text=f"Prompt: {message[:100]}{'...' if len(message) > 100 else ''}")
                await ctx.followup.send(embed=embed)
                break


def setup(bot: discord.Bot):
    bot.add_cog(FrostyCog(bot))
