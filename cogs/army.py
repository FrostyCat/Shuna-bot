import discord
from discord.ext import commands
from collections import defaultdict

from db import Session
from models import Attack, Player
from cogs.legend import tag_autocomplete

# (name, housing_space, exclude_from_category)
TROOP_DATA = {
    0:  ("Barbarian",      1,  False),
    1:  ("Archer",         1,  False),
    2:  ("Giant",          5,  False),
    3:  ("Goblin",         1,  False),
    4:  ("Wall Breaker",   2,  False),
    5:  ("Balloon",        5,  False),
    6:  ("Wizard",         4,  False),
    7:  ("Healer",         14, True),   # excluded
    8:  ("Dragon",         20, False),
    9:  ("P.E.K.K.A",      25, False),
    10: ("Baby Dragon",    10, False),
    11: ("Miner",          6,  False),
    12: ("Electro Dragon", 30, False),
    13: ("Yeti",           18, False),
    14: ("Dragon Rider",   25, False),
    15: ("Electro Titan",  30, False),
    16: ("Root Rider",     25, False),
    17: ("Minion",         2,  False),
    18: ("Hog Rider",      6,  False),
    19: ("Valkyrie",       8,  False),
    20: ("Golem",          30, False),
    21: ("Witch",          12, False),
    22: ("Lava Hound",     30, False),
    23: ("Bowler",         6,  False),
    24: ("Ice Golem",      15, False),
    25: ("Headhunter",     6,  False),
    # Super troops
    50: ("Super Barbarian",   1,  False),
    51: ("Super Archer",      1,  False),
    52: ("Sneaky Goblin",     1,  False),
    53: ("Super Wall Breaker",2,  False),
    54: ("Rocket Balloon",    5,  False),
    55: ("Inferno Dragon",    20, False),
    56: ("Super Witch",       12, False),
    57: ("Ice Hound",         30, True),  # similar to healer role
    58: ("Super Bowler",      6,  False),
    59: ("Super Dragon",      20, False),
    60: ("Super Miner",       6,  False),
    61: ("Super Hog Rider",   6,  False),
    62: ("Super Giant",       5,  False),
}

SPELL_DATA = {
    0:  ("Lightning",    2),
    1:  ("Healing",      2),
    2:  ("Rage",         2),
    3:  ("Freeze",       2),
    4:  ("Earthquake",   1),
    5:  ("Haste",        1),
    6:  ("Clone",        3),
    7:  ("Invisibility", 1),
    8:  ("Recall",       2),
    9:  ("Bat",          1),
    10: ("Skeleton",     1),
}


def parse_army_code(code: str) -> dict[str, int]:
    if not code:
        return {}

    troops: dict[str, int] = {}

    if "s" in code:
        troops_str, _ = code.split("s", 1)
    else:
        troops_str = code

    troops_str = troops_str.lstrip("u")

    for part in troops_str.split("-"):
        if "x" not in part:
            continue
        count_str, uid_str = part.split("x", 1)
        try:
            count = int(count_str)
            uid = int(uid_str)
        except ValueError:
            continue
        name, space, excluded = TROOP_DATA.get(uid, (f"Troop#{uid}", 1, False))
        if not excluded:
            troops[name] = troops.get(name, 0) + count * space

    return troops


def categorize(code: str) -> str:
    troops = parse_army_code(code)
    if not troops:
        return "Unknown"
    return max(troops, key=troops.get)


class ArmyCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="army_stats", description="Attack category stats based on army composition")
    async def army_stats(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=tag_autocomplete),
    ):
        await ctx.defer()

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        session = Session()
        player = session.query(Player).filter_by(tag=tag).first()
        if not player:
            await ctx.followup.send("❌ Player not found in database.")
            session.close()
            return

        records = session.query(Attack).filter(
            Attack.player_id == player.id,
            Attack.army_share_code.isnot(None),
        ).all()
        session.close()

        attacks = [a for a in records if a.is_attack]
        defenses = [a for a in records if not a.is_attack]

        if not records:
            await ctx.followup.send("❌ No data with army codes found. Data is collected from new battles only.")
            return

        def build_rows(entries):
            counts: dict[str, int] = defaultdict(int)
            stars: dict[str, int] = defaultdict(int)
            trophies: dict[str, int] = defaultdict(int)
            for a in entries:
                cat = categorize(a.army_share_code)
                counts[cat] += 1
                stars[cat] += a.stars
                trophies[cat] += a.trophies
            total = len(entries)
            rows = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            header = f"‎`{'#':>3} {'COUNT':>6} {'%':>5} {'AVG⭐':>6} {'AVG+':>5} `  **CATEGORY**"
            lines = [header]
            for i, (cat, count) in enumerate(rows, 1):
                pct = count / total * 100
                avg_stars = stars[cat] / count
                avg_trophies = trophies[cat] / count
                nums = f"{i:>3} {count:>6} {pct:>4.1f}% {avg_stars:>5.2f}⭐ {avg_trophies:>+5.1f} "
                lines.append(f"‎`{nums}` ‎{cat}")
            return "\n".join(lines), total

        embed = discord.Embed(
            title=f"⚔️ Army Stats — {player.name}",
            color=0x8B4513,
        )

        if attacks:
            atk_text, atk_total = build_rows(attacks)
            embed.add_field(name=f"⚔️ Attacks ({atk_total})", value=atk_text, inline=False)

        if defenses:
            def_text, def_total = build_rows(defenses)
            embed.add_field(name=f"🛡️ Defenses ({def_total})", value=def_text, inline=False)

        embed.set_footer(text=f"{len(records)} total entries with army data")
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(ArmyCog(bot))
