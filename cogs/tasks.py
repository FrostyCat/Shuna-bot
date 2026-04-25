import asyncio
from datetime import time as dt_time

import discord
from discord.ext import commands, tasks

from coc_api import get_clan_members, get_player
from db import Session
from models import Clan, Player
from helpers import WARSAW, add_player_to_db, fetch_player_attacks, fetch_war_attacks, fetch_cwl_attacks


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

    @tasks.loop(minutes=10)
    async def refresh_players(self):
        session = Session()
        players = session.query(Player).all()
        session.close()

        for p in players:
            session = Session()
            try:
                await fetch_player_attacks(session, p)
                data = await get_player(p.tag)
                if data:
                    p.current_rank = data[3]
                session.commit()
            except Exception as e:
                session.rollback()
                print(f"Error for {p.tag}: {e}")
            finally:
                session.close()
            await asyncio.sleep(1.0)

    @refresh_players.before_loop
    async def before_refresh_players(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)

    @tasks.loop(time=dt_time(hour=6, minute=50, tzinfo=WARSAW))
    async def snapshot_ranks(self):
        session = Session()
        players = session.query(Player).all()
        for p in players:
            if p.current_rank is not None:
                p.initial_rank = p.current_rank
        session.commit()
        session.close()
        print("Rank snapshot saved.")

    @snapshot_ranks.before_loop
    async def before_snapshot_ranks(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=12)
    async def refresh_clans(self):
        session = Session()
        clans = session.query(Clan).all()
        for clan in clans:
            try:
                members = await get_clan_members(clan.tag)
                for member in members:
                    tag = member if isinstance(member, str) else member["tag"]
                    await add_player_to_db(tag, session, commit=False)
                    await asyncio.sleep(0.5)
            except Exception as e:
                session.rollback()
                print(f"Error for clan {clan.tag}: {e}")
        session.commit()
        session.close()

    @refresh_clans.before_loop
    async def before_refresh_clans(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    @tasks.loop(minutes=30)
    async def refresh_wars(self):
        session = Session()
        clans = session.query(Clan).all()
        clan_tags = [c.tag for c in clans]
        session.close()

        for tag in clan_tags:
            session = Session()
            try:
                war_count = await fetch_war_attacks(session, tag)
                cwl_count = await fetch_cwl_attacks(session, tag)
                if war_count or cwl_count:
                    print(f"War attacks saved for {tag}: {war_count} war, {cwl_count} CWL")
            except Exception as e:
                session.rollback()
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
