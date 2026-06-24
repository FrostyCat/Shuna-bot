import asyncio
from datetime import datetime, timedelta, timezone, time as dt_time
from io import BytesIO

import discord
from discord.ext import tasks

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

from coc_api import get_top_players
from db import Session
from helpers import WARSAW
from models import Attack, GuildConfig, Player
from cogs.army import categorize


def _build_chart(top200_data: list, other_data: list, date_label: str) -> BytesIO:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 9))
    fig.patch.set_facecolor('#2b2d31')

    def draw(ax, data, title):
        ax.set_facecolor('#383a40')
        ax.set_title(title, color='white', fontsize=13, fontweight='bold', pad=12)
        for spine in ('top', 'right'):
            ax.spines[spine].set_visible(False)
        for spine in ('bottom', 'left'):
            ax.spines[spine].set_color('#4e5058')

        if not data:
            ax.text(0.5, 0.5, 'Brak danych', ha='center', va='center',
                    color='#b5bac1', transform=ax.transAxes, fontsize=13)
            ax.set_xticks([])
            ax.set_yticks([])
            return

        total = sum(c for _, c, _ in data)
        data  = sorted(data, key=lambda x: x[1], reverse=True)[:15]
        cats   = [d[0] for d in data]
        counts = [d[1] for d in data]
        pcts   = [c / total * 100 for c in counts]
        stars  = [d[2] for d in data]

        colors = [
            '#57f287' if s >= 2.7 else
            '#fee75c' if s >= 2.3 else
            '#ed4245'
            for s in stars
        ]

        bars = ax.barh(range(len(cats)), pcts, color=colors,
                       edgecolor='#1e1f22', linewidth=0.5, height=0.7)
        ax.set_yticks(range(len(cats)))
        ax.set_yticklabels(cats, color='white', fontsize=9)
        ax.set_xlabel('% ataków', color='#b5bac1', fontsize=10)
        ax.tick_params(colors='#b5bac1', length=0)
        ax.invert_yaxis()
        ax.set_xlim(0, max(pcts) * 1.65)

        for i, (pct, n, s) in enumerate(zip(pcts, counts, stars)):
            ax.text(pct + 0.4, i, f'{pct:.1f}%  ({n})  {s:.2f}⭐',
                    va='center', color='#dcddde', fontsize=8.5)

    n_top = sum(c for _, c, _ in top200_data) if top200_data else 0
    n_oth = sum(c for _, c, _ in other_data)  if other_data  else 0
    draw(ax1, top200_data, f'🏆 Top 200 Global  ({n_top} ataków)')
    draw(ax2, other_data,  f'👥 Pozostali  ({n_oth} ataków)')

    fig.suptitle(f'Legend League — Statystyki Armii  {date_label}',
                 color='white', fontsize=15, fontweight='bold', y=1.01)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


async def _collect_stats(top200_tags: set[str]):
    loop    = asyncio.get_running_loop()
    cutoff  = datetime.now(timezone.utc) - timedelta(hours=24)

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

    top200: dict[str, list] = {}
    others: dict[str, list] = {}

    for code, stars, tag in rows:
        cat    = categorize(code)
        bucket = top200 if tag in top200_tags else others
        if cat not in bucket:
            bucket[cat] = [0, 0]
        bucket[cat][0] += 1
        bucket[cat][1] += stars

    def to_list(d):
        return [(cat, v[0], v[1] / v[0]) for cat, v in d.items()]

    return to_list(top200), to_list(others)


class StatsCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.daily_stats.start()

    def cog_unload(self):
        self.daily_stats.cancel()

    async def _post_stats(self, ctx=None):
        if not HAS_MATPLOTLIB:
            msg = "❌ matplotlib nie jest zainstalowane. Uruchom: `pip install matplotlib`"
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
        print("[stats] building chart...")
        loop = asyncio.get_running_loop()
        buf  = await loop.run_in_executor(
            None, _build_chart, top200_data, other_data, date_label
        )
        print("[stats] chart built")

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
            buf.seek(0)
            try:
                await ch.send(file=discord.File(buf, filename=f"legend_stats_{date_label}.png"))
                sent += 1
            except Exception as e:
                print(f"[stats] failed to post to guild {config.guild_id}: {e}")

        if ctx:
            await ctx.followup.send(f"✅ Statystyki wysłane na {sent} kanał(ów).")

    @tasks.loop(time=dt_time(hour=7, minute=5, tzinfo=WARSAW))
    async def daily_stats(self):
        print("[stats] posting daily army stats...")
        await self._post_stats()

    @daily_stats.before_loop
    async def before_daily_stats(self):
        await self.bot.wait_until_ready()

    @discord.slash_command(name="set_stats_channel", description="Ustaw kanał na codzienne statystyki armii Legend League")
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
            f"✅ Codzienne statystyki armii będą wysyłane na <#{ctx.channel_id}>.", ephemeral=True
        )

    @discord.slash_command(name="stats_now", description="Wyślij statystyki armii Legend League teraz")
    async def stats_now(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        try:
            await self._post_stats(ctx=ctx)
        except Exception as e:
            import traceback
            traceback.print_exc()
            await ctx.followup.send(f"❌ Błąd: `{e}`")


def setup(bot: discord.Bot):
    bot.add_cog(StatsCog(bot))
