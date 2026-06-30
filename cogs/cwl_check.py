import asyncio
import calendar
from collections import defaultdict
from datetime import datetime, timezone

import discord

from db import Session
from models import Clan, DiscordUser, Player, WarAttack

_LEAGUE_ORDER = {
    "Champion League I":   0,
    "Champion League II":  1,
    "Champion League III": 2,
    "Master League I":     3,
    "Master League II":    4,
    "Master League III":   5,
    "Crystal League I":    6,
    "Crystal League II":   7,
    "Crystal League III":  8,
    "Gold League I":       9,
    "Gold League II":      10,
    "Gold League III":     11,
    "Silver League I":     12,
    "Silver League II":    13,
    "Silver League III":   14,
    "Bronze League I":     15,
    "Bronze League II":    16,
    "Bronze League III":   17,
}

_SPLIT_MONTHS: dict[tuple, list] = {
    (2026, 6): [
        (datetime(2026, 6, 1),  datetime(2026, 6, 14, 23, 59, 59)),
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


def _clean(name: str) -> str:
    return "".join(c for c in name if c.isascii() or "Ā" <= c <= "ɏ").strip() or name


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
            league_map: dict[tuple, str] = {}
            for a in attacks:
                key = (a.attacker_tag, a.clan_tag)
                stars_map[key] += a.stars or 0
                if a.league and key not in league_map:
                    league_map[key] = a.league

            for (tag, clan_tag), stars in stars_map.items():
                if stars >= 21:
                    results.append({
                        "discord_id": discord_by_tag[tag],
                        "player_name": name_by_tag[tag],
                        "clan_tag": clan_tag,
                        "stars": stars,
                        "league": league_map.get((tag, clan_tag), ""),
                        "season_idx": idx + 1 if multi else None,
                    })

        clan_tags = {r["clan_tag"] for r in results}
        clans = session.query(Clan).filter(Clan.tag.in_(clan_tags)).all()
        clan_name_map = {c.tag: c.name for c in clans}
        for r in results:
            r["clan_name"] = clan_name_map.get(r["clan_tag"], r["clan_tag"])

        return results
    finally:
        session.close()


def _league_short(league: str) -> str:
    mapping = {
        "Champion League I":   "Champ I",
        "Champion League II":  "Champ II",
        "Champion League III": "Champ III",
        "Master League I":     "Master I",
        "Master League II":    "Master II",
        "Master League III":   "Master III",
        "Crystal League I":    "Crystal I",
        "Crystal League II":   "Crystal II",
        "Crystal League III":  "Crystal III",
        "Gold League I":       "Gold I",
        "Gold League II":      "Gold II",
        "Gold League III":     "Gold III",
        "Silver League I":     "Silver I",
        "Silver League II":    "Silver II",
        "Silver League III":   "Silver III",
        "Bronze League I":     "Bronze I",
        "Bronze League II":    "Bronze II",
        "Bronze League III":   "Bronze III",
    }
    return mapping.get(league, league[:10] if league else "?")


def _build_embeds(title: str, results: list[dict], member_by_id: dict) -> list[discord.Embed]:
    multi = any(r["season_idx"] is not None for r in results)

    if multi:
        header = f"‎`{'#':>3} {'★':>3} {'League':<10} {'Clan':<16} CWL`  **Player — Discord**"
    else:
        header = f"‎`{'#':>3} {'★':>3} {'League':<10} {'Clan':<16}`  **Player — Discord**"

    lines = [header]
    for i, r in enumerate(results, 1):
        member = member_by_id.get(r["discord_id"])
        discord_name = _clean(member.display_name) if member else r["discord_id"]
        player_name = _clean(r["player_name"])
        clan_name = _clean(r["clan_name"])[:16]
        league_str = _league_short(r.get("league", ""))

        if multi:
            nums = f"{i:>3} {r['stars']:>3} {league_str:<10} {clan_name:<16} CWL{r['season_idx']}"
        else:
            nums = f"{i:>3} {r['stars']:>3} {league_str:<10} {clan_name:<16}"

        lines.append(f"‎`{nums}` ‎{player_name} — {discord_name}")

    embeds = []
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3800:
            e = discord.Embed(color=0x8B4513)
            if not embeds:
                e.title = title
            e.description = chunk
            embeds.append(e)
            chunk = header + "\n" + line
        else:
            chunk += ("\n" if chunk else "") + line

    if chunk:
        e = discord.Embed(color=0x8B4513)
        if not embeds:
            e.title = title
        e.description = chunk
        e.set_footer(text=f"{len(results)} account(s) with 21+ stars")
        embeds.append(e)

    return embeds


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

        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        season_label = f"{month_names[month - 1]} {year}" + (f" — Part {part}" if part else "")

        if not results:
            await ctx.followup.send(f"No members with role {role.mention} have 21+ CWL stars ({season_label}).")
            return

        results.sort(key=lambda r: (
            r["season_idx"] or 0,
            _LEAGUE_ORDER.get(r.get("league", ""), 99),
            _clean(r["clan_name"]),
            _clean(r["player_name"]),
        ))

        title = f"⚔️ CWL 21★ — {role.name} — {season_label}"
        embeds = _build_embeds(title, results, member_by_id)
        await ctx.followup.send(embeds=embeds[:10])


def setup(bot: discord.Bot):
    bot.add_cog(CwlCheckCog(bot))
