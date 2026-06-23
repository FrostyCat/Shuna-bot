import re
import discord
from collections import defaultdict
from urllib.parse import urlparse, parse_qs

from db import Session
from models import Attack

# (name, housing_space, exclude_from_category)
# IDs from clashofclans.js raw.json — match CoC army link format
TROOP_DATA = {
    # Elixir troops
    0:   ("Barbarian",          1,   True),
    1:   ("Archer",             1,   True),
    3:   ("Giant",              5,   True),
    2:   ("Goblin",             1,   True),
    4:   ("Wall Breaker",       2,   True),
    5:   ("Balloon",            5,   False),
    6:   ("Wizard",             4,   True),
    7:   ("Healer",             14,  True),
    8:   ("Dragon",             20,  False),
    9:   ("P.E.K.K.A",          25,  True),
    23:  ("Baby Dragon",        10,  True),
    24:  ("Miner",              6,   False),
    59:  ("Electro Dragon",     30,  False),
    53:  ("Yeti",               18,  False),
    65:  ("Dragon Rider",       25,  False),
    95:  ("Electro Titan",      32,  False),
    110: ("Root Rider",         20,  False),
    132: ("Thrower",            15,  False),
    177: ("Meteor Golem",       30,  False),
    # Dark Elixir troops
    10:  ("Minion",             2,   True),
    11:  ("Hog Rider",          5,   False),
    12:  ("Valkyrie",           8,   True),
    13:  ("Golem",              30,  True),
    15:  ("Witch",              12,  True),
    17:  ("Lava Hound",         30,  True),
    22:  ("Bowler",             6,   True),
    58:  ("Ice Golem",          15,  True),
    82:  ("Headhunter",         6,   True),
    97:  ("Apprentice Warden",  20,  True),
    123: ("Druid",              16,  False),
    150: ("Furnace",            16,  False),
    # Super troops
    26:  ("Super Barbarian",    5,   True),
    27:  ("Super Archer",       12,  False),
    29:  ("Super Giant",        10,  True),
    55:  ("Sneaky Goblin",      3,   True),
    28:  ("Super Wall Breaker", 8,   True),
    57:  ("Rocket Balloon",     8,   False),
    83:  ("Super Wizard",       10,  True),
    81:  ("Super Dragon",       40,  False),
    63:  ("Inferno Dragon",     15,  False),
    56:  ("Super Miner",        24,  False),
    147: ("Super Yeti",         35,  False),
    84:  ("Super Minion",       12,  True),
    98:  ("Super Hog Rider",    8,   False),
    64:  ("Super Valkyrie",     20,  True),
    66:  ("Super Witch",        40,  False),
    76:  ("Ice Hound",          40,  True),
    80:  ("Super Bowler",       30,  False),
    # Siege machines
    51:  ("Wall Wrecker",       1,   False),
    52:  ("Battle Blimp",       1,   False),
    62:  ("Stone Slammer",      1,   False),
    75:  ("Siege Barracks",     1,   False),
    87:  ("Log Launcher",       1,   False),
    91:  ("Flame Flinger",      1,   False),
    92:  ("Battle Drill",       1,   False),
    135: ("Troop Launcher",     1,   False),
    188: ("Sky Wagon",          1,   False),
}

SPELL_DATA = {
    0:   ("Lightning",     2),
    1:   ("Healing",       2),
    2:   ("Rage",          2),
    3:   ("Jump",          2),
    5:   ("Freeze",        1),
    16:  ("Clone",         3),
    35:  ("Invisibility",  1),
    53:  ("Recall",        2),
    98:  ("Revive",        2),
    120: ("Totem",         2),
    9:   ("Poison",        1),
    10:  ("Earthquake",    1),
    11:  ("Haste",         1),
    17:  ("Skeleton",      1),
    28:  ("Bat",           1),
    70:  ("Overgrowth",    2),
    109: ("Ice Block",     1),
}


def parse_army_link(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """
    Accepts either a full army link URL or just the army code string.
    Returns (troops, spells) where each is a list of (count, name) tuples.
    """
    if text.startswith("http"):
        qs = parse_qs(urlparse(text).query)
        army_str = qs.get("army", [""])[0]
    else:
        army_str = text

    if not army_str:
        return [], []

    troops: list[tuple[int, str]] = []
    spells: list[tuple[int, str]] = []

    for section_type, section_content in re.findall(r"([us])([\dx\-]+)", army_str):
        for entry in section_content.split("-"):
            if "x" not in entry:
                continue
            try:
                qty, uid = map(int, entry.split("x", 1))
            except ValueError:
                continue
            if section_type == "u":
                name = TROOP_DATA.get(uid, (f"Troop#{uid}", 1, False))[0]
                troops.append((qty, name))
            else:
                name = SPELL_DATA.get(uid, (f"Spell#{uid}", 1))[0]
                spells.append((qty, name))

    return troops, spells


def parse_army_code(code: str) -> dict[str, int]:
    """Returns {category_name: total_housing_space} for the army (troops only)."""
    troops, _ = parse_army_link(code)
    result: dict[str, int] = {}
    for qty, name in troops:
        uid = next((k for k, v in TROOP_DATA.items() if v[0] == name), None)
        if uid is None:
            continue
        _, space, excluded = TROOP_DATA[uid]
        if not excluded:
            result[name] = result.get(name, 0) + qty * space
    return result


def categorize(code: str) -> str:
    troops = parse_army_code(code)
    if not troops:
        return "Unknown"
    return max(troops, key=troops.get)


class ArmyCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="army", description="Show army composition from a CoC army link")
    async def army(
        self,
        ctx: discord.ApplicationContext,
        link: discord.Option(str, "CoC army link or army code"),
    ):
        await ctx.defer()

        troops, spells = parse_army_link(link.strip())
        if not troops and not spells:
            await ctx.followup.send("❌ Could not parse this army link.")
            return

        embed = discord.Embed(title="⚔️ Army Composition", color=0x8B4513)

        if troops:
            troop_lines = [f"`{qty}x` {name}" for qty, name in troops]
            embed.add_field(name="🗡️ Troops", value="\n".join(troop_lines), inline=True)

        if spells:
            spell_lines = [f"`{qty}x` {name}" for qty, name in spells]
            embed.add_field(name="🧪 Spells", value="\n".join(spell_lines), inline=True)

        await ctx.followup.send(embed=embed)

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
