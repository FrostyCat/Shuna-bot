import asyncio
import json
import os
import re
import secrets
import discord
from discord.ext import commands
from datetime import datetime, UTC
from utils import guild_config
from db import Session
from sqlalchemy import func
from models import Transcript, TicketPanel, TicketType


def _staff_role(guild: discord.Guild) -> discord.Role | None:
    rid = guild_config.get(guild.id, "staff_role_id")
    return guild.get_role(int(rid)) if rid else None


def _ticket_category(guild: discord.Guild) -> discord.CategoryChannel | None:
    cid = guild_config.get(guild.id, "ticket_category_id")
    return guild.get_channel(int(cid)) if cid else None


def _log_channel(guild: discord.Guild) -> discord.TextChannel | None:
    cid = guild_config.get(guild.id, "log_channel_id")
    return guild.get_channel(int(cid)) if cid else None


def _ticket_types(guild_id) -> list[str]:
    raw = guild_config.get(guild_id, "ticket_types")
    if not raw:
        return ["Support"]
    return [t.strip() for t in raw.split(",") if t.strip()]


def parse_color(hex_str: str) -> discord.Color:
    try:
        return discord.Color(int(hex_str.lstrip("#"), 16) & 0xFFFFFF)
    except (ValueError, AttributeError):
        return discord.Color.blurple()


def _next_channel_name(guild: discord.Guild, ticket_type: str, username: str) -> str:
    prefix = f"{ticket_type.lower()}-{username.lower()}"
    existing_numbers = []
    for ch in guild.text_channels:
        m = re.match(rf"^{re.escape(prefix)}-(\d+)$", ch.name)
        if m:
            existing_numbers.append(int(m.group(1)))
    n = max(existing_numbers) + 1 if existing_numbers else 1
    return f"{prefix}-{n}"


class TicketPanelEditModal(discord.ui.Modal):
    def __init__(self, msg: discord.Message, existing: discord.Embed):
        super().__init__(title="Edit Ticket Panel")
        self.msg = msg
        self.existing = existing

        self.add_item(discord.ui.InputText(
            label="Title",
            value=existing.title or "",
            max_length=256,
        ))
        self.add_item(discord.ui.InputText(
            label="Description",
            style=discord.InputTextStyle.long,
            value=existing.description or "",
            max_length=4000,
        ))
        self.add_item(discord.ui.InputText(
            label="Color (hex, e.g. #5865F2)",
            placeholder="#5865F2",
            value=f"#{existing.color.value:06x}" if existing.color else "#5865F2",
            max_length=7,
            required=False,
        ))
        self.add_item(discord.ui.InputText(
            label="Thumbnail URL (small image, top-right)",
            value=existing.thumbnail.url if existing.thumbnail else "",
            max_length=500,
            required=False,
        ))
        self.add_item(discord.ui.InputText(
            label="Image URL (large image, bottom)",
            value=existing.image.url if existing.image else "",
            max_length=500,
            required=False,
        ))

    async def callback(self, interaction: discord.Interaction):
        embed = self.existing
        embed.title = self.children[0].value
        embed.description = self.children[1].value

        color_val = self.children[2].value.strip()
        if color_val:
            embed.color = parse_color(color_val)

        thumbnail = self.children[3].value.strip()
        embed.set_thumbnail(url=thumbnail if thumbnail else None)

        image = self.children[4].value.strip()
        embed.set_image(url=image if image else None)

        await self.msg.edit(embed=embed)
        await interaction.response.send_message("✅ Panel updated!", ephemeral=True)


