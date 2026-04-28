import discord
from discord.ext import commands
from datetime import datetime, UTC

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import Session
from models import CwlSignup, CwlSignupPanel, DiscordUser, Player


class CwlSignupView(discord.ui.View):
    def __init__(self, guild_id: str, season: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Sign Up",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"cwl:signup:{guild_id}:{season}",
        ))
        self.add_item(discord.ui.Button(
            label="Remove",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"cwl:remove:{guild_id}:{season}",
        ))


class AccountSelect(discord.ui.Select):
    def __init__(self, guild_id: str, season: str, players: list):
        self._guild_id = guild_id
        self._season = season
        options = [
            discord.SelectOption(label=p.name[:25], value=p.tag, description=p.tag)
            for p in players
        ]
        super().__init__(
            placeholder="Wybierz konto CoC...",
            options=options,
            custom_id=f"cwl:select:{guild_id}:{season}",
        )

    async def callback(self, interaction: discord.Interaction):
        player_tag = self.values[0]
        discord_id = str(interaction.user.id)

        session = Session()
        try:
            stmt = pg_insert(CwlSignup).values(
                guild_id=self._guild_id,
                season=self._season,
                discord_id=discord_id,
                player_tag=player_tag,
                signed_up_at=datetime.now(UTC),
            ).on_conflict_do_update(
                index_elements=["guild_id", "season", "discord_id"],
                set_={"player_tag": player_tag, "signed_up_at": datetime.now(UTC)},
            )
            session.execute(stmt)
            session.commit()
            player = session.query(Player).filter_by(tag=player_tag).first()
            player_name = player.name if player else player_tag
        finally:
            session.close()

        await _update_signup_embed(interaction.client, self._guild_id, self._season)
        await interaction.response.send_message(
            f"✅ Zapisano jako **{player_name}**!", ephemeral=True
        )


class AccountSelectView(discord.ui.View):
    def __init__(self, guild_id: str, season: str, players: list):
        super().__init__(timeout=60)
        self.add_item(AccountSelect(guild_id, season, players))


async def _update_signup_embed(bot: discord.Bot, guild_id: str, season: str):
    session = Session()
    try:
        panel = session.query(CwlSignupPanel).filter_by(
            guild_id=guild_id, season=season
        ).order_by(CwlSignupPanel.id.desc()).first()
        if not panel:
            return
        count = session.query(CwlSignup).filter_by(
            guild_id=guild_id, season=season
        ).count()
    finally:
        session.close()

    channel = bot.get_channel(int(panel.channel_id))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(panel.message_id))
    except discord.NotFound:
        return

    embed = msg.embeds[0] if msg.embeds else discord.Embed(title="CWL Sign Up")
    embed.set_footer(text=f"Zapisanych: {count}")
    await msg.edit(embed=embed, view=CwlSignupView(guild_id, season))


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
                    self.bot.add_view(
                        CwlSignupView(panel.guild_id, panel.season),
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
            guild_id, season = parts[2], parts[3]
            try:
                await self._handle_signup(interaction, guild_id, season)
            except Exception:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⚠️ Coś poszło nie tak. Spróbuj ponownie.", ephemeral=True
                    )

        elif custom_id.startswith("cwl:remove:"):
            parts = custom_id.split(":", 3)
            if len(parts) < 4:
                return
            guild_id, season = parts[2], parts[3]
            try:
                await self._handle_remove(interaction, guild_id, season)
            except Exception:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⚠️ Coś poszło nie tak. Spróbuj ponownie.", ephemeral=True
                    )

    async def _handle_signup(self, interaction: discord.Interaction, guild_id: str, season: str):
        discord_id = str(interaction.user.id)
        session = Session()
        try:
            db_user = session.query(DiscordUser).filter_by(discord_id=discord_id).first()
            players = list(db_user.players) if db_user else []
        finally:
            session.close()

        if not players:
            await interaction.response.send_message(
                "❌ Nie masz zlinkowanego konta CoC. Użyj `/link` na serwerze.",
                ephemeral=True,
            )
            return

        if len(players) == 1:
            player = players[0]
            session = Session()
            try:
                stmt = pg_insert(CwlSignup).values(
                    guild_id=guild_id,
                    season=season,
                    discord_id=discord_id,
                    player_tag=player.tag,
                    signed_up_at=datetime.now(UTC),
                ).on_conflict_do_update(
                    index_elements=["guild_id", "season", "discord_id"],
                    set_={"player_tag": player.tag, "signed_up_at": datetime.now(UTC)},
                )
                session.execute(stmt)
                session.commit()
            finally:
                session.close()
            await _update_signup_embed(self.bot, guild_id, season)
            await interaction.response.send_message(
                f"✅ Zapisano jako **{player.name}**!", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Wybierz konto CoC do zapisu na CWL:",
                view=AccountSelectView(guild_id, season, players),
                ephemeral=True,
            )

    async def _handle_remove(self, interaction: discord.Interaction, guild_id: str, season: str):
        discord_id = str(interaction.user.id)
        session = Session()
        try:
            row = session.query(CwlSignup).filter_by(
                guild_id=guild_id, season=season, discord_id=discord_id
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
            await _update_signup_embed(self.bot, guild_id, season)
            await interaction.response.send_message("❌ Wypisano z CWL.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Nie jesteś zapisany na ten sezon.", ephemeral=True
            )


def setup(bot: commands.Bot):
    bot.add_cog(CwlSignupCog(bot))
