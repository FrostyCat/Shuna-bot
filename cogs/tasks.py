import asyncio
import os
from datetime import time as dt_time

import discord
from discord.ext import commands, tasks

from coc_api import get_clan_members, get_player, get_top_clans
from db import Session
from models import Clan, Player, GuildClan, GuildConfig
from helpers import WARSAW, add_player_to_db, fetch_player_attacks, fetch_war_attacks, fetch_cwl_attacks

_NOTIFY_GUILD_ID = os.getenv("NOTIFY_GUILD_ID", "")


class TasksCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._api_sem = asyncio.Semaphore(10)
        self.refresh_players.start()
        self.refresh_clans.start()
        self.snapshot_ranks.start()
        self.refresh_wars.start()
        self.pre_reset_sweep.start()
        self.sync_top_clans.start()

    def cog_unload(self):
        self.refresh_players.cancel()
        self.refresh_clans.cancel()
        self.snapshot_ranks.cancel()
        self.refresh_wars.cancel()
        self.pre_reset_sweep.cancel()
        self.sync_top_clans.cancel()

    async def _refresh_one_player(self, tag: str, sem: asyncio.Semaphore = None, sleep: float = 0.1):
        async with (sem or self._api_sem):
            loop = asyncio.get_running_loop()
            session = Session()
            try:
                data = await get_player(tag)
                if not data:
                    return
                player = await loop.run_in_executor(None, session.query(Player).filter_by(tag=tag).first)
                if not player:
                    return
                player.current_rank = data[3]
                if data[2] is not None:
                    player.season_trophies = data[2]
                if data[4] is not None:
                    player.th_level = data[4]
                if len(data) > 5:
                    player.league_tier = data[5]
                if player.league_tier == "Legend I":
                    await fetch_player_attacks(session, player)
                await loop.run_in_executor(None, session.commit)
            except Exception as e:
                await loop.run_in_executor(None, session.rollback)
                print(f"Error for {tag}: {e}")
            finally:
                await loop.run_in_executor(None, session.close)
            await asyncio.sleep(sleep)

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

    @tasks.loop(hours=4)
    async def refresh_players(self):
        loop = asyncio.get_running_loop()
        session = Session()
        try:
            players = await loop.run_in_executor(None, session.query(Player).all)
            tags = [p.tag for p in players]
        except Exception as e:
            print(f"DB error loading players: {e}")
            return
        finally:
            await loop.run_in_executor(None, session.close)

        import time
        t0 = time.monotonic()
        print(f"[refresh_players] starting {len(tags)} players")
        await asyncio.gather(*[self._refresh_one_player(tag) for tag in tags])
        print(f"[refresh_players] done in {time.monotonic() - t0:.1f}s")

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

    @tasks.loop(time=dt_time(hour=6, minute=55, tzinfo=WARSAW))
    async def pre_reset_sweep(self):
        loop = asyncio.get_running_loop()
        session = Session()
        try:
            players = await loop.run_in_executor(None, session.query(Player).all)
            tags = [p.tag for p in players]
        except Exception as e:
            print(f"[pre_reset_sweep] DB error: {e}")
            return
        finally:
            await loop.run_in_executor(None, session.close)

        sweep_sem = asyncio.Semaphore(5)
        print(f"[pre_reset_sweep] Starting sweep for {len(tags)} players...")
        await asyncio.gather(*[self._refresh_one_player(tag, sem=sweep_sem, sleep=0.2) for tag in tags])
        print("[pre_reset_sweep] Done.")

    @pre_reset_sweep.before_loop
    async def before_pre_reset_sweep(self):
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
                    await asyncio.sleep(0.1)
            except Exception as e:
                await loop.run_in_executor(None, session.rollback)
                print(f"Error for clan {clan.tag}: {e}")

        await loop.run_in_executor(None, session.commit)
        session.close()

    @refresh_clans.before_loop
    async def before_refresh_clans(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    async def _sync_top_clans(self) -> int:
        clans = await get_top_clans(200)
        if not clans:
            return 0
        loop = asyncio.get_running_loop()
        session = Session()
        added = 0
        try:
            def _insert():
                nonlocal added
                for c in clans:
                    tag = c.get("tag")
                    name = c.get("name", "")
                    if not tag:
                        continue
                    existing = session.query(Clan).filter_by(tag=tag).first()
                    if not existing:
                        session.add(Clan(tag=tag, name=name, tracked_since=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
                        added += 1
                    else:
                        existing.name = name
                session.commit()
            await loop.run_in_executor(None, _insert)
        except Exception as e:
            await loop.run_in_executor(None, session.rollback)
            print(f"[sync_top_clans] error: {e}")
        finally:
            session.close()
        return added

    @tasks.loop(hours=168)
    async def sync_top_clans(self):
        added = await self._sync_top_clans()
        print(f"[sync_top_clans] synced top 200 clans, {added} new")

    @sync_top_clans.before_loop
    async def before_sync_top_clans(self):
        await self.bot.wait_until_ready()

    @discord.slash_command(name="import_top_clans", description="Import top 200 global clans into tracking")
    async def import_top_clans(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        added = await self._sync_top_clans()
        await ctx.followup.send(f"✅ Top 200 clans synced. {added} new clans added to tracking.")

    @discord.slash_command(name="db_stats", description="Show database stats: players, clans, attacks")
    async def db_stats(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        loop = asyncio.get_running_loop()
        session = Session()
        try:
            def _query():
                total_players  = session.query(Player).count()
                legend_players = session.query(Player).filter_by(league_tier="Legend I").count()
                total_clans    = session.query(Clan).count()
                from models import Attack
                total_attacks  = session.query(Attack).count()
                return total_players, legend_players, total_clans, total_attacks
            total_players, legend_players, total_clans, total_attacks = await loop.run_in_executor(None, _query)
        finally:
            session.close()

        embed = discord.Embed(title="📊 Database Stats", color=0x8B4513)
        embed.add_field(name="🏰 Clans tracked",   value=str(total_clans),                              inline=True)
        embed.add_field(name="👤 Players tracked", value=f"{total_players:,}",                          inline=True)
        embed.add_field(name="👑 Legend I",         value=f"{legend_players:,} ({legend_players*100//max(total_players,1)}%)", inline=True)
        embed.add_field(name="⚔️ Attacks stored",  value=f"{total_attacks:,}",                          inline=True)
        await ctx.followup.send(embed=embed)

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
