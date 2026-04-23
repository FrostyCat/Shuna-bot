import discord
from discord.ext import commands
from collections import defaultdict

from db import Session
from models import Attack

# (name, housing_space, exclude_from_category)
TROOP_DATA = {
    # Elixir troops
    0:  ("Barbarian",      1,  True),
    1:  ("Archer",         1,  True),
    2:  ("Giant",          5,  True),
    3:  ("Goblin",         1,  True),
    4:  ("Wall Breaker",   2,  True),
    5:  ("Balloon",        5,  False),
    6:  ("Wizard",         4,  True),
    7:  ("Healer",         14, True),
    8:  ("Dragon",         20, False),
    9:  ("P.E.K.K.A",      25, True),
    10: ("Baby Dragon",    10, True),
    11: ("Miner",          6,  False),
    12: ("Electro Dragon", 30, False),
    13: ("Yeti",           18, False),
    14: ("Dragon Rider",   25, False),
    15: ("Electro Titan",  32, False),
    16: ("Root Rider",     20, False),
    # Dark Elixir troops
    17: ("Minion",         2,  True),
    18: ("Hog Rider",      5,  False),
    19: ("Valkyrie",       8,  True),
    20: ("Golem",          30, True),
    21: ("Witch",          12, True),
    22: ("Lava Hound",     30, True),
    23: ("Bowler",         6,  True),
    24: ("Ice Golem",      15, True),
    25: ("Headhunter",     6,  True),
    26: ("Apprentice Warden", 20, True),
    27: ("Druid",          16, False),
    # Super troops
    50: ("Super Barbarian",    5,  True),
    51: ("Super Archer",       12, False),
    52: ("Sneaky Goblin",      3,  True),
    53: ("Super Wall Breaker", 8,  True),
    54: ("Rocket Balloon",     8,  False),
    55: ("Inferno Dragon",     15, False),
    56: ("Super Witch",        40, False),
    57: ("Ice Hound",          40, True),
    58: ("Super Bowler",       30, False),
    59: ("Super Dragon",       40, False),
    60: ("Super Miner",        24, False),
    61: ("Super Hog Rider",    8,  False),
    62: ("Super Giant",        10, True),
    63: ("Super Valkyrie",     20, True),
    64: ("Super Wizard",       10, True),
    65: ("Super Minion",       12, True),
    66: ("Super Yeti",         35, False),
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

    @discord.slash_command(name="army_stats", description="Overall army composition stats across all players")
    async def army_stats(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        session = Session()
        records = session.query(Attack).filter(Attack.army_share_code.isnot(None)).all()
        session.close()

        if not records:
            await ctx.followup.send("❌ No army data yet. Data is collected from new battles only.")
            return

        attacks = [a for a in records if a.is_attack]
        defenses = [a for a in records if not a.is_attack]

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
                line = f"‎`{nums}` ‎{cat}"
                if sum(len(l) + 1 for l in lines) + len(line) > 1000:
                    lines.append(f"‎*...and {len(rows) - i + 1} more*")
                    break
                lines.append(line)
            return "\n".join(lines), total

        embed = discord.Embed(title="⚔️ Army Stats — All Players", color=0x8B4513)

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
