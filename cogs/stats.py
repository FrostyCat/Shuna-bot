import asyncio
from datetime import datetime, timedelta, timezone, time as dt_time
from io import BytesIO

import discord
from discord.ext import tasks

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

from coc_api import get_top_players
from db import Session
from helpers import WARSAW
from models import Attack, GuildConfig, Player
from cogs.army import categorize_ml as categorize


def _build_single_chart(data: list, title: str, date_label: str) -> BytesIO:
    """Build a chart for one group (Top 200 or Others) and return as BytesIO."""
    fig, ax = plt.subplots(figsize=(12, 8))
    fig.patch.set_facecolor('#2b2d31')
    ax.set_facecolor('#383a40')
    ax.set_title(f'{title}\n{date_label}', color='white', fontsize=13, fontweight='bold', pad=12)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    for spine in ('bottom', 'left'):
        ax.spines[spine].set_color('#4e5058')

    data = [d for d in data if d[0] not in ('Unknown', 'Other')]
    if not data:
        ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                color='#b5bac1', transform=ax.transAxes, fontsize=13)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        total  = sum(d[1] for d in data)
        data   = sorted(data, key=lambda x: x[1], reverse=True)[:15]
        cats   = [d[0] for d in data]
        counts = [d[1] for d in data]
        pcts   = [c / total * 100 for c in counts]
        stars  = [d[2] for d in data]
        tri    = [d[3] for d in data]

        colors = [
            '#57f287' if s >= 2.7 else
            '#fee75c' if s >= 2.3 else
            '#ed4245'
            for s in stars
        ]

        ax.barh(range(len(cats)), pcts, color=colors,
                edgecolor='#1e1f22', linewidth=0.5, height=0.7)
        ax.set_yticks(range(len(cats)))
        ax.set_yticklabels(cats, color='white', fontsize=10)
        ax.set_xlabel('% of attacks', color='#b5bac1', fontsize=10)
        ax.tick_params(colors='#b5bac1', length=0)
        ax.invert_yaxis()
        ax.set_xlim(0, max(pcts) * 1.75)

        for i, (pct, n, s, t) in enumerate(zip(pcts, counts, stars, tri)):
            ax.text(pct + 0.3, i, f'{pct:.1f}%  ({n})   avg {s:.2f}*   3* {t:.0f}%',
                    va='center', color='#dcddde', fontsize=9)

        patches = [
            mpatches.Patch(color='#57f287', label='avg >= 2.70*'),
            mpatches.Patch(color='#fee75c', label='avg >= 2.30*'),
            mpatches.Patch(color='#ed4245', label='avg < 2.30*'),
        ]
        ax.legend(handles=patches, loc='lower right', fontsize=9,
                  facecolor='#2b2d31', edgecolor='#4e5058',
                  labelcolor='white', framealpha=0.8)

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=130, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


async def _collect_stats(top200_tags: set[str]):
    loop   = asyncio.get_running_loop()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    def _query():
        session = Session()
        try:
            rows = (
                session.query(Attack, Player)
                .join(Player, Attack.player_id == Player.id)
                .filter(
                    Attack.is_attack == True,
                    Attack.army_share_code.isnot(None),
                    Attack.created_at >= cutoff,
                )
                .all()
            )
            return [(a.army_share_code, a.stars or 0, p.tag) for a, p in rows]
        finally:
            session.close()

    rows = await loop.run_in_executor(None, _query)

    # {cat: [count, stars_sum, three_star_count]}
    top200: dict[str, list] = {}
    others: dict[str, list] = {}

    for code, stars, tag in rows:
        cat    = categorize(code)
        bucket = top200 if tag in top200_tags else others
        if cat not in bucket:
            bucket[cat] = [0, 0, 0]
        bucket[cat][0] += 1
        bucket[cat][1] += stars
        bucket[cat][2] += 1 if stars == 3 else 0

    def to_list(d):
        return [
            (cat, v[0], v[1] / v[0], v[2] / v[0] * 100)
            for cat, v in d.items()
        ]

    return to_list(top200), to_list(others)


class StatsCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.daily_stats.start()

    def cog_unload(self):
        self.daily_stats.cancel()

    async def _post_stats(self, ctx=None):
        if not HAS_MATPLOTLIB:
            msg = "❌ matplotlib is not installed. Run: `pip install matplotlib`"
            if ctx:
                await ctx.followup.send(msg)
            else:
                print(f"[stats] {msg}")
            return

        print("[stats] fetching top 200 players...")
        top_players = await get_top_players(200)
        top200_tags = {p["tag"] for p in top_players}
        print(f"[stats] got {len(top200_tags)} top players")

        print("[stats] collecting attack stats...")
        top200_data, other_data = await _collect_stats(top200_tags)
        print(f"[stats] top200={len(top200_data)} categories, others={len(other_data)} categories")

        date_label = datetime.now(WARSAW).strftime('%Y-%m-%d')
        n_top = sum(d[1] for d in top200_data) if top200_data else 0
        n_oth = sum(d[1] for d in other_data)  if other_data  else 0

        print("[stats] building charts...")
        loop = asyncio.get_running_loop()
        buf_top = await loop.run_in_executor(
            None, _build_single_chart, top200_data,
            f"Top 200 Global  ({n_top:,} attacks)", date_label
        )
        buf_oth = await loop.run_in_executor(
            None, _build_single_chart, other_data,
            f"Others  ({n_oth:,} attacks)", date_label
        )
        print("[stats] charts built")

        session = Session()
        try:
            configs = await loop.run_in_executor(
                None,
                lambda: session.query(GuildConfig)
                               .filter(GuildConfig.stats_channel_id.isnot(None))
                               .all()
            )
        finally:
            session.close()

        sent = 0
        for config in configs:
            ch = self.bot.get_channel(int(config.stats_channel_id))
            if not ch:
                continue
            try:
                buf_top.seek(0)
                await ch.send(file=discord.File(buf_top, filename=f"top200_{date_label}.png"))
                buf_oth.seek(0)
                await ch.send(file=discord.File(buf_oth, filename=f"others_{date_label}.png"))
                sent += 1
            except Exception as e:
                print(f"[stats] failed to post to guild {config.guild_id}: {e}")

        if ctx:
            await ctx.followup.send(f"✅ Stats posted to {sent} channel(s).")

    @tasks.loop(time=dt_time(hour=7, minute=5, tzinfo=WARSAW))
    async def daily_stats(self):
        print("[stats] posting daily army stats...")
        await self._post_stats()

    @daily_stats.before_loop
    async def before_daily_stats(self):
        await self.bot.wait_until_ready()

    @discord.slash_command(name="set_stats_channel", description="Set channel for daily Legend League army stats")
    async def set_stats_channel(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        loop    = asyncio.get_running_loop()
        session = Session()
        try:
            def _set():
                config = session.query(GuildConfig).filter_by(guild_id=str(ctx.guild_id)).first()
                if not config:
                    config = GuildConfig(guild_id=str(ctx.guild_id))
                    session.add(config)
                config.stats_channel_id = str(ctx.channel_id)
                session.commit()
            await loop.run_in_executor(None, _set)
        finally:
            session.close()
        await ctx.followup.send(
            f"✅ Daily army stats will be posted in <#{ctx.channel_id}>.", ephemeral=True
        )

    @discord.slash_command(name="stats_now", description="Post Legend League army stats now")
    async def stats_now(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        try:
            await self._post_stats(ctx=ctx)
        except Exception as e:
            import traceback
            traceback.print_exc()
            await ctx.followup.send(f"❌ Error: `{e}`")


def setup(bot: discord.Bot):
    bot.add_cog(StatsCog(bot))