class TicketMessageModal(discord.ui.Modal):
    def __init__(self, panel: TicketPanel):
        super().__init__(title="Set Ticket Welcome Message")
        self.panel = panel

        self.add_item(discord.ui.InputText(
            label="Title (use {type} for ticket type)",
            value=panel.msg_title or "🎫 {type} Ticket",
            max_length=256,
        ))
        self.add_item(discord.ui.InputText(
            label="Description (use {type}, {user})",
            style=discord.InputTextStyle.long,
            value=panel.msg_description or "Welcome {user}!\n\nDescribe your issue and our team will help you shortly.\nTo close this ticket, click the button below.",
            max_length=4000,
        ))
        self.add_item(discord.ui.InputText(
            label="Color (hex, e.g. #5865F2)",
            value=panel.msg_color or "#5865F2",
            max_length=7,
            required=False,
        ))
        self.add_item(discord.ui.InputText(
            label="Thumbnail URL (small image, top-right)",
            value=panel.msg_thumbnail or "",
            max_length=500,
            required=False,
        ))
        self.add_item(discord.ui.InputText(
            label="Image URL (large image, bottom)",
            value=panel.msg_image or "",
            max_length=500,
            required=False,
        ))

    async def callback(self, interaction: discord.Interaction):
        session = Session()
        p = session.get(TicketPanel, self.panel.id)
        p.msg_title = self.children[0].value
        p.msg_description = self.children[1].value
        p.msg_color = self.children[2].value.strip() or "#5865F2"
        p.msg_thumbnail = self.children[3].value.strip() or None
        p.msg_image = self.children[4].value.strip() or None
        session.commit()
        session.close()
        await interaction.response.send_message("✅ Ticket welcome message saved!", ephemeral=True)


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

        messages = []
        async for msg in interaction.channel.history(limit=500, oldest_first=True):
            messages.append({
                "author": str(msg.author),
                "avatar": str(msg.author.display_avatar.url),
                "content": msg.content,
                "embeds": [e.to_dict() for e in msg.embeds],
                "timestamp": msg.created_at.isoformat(),
            })

        session = Session()
        transcript = Transcript(
            token=secrets.token_urlsafe(12),
            guild_id=str(interaction.guild.id),
            channel_name=interaction.channel.name,
            closed_by=str(interaction.user),
            closed_at=datetime.now(UTC),
            messages=json.dumps(messages),
        )
        session.add(transcript)
        session.commit()
        transcript_token = transcript.token
        session.close()

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
            web_url = os.getenv("WEB_URL")
            view = discord.ui.View()
            if web_url:
                view.add_item(discord.ui.Button(
                    label="View Transcript",
                    url=f"{web_url}/transcript/{transcript_token}",
                    style=discord.ButtonStyle.link,
                    emoji="📄",
                ))
            await log_ch.send(embed=log_embed, view=view)

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
        self.bot.add_view(TicketManageView())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("ticket:open:"):
            ticket_type = custom_id[len("ticket:open:"):]
            await self._open_ticket(interaction, ticket_type)

    async def _open_ticket(self, interaction: discord.Interaction, ticket_type: str):
        guild = interaction.guild
        user = interaction.user
        staff_role = _staff_role(guild)
        category = _ticket_category(guild)

        if category is None:
            await interaction.response.send_message(
                "⚠️ Bot not configured. Admin must set `/config ticket_category`.",
                ephemeral=True,
            )
            return

        channel_name = _next_channel_name(guild, ticket_type, user.name)

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
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"{ticket_type} ticket by {user} | ID: {user.id}",
        )

        panel_msg_id = str(interaction.message.id) if interaction.message else None
        tt = None
        if panel_msg_id:
            session = Session()
            panel = session.query(TicketPanel).filter_by(message_id=panel_msg_id).first()
            if panel:
                tt = session.query(TicketType).filter(
                    TicketType.panel_id == panel.id,
                    func.lower(TicketType.name) == ticket_type.lower(),
                ).first()
            session.close()

        title = (tt.msg_title if tt and tt.msg_title else "🎫 {type} Ticket").replace("{type}", ticket_type)
        description = (tt.msg_description if tt and tt.msg_description else "Welcome {user}!\n\nDescribe your issue and our team will help you shortly.\nTo close this ticket, click the button below.").replace("{type}", ticket_type).replace("{user}", user.mention)
        color = parse_color(tt.msg_color if tt and tt.msg_color else "#5865F2")
        thumbnail = tt.msg_thumbnail if tt and tt.msg_thumbnail else None
        image = tt.msg_image if tt and tt.msg_image else None

        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.utcnow())
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if image:
            embed.set_image(url=image)
        embed.set_footer(text=f"Ticket ID: {channel.id}")

        mention = f"{user.mention} {staff_role.mention}" if staff_role else user.mention
        await channel.send(content=mention, embed=embed, view=TicketManageView())
        await interaction.response.send_message(
            f"Your ticket has been opened: {channel.mention}", ephemeral=True
        )

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

        types = _ticket_types(ctx.guild_id)
        view = discord.ui.View(timeout=None)
        for t in types:
            view.add_item(discord.ui.Button(
                label=t,
                style=discord.ButtonStyle.primary,
                emoji="🎫",
                custom_id=f"ticket:open:{t.lower()}",
            ))

        embed = discord.Embed(
            title="🎫 Support",
            description=(
                "Need help? Click the button below.\n\n"
                "**How it works:**\n"
                "1. Click the button for your ticket type\n"
                "2. A private channel will be created\n"
                "3. Describe your issue — staff will respond"
            ),
            color=discord.Color.blurple(),
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.set_footer(text=ctx.guild.name)

        panel_msg = await ctx.channel.send(embed=embed, view=view)

        session = Session()
        existing = session.query(TicketPanel).filter_by(message_id=str(panel_msg.id)).first()
        if not existing:
            session.add(TicketPanel(
                guild_id=str(ctx.guild_id),
                message_id=str(panel_msg.id),
                channel_id=str(ctx.channel.id),
            ))
            session.commit()
        session.close()

        await ctx.respond(f"✅ Ticket panel set up! ID: `{panel_msg.id}`", ephemeral=True)

    @ticket.command(name="types", description="Set ticket types shown on the panel (comma-separated)")
    @commands.has_permissions(administrator=True)
    async def types(
        self,
        ctx: discord.ApplicationContext,
        types: discord.Option(str, "Types separated by commas, e.g. Support,Subscribe"),
    ):
        type_list = [t.strip() for t in types.split(",") if t.strip()]
        if not type_list:
            await ctx.respond("❌ Provide at least one type.", ephemeral=True)
            return
        guild_config.set_value(ctx.guild_id, "ticket_types", ",".join(type_list))
        await ctx.respond(f"✅ Ticket types set: **{', '.join(type_list)}**\nRun `/ticket setup` again to update the panel.", ephemeral=True)

    @ticket.command(name="message", description="Set the welcome message for a specific ticket panel")
    @commands.has_permissions(administrator=True)
    async def message(
        self,
        ctx: discord.ApplicationContext,
        panel_id: discord.Option(str, "Message ID of the ticket panel"),
    ):
        session = Session()
        panel = session.query(TicketPanel).filter_by(
            message_id=panel_id, guild_id=str(ctx.guild_id)
        ).first()
        session.close()

        if not panel:
            await ctx.respond("❌ Panel not found. Use `/ticket setup` first.", ephemeral=True)
            return

        await ctx.send_modal(TicketMessageModal(panel))

    @ticket.command(name="edit_panel", description="Edits the ticket panel embed (by message ID)")
    @commands.has_permissions(administrator=True)
    async def edit_panel(
        self,
        ctx: discord.ApplicationContext,
        message_id: discord.Option(str, "Message ID of the ticket panel"),
    ):
        try:
            msg = await ctx.channel.fetch_message(int(message_id))
        except (discord.NotFound, ValueError):
            await ctx.respond("❌ Message not found.", ephemeral=True)
            return

        if not msg.embeds:
            await ctx.respond("❌ That message has no embed.", ephemeral=True)
            return

        await ctx.send_modal(TicketPanelEditModal(msg, msg.embeds[0]))

    @ticket.command(name="add", description="Adds a user to the ticket channel")
    @commands.has_permissions(manage_channels=True)
    async def add(self, ctx: discord.ApplicationContext, user: discord.Member):
        if not re.match(r"^[a-z]+-[^-]+-\d+$", ctx.channel.name):
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
        if not re.match(r"^[a-z]+-[^-]+-\d+$", ctx.channel.name):
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
