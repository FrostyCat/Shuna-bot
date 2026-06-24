import discord

from coc_api import get_player_profile
from db import Session
from models import DiscordUser, Player

HERO_ICONS = {
    "Barbarian King": "👑",
    "Archer Queen": "🏹",
    "Grand Warden": "📚",
    "Royal Champion": "💎",
    "Minion Prince": "👿",
}
HERO_ORDER = ["Barbarian King", "Archer Queen", "Grand Warden", "Royal Champion", "Minion Prince"]

CLAN_ROLES = {
    "leader": "Leader",
    "coLeader": "Co-Leader",
    "admin": "Elder",
    "member": "Member",
}


async def tag_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    current = f"%{ctx.value}%"
    players = (
        session.query(Player)
        .filter(Player.name.ilike(current) | Player.tag.ilike(current))
        .limit(25)
        .all()
    )
    choices = [discord.OptionChoice(name=f"{p.name} ({p.tag})", value=p.tag) for p in players]
    session.close()
    return choices


class PlayerCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="player", description="Clash of Clans player profile")
    async def player(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=tag_autocomplete),
    ):
        await ctx.defer()

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        data = await get_player_profile(tag)
        if not data:
            await ctx.followup.send("❌ Player not found.")
            return

        session = Session()
        player = session.query(Player).filter_by(tag=tag).first()
        discord_user = player.discord_user if player else None
        session.close()

        name = data.get("name", "?")
        th = data.get("townHallLevel", "?")
        trophies = data.get("trophies", 0)
        best = data.get("bestTrophies", 0)
        war_stars = data.get("warStars", 0)
        league_name = data.get("league", {}).get("name", "Unranked")

        legend_stats = data.get("legendStatistics", {})
        legend_trophies = legend_stats.get("legendTrophies")
        current_season = legend_stats.get("currentSeason", {})
        season_trophies = current_season.get("trophies")
        season_rank = current_season.get("rank")

        clan = data.get("clan")
        if clan:
            role = CLAN_ROLES.get(clan.get("role", ""), clan.get("role", ""))
            clan_str = f"{clan['name']} • {role}"
        else:
            clan_str = "—"

        heroes = {h["name"]: h["level"] for h in data.get("heroes", []) if h.get("village") == "home"}
        hero_parts = [
            f"{HERO_ICONS[h]}{heroes[h]}"
            for h in HERO_ORDER
            if h in heroes
        ]
        hero_str = "  ".join(hero_parts) if hero_parts else "—"

        trophy_display = f"{season_trophies} 🏆" if season_trophies else f"{trophies} 🏆"
        rank_display = f"  •  #{season_rank}" if season_rank else ""

        embed = discord.Embed(
            title=f"{name}  ({tag})",
            color=0x8B4513,
        )
        embed.add_field(name="TH", value=str(th), inline=True)
        embed.add_field(name="League", value=league_name, inline=True)
        embed.add_field(name="Trophies", value=f"{trophy_display}{rank_display}", inline=True)
        if legend_trophies:
            embed.add_field(name="Legend Trophies", value=f"{legend_trophies} 🏆", inline=True)
        embed.add_field(name="Best Trophies", value=f"{best} 🏆", inline=True)
        embed.add_field(name="War Stars", value=f"{war_stars} ⭐", inline=True)
        embed.add_field(name="Clan", value=clan_str, inline=False)
        embed.add_field(name="Heroes", value=hero_str, inline=False)

        if discord_user:
            embed.add_field(name="Discord", value=f"<@{discord_user.discord_id}>", inline=False)
        else:
            embed.add_field(name="Discord", value="— *(not linked)*", inline=False)

        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(PlayerCog(bot))
