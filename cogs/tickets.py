import asyncio
import discord
from discord.ext import commands
from datetime import datetime
from utils import guild_config


def _staff_role(guild: discord.Guild) -> discord.Role | None:
    rid = guild_config.get(guild.id, "staff_role_id")
    return guild.get_role(int(rid)) if rid else None


def _ticket_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    cid = guild_config.get(guild.id, "ticket_category_id")
    return guild.get_channel(int(cid)) if cid else None


def _log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    cid = guild_config.get(guild.id, "log_channel_id")
    return guild.get_channel(int(cid)) if cid else None


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="ticket:open",
    )
    async def open_ticket(self, _button: discord.ui.Button, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        staff_role = _staff_role(guild)
        category = _ticket_category(guild)

        if category is None:
            await interaction.response.send_message(
                "⚠️ The bot is not configured yet. An administrator must set `/config ticket_category`.",
                ephemeral=True,
            )
            return

        existing = discord.utils.get(guild.text_channels, name=f"ticket-{user.name.lower()}")
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}", ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        channel = await guild.create_text_channel(
            name=f"ticket-{user.name.lower()}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket by {user} | ID: {user.id}",
        )

        embed = discord.Embed(
            title="🎫 New Ticket",
            description=(
                f"Welcome {user.mention}!\n\n"
                "Describe your issue and our team will help you shortly.\n"
                "To close this ticket, click the button below."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Ticket ID: {channel.id}")

        mention = f"{user.mention} {staff_role.mention}" if staff_role else user.mention
        await channel.send(content=mention, embed=embed, view=TicketManageView())

        await interaction.response.send_message(
            f"Your ticket has been opened: {channel.mention}", ephemeral=True
        )


class TicketManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket:close",
    )
    async def close_ticket(self, _button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Are you sure you want to close this ticket?",
                color=discord.Color.orange(),
            ),
            view=ConfirmCloseView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.success,
        emoji="✋",
        custom_id="ticket:claim",
    )
    async def claim_ticket(self, _button: discord.ui.Button, interaction: discord.Interaction):
        staff_role = _staff_role(interaction.guild)
        if staff_role and staff_role not in interaction.user.roles:
            await interaction.response.send_message(
                "Only staff members can claim a ticket.", ephemeral=True
            )
            return

        embed = discord.Embed(
            description=f"✋ Ticket claimed by {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.defer()


class ConfirmCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    @discord.ui.button(label="Yes, close", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, _button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()

        embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=f"Closed by {interaction.user.mention}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow(),
        )
        await interaction.channel.send(embed=embed)

        log_ch = _log_channel(interaction.guild)
        if log_ch:
            log_embed = discord.Embed(
                title="📋 Log — Closed Ticket",
                color=discord.Color.red(),
                timestamp=datetime.utcnow(),
            )
            log_embed.add_field(name="Channel", value=interaction.channel.name, inline=True)
            log_embed.add_field(name="Closed by", value=str(interaction.user), inline=True)
            await log_ch.send(embed=log_embed)

        await asyncio.sleep(3)
        await interaction.channel.delete()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, _button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketManageView())

    ticket = discord.SlashCommandGroup("ticket", "Ticket system commands")

    @ticket.command(name="setup", description="Sends the ticket panel to the current channel")
    @commands.has_permissions(administrator=True)
    async def setup(self, ctx: discord.ApplicationContext):
        settings = guild_config.get_all(ctx.guild_id)
        missing = [k for k in ("staff_role_id", "ticket_category_id") if not settings.get(k)]
        if missing:
            labels = {"staff_role_id": "`/config staff_role`", "ticket_category_id": "`/config ticket_category`"}
            tip = " and ".join(labels[k] for k in missing)
            await ctx.respond(f"⚠️ Please configure the server first: {tip}", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎫 Support",
            description=(
                "Need help? Click the button below.\n\n"
                "**How it works:**\n"
                "1. Click **Open Ticket**\n"
                "2. A private channel will be created\n"
                "3. Describe your issue — staff will respond"
            ),
            color=discord.Color.blurple(),
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text=ctx.guild.name)

        await ctx.channel.send(embed=embed, view=TicketPanelView())
        await ctx.respond("Ticket panel has been set up!", ephemeral=True)

    @ticket.command(name="add", description="Adds a user to the ticket channel")
    @commands.has_permissions(manage_channels=True)
    async def add(self, ctx: discord.ApplicationContext, user: discord.Member):
        if not ctx.channel.name.startswith("ticket-"):
            await ctx.respond("This command only works inside ticket channels!", ephemeral=True)
            return
        await ctx.channel.set_permissions(
            user, view_channel=True, send_messages=True, read_message_history=True
        )
        await ctx.channel.send(embed=discord.Embed(
            description=f"✅ {user.mention} was added by {ctx.author.mention}",
            color=discord.Color.green(),
        ))
        await ctx.respond("User added.", ephemeral=True)

    @ticket.command(name="remove", description="Removes a user from the ticket channel")
    @commands.has_permissions(manage_channels=True)
    async def remove(self, ctx: discord.ApplicationContext, user: discord.Member):
        if not ctx.channel.name.startswith("ticket-"):
            await ctx.respond("This command only works inside ticket channels!", ephemeral=True)
            return
        await ctx.channel.set_permissions(user, overwrite=None)
        await ctx.channel.send(embed=discord.Embed(
            description=f"❌ {user.mention} was removed by {ctx.author.mention}",
            color=discord.Color.red(),
        ))
        await ctx.respond("User removed.", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(Tickets(bot))
