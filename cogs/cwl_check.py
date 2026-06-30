import asyncio
import calendar
from collections import defaultdict
from datetime import datetime, timezone

import discord

from db import Session
from models import Clan, DiscordUser, Player, WarAttack

# Months with 2 CWLs: (year, month) -> [(start, end), (start, end)]
_SPLIT_MONTHS: dict[tuple, list] = {
    (2026, 6): [
        (datetime(2026, 6, 1), datetime(2026, 6, 14, 23, 59, 59)),
        (datetime(2026, 6, 15), datetime(2026, 6, 30, 23, 59, 59)),
    ],
}


def _get_seasons(year: int, month: int, part: int | None) -> list[tuple[datetime, datetime]]:
    key = (year, month)
    if key in _SPLIT_MONTHS:
        seasons = _SPLIT_MONTHS[key]
        if part in (1, 2):
            return [seasons[part - 1]]
        return seasons
    last = calendar.monthrange(year, month)[1]
    return [(datetime(year, month, 1), datetime(year, month, last, 23, 59, 59))]


def _query(discord_ids: list[str], seasons: list[tuple]) -> list[dict]:
    session = Session()
    try:
        rows = (
            session.query(DiscordUser, Player)
            .join(Player, Player.discord_user_id == DiscordUser.id)
            .filter(DiscordUser.discord_id.in_(discord_ids))
            .all()
        )
        player_tags = [p.tag for _, p in rows]
        discord_by_tag = {p.tag: du.discord_id for du, p in rows}
        name_by_tag = {p.tag: p.name for _, p in rows}

        if not player_tags:
            return []

        results = []
        multi = len(seasons) > 1

        for idx, (start, end) in enumerate(seasons):
            attacks = (
                session.query(WarAttack)
                .filter(
                    WarAttack.attacker_tag.in_(player_tags),
                    WarAttack.war_type == "cwl",
                    WarAttack.created_at >= start,
                    WarAttack.created_at <= end,
                )
                .all()
            )

            stars_map: dict[tuple, int] = defaultdict(int)
            for a in attacks:
                stars_map[(a.attacker_tag, a.clan_tag)] += a.stars or 0

            for (tag, clan_tag), stars in stars_map.items():
                if stars >= 21:
                    results.append({
                        "discord_id": discord_by_tag[tag],
                        "player_name": name_by_tag[tag],
                        "clan_tag": clan_tag,
                        "stars": stars,
                        "season": f"CWL {idx + 1}" if multi else "CWL",
                    })

        clan_tags = {r["clan_tag"] for r in results}
        clans = session.query(Clan).filter(Clan.tag.in_(clan_tags)).all()
        clan_name_map = {c.tag: c.name for c in clans}
        for r in results:
            r["clan_name"] = clan_name_map.get(r["clan_tag"], r["clan_tag"])

        return results
    finally:
        session.close()


class CwlCheckCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="cwl_check", description="Check who got 21 stars in CWL")
    async def cwl_check(
        self,
        ctx: discord.ApplicationContext,
        role: discord.Role,
        month: discord.Option(int, "Month (1-12), default: current", min_value=1, max_value=12, required=False) = None,
        year: discord.Option(int, "Year, default: current", required=False) = None,
        part: discord.Option(int, "CWL part 1 or 2 (for months with 2 CWLs)", min_value=1, max_value=2, required=False) = None,
    ):
        await ctx.defer()

        now = datetime.now(timezone.utc)
        month = month or now.month
        year = year or now.year

        seasons = _get_seasons(year, month, part)

        members = [m for m in ctx.guild.members if role in m.roles and not m.bot]
        if not members:
            await ctx.followup.send(f"No members with role {role.mention}.")
            return

        discord_ids = [str(m.id) for m in members]
        member_by_id = {str(m.id): m for m in members}

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, _query, discord_ids, seasons)

        season_label = f"{year}-{month:02d}" + (f" część {part}" if part else "")

        if not results:
            await ctx.followup.send(f"No members with role {role.mention} have 21+ CWL stars ({season_label}).")
            return

        results.sort(key=lambda r: (member_by_id.get(r["discord_id"]) or type("", (), {"display_name": ""})()).display_name)

        multi = len(seasons) > 1
        header = f"{'Discord':<20} {'Konto':<16} {'Klan':<20} {'★':>3}" + (f" {'Sezon':>6}" if multi else "")
        sep = "-" * len(header)
        lines = [header, sep]

        for r in results:
            member = member_by_id.get(r["discord_id"])
            discord_name = member.display_name if member else r["discord_id"]
            line = f"{discord_name[:20]:<20} {r['player_name'][:16]:<16} {r['clan_name'][:20]:<20} {r['stars']:>3}"
            if multi:
                line += f" {r['season']:>6}"
            lines.append(line)

        chunks, current = [], ""
        for line in lines:
            if len(current) + len(line) + 1 > 3900:
                chunks.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"CWL 21★ — {role.name} ({season_label})" + (f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""),
                description=f"```\n{chunk}```",
                color=0xFFD700,
            )
            await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(CwlCheckCog(bot))
