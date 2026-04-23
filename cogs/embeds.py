import discord
from discord.ext import commands
from datetime import datetime


def parse_color(hex_str: str) -> discord.Color:
    try:
        return discord.Color(int(hex_str.lstrip("#"), 16) & 0xFFFFFF)
    except (ValueError, AttributeError):
        return discord.Color.blurple()


class EmbedModal(discord.ui.Modal):
    def __init__(self, color, channel, footer, thumbnail, image):
        super().__init__(title="Create Embed")
        self.color = color
        self.channel = channel
        self.footer = footer
        self.thumbnail = thumbnail
        self.image = image

        self.add_item(discord.ui.InputText(
            label="Title",
            placeholder="Embed title...",
            max_length=256,
        ))
        self.add_item(discord.ui.InputText(
            label="Description",
            placeholder="Embed content...",
            style=discord.InputTextStyle.long,
            max_length=4000,
        ))

    async def callback(self, interaction: discord.Interaction):
        title = self.children[0].value
        description = self.children[1].value

        embed = discord.Embed(
            title=title,
            description=description,
            color=parse_color(self.color),
            timestamp=datetime.utcnow(),
        )
        if self.footer:
            embed.set_footer(text=self.footer)
        else:
            embed.set_footer(text=f"Sent by {interaction.user}", icon_url=interaction.user.display_avatar.url)
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)
        if self.image:
            embed.set_image(url=self.image)

        target = self.channel or interaction.channel
        perms = target.permissions_for(interaction.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                f"❌ I don't have permission to send messages in {target.mention}.", ephemeral=True
            )
            return
        await target.send(embed=embed)
        await interaction.response.send_message("Embed sent!", ephemeral=True)


class EmbedEditModal(discord.ui.Modal):
    def __init__(self, msg, existing, color, thumbnail, image):
        super().__init__(title="Edit Embed")
        self.msg = msg
        self.existing = existing
        self.color = color
        self.thumbnail = thumbnail
        self.image = image

        self.add_item(discord.ui.InputText(
            label="Title",
            placeholder="Embed title...",
            value=existing.title or "",
            max_length=256,
        ))
        self.add_item(discord.ui.InputText(
            label="Description",
            placeholder="Embed content...",
            style=discord.InputTextStyle.long,
            value=existing.description or "",
            max_length=4000,
        ))

    async def callback(self, interaction: discord.Interaction):
        embed = self.existing
        embed.title = self.children[0].value
        embed.description = self.children[1].value
        if self.color:
            embed.color = parse_color(self.color)
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)
        if self.image:
            embed.set_image(url=self.image)

        await self.msg.edit(embed=embed)
        await interaction.response.send_message("Embed updated!", ephemeral=True)


