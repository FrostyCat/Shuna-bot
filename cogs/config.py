import discord
from discord.ext import commands
from utils import guild_config


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    config_group = discord.SlashCommandGroup(
        "config", "Server configuration (admin only)",
        default_member_permissions=discord.Permissions(administrator=True),
    )

    @config_group.command(name="staff_role", description="Set the staff role for tickets")
    async def set_staff_role(
        self,
        ctx: discord.ApplicationContext,
        role: discord.Option(discord.Role, "Staff role"),
    ):
        guild_config.set_value(ctx.guild_id, "staff_role_id", role.id)
        await ctx.respond(f"✅ Staff role set to {role.mention}.", ephemeral=True)

    @config_group.command(name="ticket_category", description="Set the category for ticket channels")
    async def set_ticket_category(
        self,
        ctx: discord.ApplicationContext,
        category: discord.Option(discord.CategoryChannel, "Ticket category"),
    ):
        guild_config.set_value(ctx.guild_id, "ticket_category_id", category.id)
        await ctx.respond(f"✅ Ticket category set to **{category.name}**.", ephemeral=True)

    @config_group.command(name="log_channel", description="Set the channel for ticket logs")
    async def set_log_channel(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.Option(discord.TextChannel, "Log channel"),
    ):
        guild_config.set_value(ctx.guild_id, "log_channel_id", channel.id)
        await ctx.respond(f"✅ Log channel set to {channel.mention}.", ephemeral=True)

    @config_group.command(name="show", description="Show current server configuration")
    async def show_config(self, ctx: discord.ApplicationContext):
        settings = guild_config.get_all(ctx.guild_id)
        guild = ctx.guild

        def fmt_role(rid):
            r = guild.get_role(int(rid)) if rid else None
            return r.mention if r else "Not set"

        def fmt_channel(cid):
            c = guild.get_channel(int(cid)) if cid else None
            return c.mention if c else "Not set"

        embed = discord.Embed(title=f"⚙️ Config — {guild.name}", color=0x8B4513)
        embed.add_field(name="Staff Role", value=fmt_role(settings["staff_role_id"]), inline=False)
        embed.add_field(name="Ticket Category", value=fmt_channel(settings["ticket_category_id"]), inline=False)
        embed.add_field(name="Log Channel", value=fmt_channel(settings["log_channel_id"]), inline=False)
        await ctx.respond(embed=embed, ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Config(bot))
