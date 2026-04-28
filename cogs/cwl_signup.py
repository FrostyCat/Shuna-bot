import discord
from discord.ext import commands
from datetime import datetime, UTC

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import Session
from models import CwlSignup, CwlSignupPanel, DiscordUser, Player

_BUTTON_INSTRUCTIONS = (
    "Click **✅ Sign Up** to register for this month's CWL.\n"
    "Click **❌ Remove** to withdraw your signup.\n\n"
    "If you have multiple accounts linked, you'll be asked to choose one."
)


class CwlSignupView(discord.ui.View):
    def __init__(self, guild_id: str, panel_id: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Sign Up",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"cwl:signup:{guild_id}:{panel_id}",
        ))
        self.add_item(discord.ui.Button(
            label="Remove",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"cwl:remove:{guild_id}:{panel_id}",
        ))


class AccountSelect(discord.ui.Select):
    def __init__(self, guild_id: str, panel_id: int, players: list):
        self._guild_id = guild_id
        self._panel_id = panel_id
        options = [
            discord.SelectOption(label=p.name[:25], value=p.tag, description=p.tag)
            for p in players
        ]
        super().__init__(
            placeholder="Select your CoC account...",
            options=options,
            custom_id=f"cwl:select:{guild_id}:{panel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        player_tag = self.values[0]
        discord_id = str(interaction.user.id)

        session = Session()
        try:
            panel = session.query(CwlSignupPanel).filter_by(id=self._panel_id).first()
            if not panel:
                await interaction.response.send_message("❌ This sign-up post no longer exists.", ephemeral=True)
                return
            stmt = pg_insert(CwlSignup).values(
                panel_id=self._panel_id,
                guild_id=panel.guild_id,
                season=panel.season,
                discord_id=discord_id,
                player_tag=player_tag,
                signed_up_at=datetime.now(UTC),
            ).on_conflict_do_nothing(
                index_elements=["panel_id", "player_tag"],
            )
            session.execute(stmt)
            session.commit()
            player = session.query(Player).filter_by(tag=player_tag).first()
            player_name = player.name if player else player_tag
        finally:
            session.close()

        await _update_panel_embed(interaction.client, self._panel_id)
        await interaction.response.send_message(
            f"✅ Signed up as **{player_name}**!", ephemeral=True
        )


class AccountSelectView(discord.ui.View):
    def __init__(self, guild_id: str, panel_id: int, players: list):
        super().__init__(timeout=60)
        self.add_item(AccountSelect(guild_id, panel_id, players))


class AccountRemoveSelect(discord.ui.Select):
    def __init__(self, guild_id: str, panel_id: int, options: list):
        self._guild_id = guild_id
        self._panel_id = panel_id
        select_options = [
            discord.SelectOption(label=name[:25], value=str(signup_id), description=tag)
            for signup_id, name, tag in options
        ]
        super().__init__(
            placeholder="Select account to remove...",
            options=select_options,
            custom_id=f"cwl:removeselect:{guild_id}:{panel_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        signup_id = int(self.values[0])
        session = Session()
        try:
            row = session.query(CwlSignup).filter_by(id=signup_id).first()
            if row:
                player = session.query(Player).filter_by(tag=row.player_tag).first()
                player_name = player.name if player else row.player_tag
                session.delete(row)
                session.commit()
            else:
                player_name = None
        finally:
            session.close()

        if player_name is not None:
            await _update_panel_embed(interaction.client, self._panel_id)
            await interaction.response.send_message(
                f"❌ Removed **{player_name}** from CWL signup.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Could not find that signup.", ephemeral=True)


class AccountRemoveView(discord.ui.View):
    def __init__(self, guild_id: str, panel_id: int, options: list):
        super().__init__(timeout=60)
        self.add_item(AccountRemoveSelect(guild_id, panel_id, options))


async def _update_panel_embed(bot: discord.Bot, panel_id: int):
    session = Session()
    try:
        panel = session.query(CwlSignupPanel).filter_by(id=panel_id).first()
        if not panel:
            return
        signups = session.query(CwlSignup).filter_by(panel_id=panel_id).all()
        count = len(signups)
        tags = [s.player_tag for s in signups]
        players_map = {p.tag: p for p in session.query(Player).filter(Player.tag.in_(tags)).all()} if tags else {}
    finally:
        session.close()

    channel = bot.get_channel(int(panel.channel_id))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(panel.message_id))
    except discord.NotFound:
        return

    guild = bot.get_guild(int(panel.guild_id))

    rows = []
    for i, s in enumerate(signups, 1):
        p = players_map.get(s.player_tag)
        name = (p.name if p else s.player_tag)
        th = str(p.th_level) if (p and p.th_level) else "?"
        disc = ""
        if guild:
            member = guild.get_member(int(s.discord_id))
            if member:
                disc = member.display_name
        rows.append((i, th, name, disc))

    def _trunc(s, n):
        return s[:n] if len(s) <= n else s[:n - 1] + "…"

    if rows:
        th_w = 3
        name_w = max(6, min(18, max(len(r[2]) for r in rows)))
        disc_w = max(7, min(16, max((len(r[3]) for r in rows), default=7)))
        header = f"{'#':>3}  {'TH':<{th_w}}  {'PLAYER':<{name_w}}  {'DISCORD':<{disc_w}}"
        sep    = "─" * len(header)
        lines  = [header, sep]
        for i, th, name, disc in rows:
            lines.append(
                f"{i:>3}  {_trunc(th, th_w):<{th_w}}  "
                f"{_trunc(name, name_w):<{name_w}}  "
                f"{_trunc(disc, disc_w):<{disc_w}}"
            )
        player_section = "```\n" + "\n".join(lines) + "\n```"
    else:
        player_section = None

    parts = []
    if panel.embed_description:
        parts.append(panel.embed_description)
    if player_section:
        parts.append(player_section)
    parts.append(_BUTTON_INSTRUCTIONS)

    embed = discord.Embed(
        title=f"🏆 {panel.embed_title or 'CWL Roster Sign Up'}",
        description="\n\n".join(parts),
        color=0xf472b6,
    )
    embed.set_footer(text=f"Signed up: {count}")
    await msg.edit(embed=embed, view=CwlSignupView(panel.guild_id, panel_id))


class CwlSignupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.loop.create_task(self._restore_panels())

    async def _restore_panels(self):
        await self.bot.wait_until_ready()
        session = Session()
        try:
            panels = session.query(CwlSignupPanel).all()
            stale_ids = []
            for panel in panels:
                channel = self.bot.get_channel(int(panel.channel_id))
                if channel is None:
                    stale_ids.append(panel.id)
                    continue
                try:
                    await channel.fetch_message(int(panel.message_id))
                    if panel.is_open:
                        self.bot.add_view(
                            CwlSignupView(panel.guild_id, panel.id),
                            message_id=int(panel.message_id),
                        )
                except discord.NotFound:
                    stale_ids.append(panel.id)
            if stale_ids:
                session.query(CwlSignupPanel).filter(
                    CwlSignupPanel.id.in_(stale_ids)
                ).delete(synchronize_session=False)
                session.commit()
        finally:
            session.close()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")

        if custom_id.startswith("cwl:signup:"):
            parts = custom_id.split(":", 3)
            if len(parts) < 4:
                return
            guild_id, panel_id_str = parts[2], parts[3]
            try:
                await self._handle_signup(interaction, guild_id, int(panel_id_str))
            except Exception:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⚠️ Something went wrong. Please try again.", ephemeral=True
                    )

        elif custom_id.startswith("cwl:remove:"):
            parts = custom_id.split(":", 3)
            if len(parts) < 4:
                return
            guild_id, panel_id_str = parts[2], parts[3]
            try:
                await self._handle_remove(interaction, guild_id, int(panel_id_str))
            except Exception:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⚠️ Something went wrong. Please try again.", ephemeral=True
                    )

    async def _handle_signup(self, interaction: discord.Interaction, guild_id: str, panel_id: int):
        discord_id = str(interaction.user.id)
        session = Session()
        try:
            panel = session.query(CwlSignupPanel).filter_by(id=panel_id).first()
            db_user = session.query(DiscordUser).filter_by(discord_id=discord_id).first()
            players = list(db_user.players) if db_user else []
            already_signed = {
                s.player_tag for s in session.query(CwlSignup).filter_by(
                    panel_id=panel_id, discord_id=discord_id
                ).all()
            }
        finally:
            session.close()

        if not panel:
            await interaction.response.send_message("❌ This sign-up post no longer exists.", ephemeral=True)
            return

        if not players:
            await interaction.response.send_message(
                "❌ You don't have a linked CoC account. Use `/link` in Discord.",
                ephemeral=True,
            )
            return

        available = [p for p in players if p.tag not in already_signed]

        if not available:
            await interaction.response.send_message(
                "✅ All your linked accounts are already signed up for this post.",
                ephemeral=True,
            )
            return

        if len(available) == 1:
            player = available[0]
            session = Session()
            try:
                stmt = pg_insert(CwlSignup).values(
                    panel_id=panel_id,
                    guild_id=guild_id,
                    season=panel.season,
                    discord_id=discord_id,
                    player_tag=player.tag,
                    signed_up_at=datetime.now(UTC),
                ).on_conflict_do_nothing(
                    index_elements=["panel_id", "player_tag"],
                )
                session.execute(stmt)
                session.commit()
            finally:
                session.close()
            await _update_panel_embed(self.bot, panel_id)
            await interaction.response.send_message(
                f"✅ Signed up as **{player.name}**!", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Select a CoC account to sign up for CWL:",
                view=AccountSelectView(guild_id, panel_id, available),
                ephemeral=True,
            )

    async def _handle_remove(self, interaction: discord.Interaction, guild_id: str, panel_id: int):
        discord_id = str(interaction.user.id)
        session = Session()
        try:
            signups = session.query(CwlSignup).filter_by(
                panel_id=panel_id, discord_id=discord_id
            ).all()
            signup_data = [(s.id, s.player_tag) for s in signups]
        finally:
            session.close()

        if not signup_data:
            await interaction.response.send_message(
                "You are not signed up for this post.", ephemeral=True
            )
            return

        if len(signup_data) == 1:
            signup_id = signup_data[0][0]
            session = Session()
            try:
                row = session.query(CwlSignup).filter_by(id=signup_id).first()
                if row:
                    session.delete(row)
                    session.commit()
            finally:
                session.close()
            await _update_panel_embed(self.bot, panel_id)
            await interaction.response.send_message("❌ Removed from CWL signup.", ephemeral=True)
        else:
            # Multiple accounts signed up — let user pick which to remove
            tags = [tag for _, tag in signup_data]
            session = Session()
            try:
                players_map = {
                    p.tag: p for p in session.query(Player).filter(Player.tag.in_(tags)).all()
                }
            finally:
                session.close()

            options = [
                (signup_id, (players_map[tag].name if tag in players_map else tag), tag)
                for signup_id, tag in signup_data
            ]
            await interaction.response.send_message(
                "Select which account to remove from CWL signup:",
                view=AccountRemoveView(guild_id, panel_id, options),
                ephemeral=True,
            )


def setup(bot: commands.Bot):
    bot.add_cog(CwlSignupCog(bot))