class Embeds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    embed_group = discord.SlashCommandGroup("embed", "Embed tools")

    @embed_group.command(name="send", description="Creates and sends a custom embed")
    @commands.has_permissions(manage_messages=True)
    async def send_embed(
        self,
        ctx: discord.ApplicationContext,
        color: discord.Option(str, "Hex color, e.g. #ff0000", required=False, default="#5865F2"),
        channel: discord.Option(discord.TextChannel, "Target channel (default: current)", required=False, default=None),
        footer: discord.Option(str, "Footer text", required=False, default=None),
        thumbnail: discord.Option(str, "Small image URL (top right corner)", required=False, default=None),
        image: discord.Option(str, "Large image URL (bottom of embed)", required=False, default=None),
    ):
        await ctx.send_modal(EmbedModal(color, channel, footer, thumbnail, image))

    @embed_group.command(name="edit", description="Edits an embed from a message (by ID)")
    @commands.has_permissions(manage_messages=True)
    async def edit_embed(
        self,
        ctx: discord.ApplicationContext,
        message_id: discord.Option(str, "Message ID containing the embed"),
        color: discord.Option(str, "New hex color", required=False, default=None),
        thumbnail: discord.Option(str, "New small image URL (top right)", required=False, default=None),
        image: discord.Option(str, "New large image URL (bottom)", required=False, default=None),
    ):
        try:
            msg = await ctx.channel.fetch_message(int(message_id))
        except (discord.NotFound, ValueError):
            await ctx.respond("Message not found.", ephemeral=True)
            return

        if not msg.embeds:
            await ctx.respond("That message does not contain an embed.", ephemeral=True)
            return

        existing = msg.embeds[0]
        await ctx.send_modal(EmbedEditModal(msg, existing, color, thumbnail, image))

    @discord.slash_command(name="help", description="Shows all available commands")
    async def help_command(self, ctx: discord.ApplicationContext):
        embed = discord.Embed(
            title="📋 Command List",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(
            name="🎫 Tickets",
            value=(
                "`/ticket setup` — send the ticket panel\n"
                "`/ticket add <user>` — add a user to a ticket\n"
                "`/ticket remove <user>` — remove a user from a ticket"
            ),
            inline=False,
        )
        embed.add_field(
            name="🖼️ Embeds",
            value=(
                "`/embed send` — send a custom embed\n"
                "`/embed edit` — edit an existing embed"
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Info",
            value=(
                "`/help` — this message\n"
                "`/serverinfo` — server information\n"
                "`/userinfo [user]` — user information\n"
                "`/botinfo` — bot information"
            ),
            inline=False,
        )
        if ctx.guild.icon:
            embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon.url)
        await ctx.respond(embed=embed)

    @discord.slash_command(name="serverinfo", description="Shows server information")
    async def serverinfo(self, ctx: discord.ApplicationContext):
        g = ctx.guild
        embed = discord.Embed(
            title=f"📊 {g.name}",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="👑 Owner", value=g.owner.mention, inline=True)
        embed.add_field(name="👥 Members", value=g.member_count, inline=True)
        embed.add_field(name="📅 Created", value=f"<t:{int(g.created_at.timestamp())}:D>", inline=True)
        embed.add_field(name="💬 Channels", value=len(g.channels), inline=True)
        embed.add_field(name="🎭 Roles", value=len(g.roles), inline=True)
        embed.add_field(name="😀 Emojis", value=len(g.emojis), inline=True)
        embed.set_footer(text=f"ID: {g.id}")
        await ctx.respond(embed=embed)

    @discord.slash_command(name="userinfo", description="Shows information about a user")
    async def userinfo(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "User (default: yourself)", required=False, default=None),
    ):
        user = user or ctx.author
        roles = [r.mention for r in reversed(user.roles) if r != ctx.guild.default_role]
        color = user.color if user.color != discord.Color.default() else discord.Color.blurple()
        embed = discord.Embed(title=f"👤 {user}", color=color, timestamp=datetime.utcnow())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="📛 Nickname", value=user.display_name, inline=True)
        embed.add_field(name="🤖 Bot", value="Yes" if user.bot else "No", inline=True)
        embed.add_field(name="📅 Discord since", value=f"<t:{int(user.created_at.timestamp())}:D>", inline=True)
        embed.add_field(name="📥 Joined", value=f"<t:{int(user.joined_at.timestamp())}:D>", inline=True)
        role_text = " ".join(roles[:10]) + ("…" if len(roles) > 10 else "") if roles else "None"
        embed.add_field(name=f"🎭 Roles ({len(roles)})", value=role_text, inline=False)
        embed.set_footer(text=f"ID: {user.id}")
        await ctx.respond(embed=embed)

    @discord.slash_command(name="botinfo", description="Shows bot information")
    async def botinfo(self, ctx: discord.ApplicationContext):
        bot = self.bot
        embed = discord.Embed(
            title=f"🤖 {bot.user.name}",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.add_field(name="📚 Library", value="py-cord", inline=True)
        embed.add_field(name="🖥️ Servers", value=len(bot.guilds), inline=True)
        embed.add_field(name="👥 Users", value=sum(g.member_count for g in bot.guilds), inline=True)
        embed.add_field(name="📡 Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)
        embed.set_footer(text=f"ID: {bot.user.id}")
        await ctx.respond(embed=embed)


def setup(bot: commands.Bot):
    bot.add_cog(Embeds(bot))
