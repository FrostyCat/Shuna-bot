import asyncio

import discord

from db import Session
from models import Attack, Clan, Player
from helpers import sync_top_clans


class TasksCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="import_top_clans", description="Import top 200 global clans into tracking")
    async def import_top_clans(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        added = await sync_top_clans()
        await ctx.followup.send(f"✅ Top 200 clans synced. {added} new clans added to tracking.")

    @discord.slash_command(name="db_stats", description="Show database stats: players, clans, attacks")
    async def db_stats(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        session = Session()
        try:
            def _query():
                total_players  = session.query(Player).count()
                legend_players = session.query(Player).filter_by(league_tier="Legend I").count()
                total_clans    = session.query(Clan).count()
                total_attacks  = session.query(Attack).count()
                return total_players, legend_players, total_clans, total_attacks
            total_players, legend_players, total_clans, total_attacks = await asyncio.to_thread(_query)
        finally:
            session.close()

        embed = discord.Embed(title="📊 Database Stats", color=0x8B4513)
        embed.add_field(name="🏰 Clans tracked",   value=str(total_clans),                              inline=True)
        embed.add_field(name="👤 Players tracked", value=f"{total_players:,}",                          inline=True)
        embed.add_field(name="👑 Legend I",         value=f"{legend_players:,} ({legend_players*100//max(total_players,1)}%)", inline=True)
        embed.add_field(name="⚔️ Attacks stored",  value=f"{total_attacks:,}",                          inline=True)
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(TasksCog(bot))
