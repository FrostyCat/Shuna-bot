import asyncio
import os
from datetime import time as dt_time

import discord
from discord.ext import commands, tasks

from coc_api import get_clan_members, get_player
from db import Session
from models import Clan, Player, GuildClan, GuildConfig
from helpers import WARSAW, add_player_to_db, fetch_player_attacks, fetch_war_attacks, fetch_cwl_attacks

_NOTIFY_GUILD_ID = os.getenv("NOTIFY_GUILD_ID", "")


class TasksCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.refresh_players.start()
        self.refresh_clans.start()
        self.snapshot_ranks.start()
        self.refresh_wars.start()

    def cog_unload(self):
        self.refresh_players.cancel()
        self.refresh_clans.cancel()
        self.snapshot_ranks.cancel()
        self.refresh_wars.cancel()

    async def _notify_new_player(self, session, clan_tag: str, name: str, tag: str):
        loop = asyncio.get_running_loop()
        guild_clans = await loop.run_in_executor(
            None, lambda: session.query(GuildClan).filter_by(clan_tag=clan_tag).all()
        )
        for gc in guild_clans:
            config = await loop.run_in_executor(
                None, lambda gid=gc.guild_id: session.query(GuildConfig).filter_by(guild_id=gid).first()
            )
            if not config or not config.log_channel_id:
                continue
            if _NOTIFY_GUILD_ID and str(gc.guild_id) != _NOTIFY_GUILD_ID:
                continue
            ch = self.bot.get_channel(int(config.log_channel_id))
            if not ch:
                continue
            embed = discord.Embed(
                title="New Player Tracking Started",
                description=(
                    f"**{name}** (`{tag}`) has been added to the tracking system.\n"
                    f"Stats collection starts now — first-day data will be skipped to ensure accuracy."
                ),
                color=0xf472b6,
            )
            try:
                await ch.send(embed=embed)
            except Exception as e:
                print(f"Notify error for {tag}: {e}")

    @tasks.loop(minutes=10)
    async def refresh_players(self):
        loop = asyncio.get_running_loop()
        session = Session()
        try:
            players = await loop.run_in_executor(None, lambda: session.query(Player).all())
        except Exception as e:
            print(f"DB error loading players: {e}")
            session.close()
            return

        for p in players:
            try:
                await fetch_player_attacks(session, p)
                data = await get_player(p.tag)
                if data:
                    p.current_rank = data[3]
                    if data[4] is not None:
                        p.th_level = data[4]
                await loop.run_in_executor(None, session.commit)
            except Exception as e:
                await loop.run_in_executor(None, session.rollback)
                print(f"Error for {p.tag}: {e}")
            await asyncio.sleep(1.0)

        session.close()

    @refresh_players.before_loop
    async def before_refresh_players(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)

    @tasks.loop(time=dt_time(hour=6, minute=50, tzinfo=WARSAW))
    async def snapshot_ranks(self):
        def _do_snapshot():
            session = Session()
            try:
                players = session.query(Player).all()
                for p in players:
                    if p.current_rank is not None:
                        p.initial_rank = p.current_rank
                session.commit()
            finally:
                session.close()

        await asyncio.get_running_loop().run_in_executor(None, _do_snapshot)
        print("Rank snapshot saved.")

    @snapshot_ranks.before_loop
    async def before_snapshot_ranks(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=12)
    async def refresh_clans(self):
        loop = asyncio.get_running_loop()
        session = Session()
        try:
            clans = await loop.run_in_executor(None, lambda: session.query(Clan).all())
        except Exception as e:
            print(f"DB error loading clans: {e}")
            session.close()
            return

        for clan in clans:
            try:
                members = await get_clan_members(clan.tag)
                for member in members:
                    tag = member if isinstance(member, str) else member["tag"]
                    result = await add_player_to_db(tag, session, commit=False, fetch_attacks=False)
                    if result.get("is_new"):
                        await self._notify_new_player(session, clan.tag, result["name"], result["tag"])
                    await asyncio.sleep(0.5)
            except Exception as e:
                await loop.run_in_executor(None, session.rollback)
                print(f"Error for clan {clan.tag}: {e}")

        await loop.run_in_executor(None, session.commit)
        session.close()

    @refresh_clans.before_loop
    async def before_refresh_clans(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    @tasks.loop(minutes=30)
    async def refresh_wars(self):
        loop = asyncio.get_running_loop()
        session = Session()
        try:
            clan_tags = await loop.run_in_executor(None, lambda: [c.tag for c in session.query(Clan).all()])
        finally:
            session.close()

        for tag in clan_tags:
            session = Session()
            try:
                war_count = await fetch_war_attacks(session, tag)
                cwl_count = await fetch_cwl_attacks(session, tag)
                if war_count or cwl_count:
                    print(f"War attacks saved for {tag}: {war_count} war, {cwl_count} CWL")
            except Exception as e:
                await loop.run_in_executor(None, session.rollback)
                print(f"War fetch error for {tag}: {e}")
            finally:
                session.close()
            await asyncio.sleep(1.0)

    @refresh_wars.before_loop
    async def before_refresh_wars(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)


def setup(bot: discord.Bot):
    bot.add_cog(TasksCog(bot))
