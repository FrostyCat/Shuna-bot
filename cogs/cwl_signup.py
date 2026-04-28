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
            ).on_conflict_do_update(
                index_elements=["panel_id", "discord_id"],
                set_={"player_tag": player_tag, "signed_up_at": datetime.now(UTC)},
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

    player_lines = []
    for s in signups:
        p = players_map.get(s.player_tag)
        if p:
            th_str = f" — TH{p.th_level}" if p.th_level else ""
            player_lines.append(f"• {p.name}{th_str}")
        else:
            player_lines.append(f"• {s.player_tag}")

    parts = []
    if panel.embed_description:
        parts.append(panel.embed_description)
    if player_lines:
        parts.append("\n".join(player_lines))
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

        if len(players) == 1:
            player = players[0]
            session = Session()
            try:
                stmt = pg_insert(CwlSignup).values(
                    panel_id=panel_id,
                    guild_id=guild_id,
                    season=panel.season,
                    discord_id=discord_id,
                    player_tag=player.tag,
                    signed_up_at=datetime.now(UTC),
                ).on_conflict_do_update(
                    index_elements=["panel_id", "discord_id"],
                    set_={"player_tag": player.tag, "signed_up_at": datetime.now(UTC)},
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
                "Select your CoC account to sign up for CWL:",
                view=AccountSelectView(guild_id, panel_id, players),
                ephemeral=True,
            )

    async def _handle_remove(self, interaction: discord.Interaction, guild_id: str, panel_id: int):
        discord_id = str(interaction.user.id)
        session = Session()
        try:
            row = session.query(CwlSignup).filter_by(
                panel_id=panel_id, discord_id=discord_id
            ).first()
            if row:
                session.delete(row)
                session.commit()
                removed = True
            else:
                removed = False
        finally:
            session.close()

        if removed:
            await _update_panel_embed(self.bot, panel_id)
            await interaction.response.send_message("❌ Removed from CWL signup.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "You are not signed up for this post.", ephemeral=True
            )


def setup(bot: commands.Bot):
    bot.add_cog(CwlSignupCog(bot))
