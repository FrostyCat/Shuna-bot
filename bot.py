import asyncio
from sqlite3 import IntegrityError

from discord.ext import tasks

import discord
from discord.ext import commands
from coc_api import get_battlelog, get_player, get_clan, get_clan_members
from db import Session, init_db
from models import Attack
import os
from dotenv import load_dotenv
import json
from discord import app_commands
from models import Player, Clan
from datetime import UTC, datetime, timedelta


async def add_player_to_db(tag: str, session, commit=True):
    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    data = await get_player(tag)
    if not data:
        return {"success": False, "error": "Nie znaleziono gracza"}

    tag_api, name = data

    player = session.query(Player).filter_by(tag=tag_api).first()

    if not player:
        player = Player(tag=tag_api, name=name)
        session.add(player)
    else:
        player.name = name

    if commit:
        session.commit()

    added = await fetch_player_attacks(session, player)

    if commit:
        session.commit()

    return {
        "success": True,
        "name": name,
        "tag": tag_api,
        "added_attacks": added
    }

async def fetch_player_attacks(session, player):
    total_count = 0

    battles = await get_battlelog(player.tag)

    for b in battles:
        if b.get("battleType") != "legend":
            continue

        is_attack = b.get("attack", False)
        stars = b.get("stars", 0)
        destruction = b.get("destructionPercentage", 0)
        trophies = calculate_trophies(stars, destruction)

        if not is_attack:
            trophies = -trophies

        exists = session.query(Attack).filter_by(
            player_id=player.id,
            defender=b.get("opponentPlayerTag"),
            stars=stars,
            destruction=destruction,
            is_attack=is_attack
        ).first()

        if exists:
            continue

        record = Attack(
            player_id=player.id,
            defender=b.get("opponentPlayerTag"),
            stars=stars,
            destruction=destruction,
            trophies=trophies,
            is_attack=is_attack,
            created_at=datetime.now(UTC)
        )

        session.add(record)
        total_count += 1

    return total_count

def calculate_trophies(stars, destruction):
    table = {
        0: [
            (0, 0), (10, 1), (20, 2), (28, 3), (30, 4),
            (37, 5), (40, 6), (46, 7), (50, 8), (53, 9),
            (56, 10), (59, 11), (62, 12), (64, 12),
            (65, 12), (68, 13), (71, 13), (73, 13),
            (74, 13), (77, 13), (80, 13), (82, 14),
            (83, 14), (86, 14), (89, 14), (91, 15),
            (92, 15), (95, 15), (98, 15), (100, 15)
        ],
        1: [
            (10, 5), (19, 6), (20, 7), (28, 8), (30, 9),
            (37, 10), (40, 10), (46, 11), (50, 11),
            (53, 11), (55, 11), (56, 11), (59, 11),
            (62, 12), (64, 12), (65, 12), (68, 12),
            (71, 13), (73, 13), (74, 13), (77, 13),
            (80, 13), (82, 14), (83, 14), (86, 14),
            (89, 14), (91, 15), (92, 15), (95, 15),
            (98, 15), (100, 15)
        ],
        2: [
            (50, 16), (53, 17), (55, 18), (56, 18),
            (59, 19), (62, 20), (64, 20), (65, 21),
            (68, 22), (71, 23), (73, 23), (74, 24),
            (77, 25), (80, 26), (82, 26), (83, 27),
            (86, 28), (89, 29), (91, 29), (92, 30),
            (95, 31), (98, 32), (100, 32)
        ],
        3: [
            (100, 40)
        ]
    }

    thresholds = table.get(stars)
    if not thresholds:
        return 0

    last_value = 0
    for threshold, trophies in thresholds:
        if destruction < threshold:
            return last_value
        last_value = trophies

    return last_value

load_dotenv()

import discord

intents = discord.Intents.default()
intents.message_content = True  # WAŻNE

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    refresh_players.start()
    refresh_clans.start()
    print(f"Zalogowano jako {bot.user}")

## Lista kont
@bot.tree.command(name="players", description="Lista kont")
async def players(interaction: discord.Interaction):
    session = Session()

    players = session.query(Player).all()
    text = "\n".join(p.tag for p in players) or "Brak kont"

    await interaction.response.send_message(text)
    session.close()


@bot.command()
async def fetch(ctx):
    session = Session()

    players = session.query(Player).all()

    if not players:
        await ctx.send("Brak kont w bazie ❗ Dodaj przez /add")
        session.close()
        return

    total_count = 0

    for p in players:
        total_count += await fetch_player_attacks(session, p)

    session.commit()
    session.close()

    await ctx.send(f"Dodano {total_count} nowych ataków 🚀")


def get_day_window(day_offset: int):
    now = datetime.now(UTC)
    if now.hour < 5:
        current_start = (now - timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
    else:
        current_start = now.replace(hour=5, minute=0, second=0, microsecond=0)
    start = current_start + timedelta(days=day_offset)
    end = start + timedelta(days=1)
    return start, end


def build_legend_embed(player, session, day_offset: int):
    start, end = get_day_window(day_offset)

    attacks = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= start,
        Attack.created_at < end,
        Attack.is_attack == True
    ).order_by(Attack.created_at.asc()).all()

    defenses = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= start,
        Attack.created_at < end,
        Attack.is_attack == False
    ).order_by(Attack.created_at.asc()).all()

    last_8 = attacks[::-1][:8]
    total = len(last_8)
    total_trophies = sum(a.trophies for a in last_8)
    avg_stars = sum(a.stars for a in last_8) / total if total else 0

    last_8_defenses = defenses[::-1][:8]
    total_trophies_defenses = sum(d.trophies for d in last_8_defenses)

    if day_offset == 0:
        day_label = "Today"
    elif day_offset == -1:
        day_label = "Yesterday"
    else:
        day_label = f"{abs(day_offset)} days ago"

    attacks_text = "```\n"
    for a in last_8:
        attacks_text += f"{a.defender:<10} {a.stars}⭐ {a.destruction:>3}% {a.trophies:+}\n"
    attacks_text += "```"

    defenses_text = "```\n"
    for d in last_8_defenses:
        defenses_text += f"{d.defender:<10} {d.stars}⭐ {d.destruction:>3}% {d.trophies:+}\n"
    defenses_text += "```"

    embed = discord.Embed(
        title=f"📊 {player.name} ({player.tag}) — {day_label}",
        color=0x8B4513
    )
    embed.add_field(
        name="🏆 Overview",
        value=(
            f"{total}/8 attacks\n"
            f"Avg ⭐: {avg_stars:.2f}\n"
            f"Trophies: {total_trophies:+}\n"
            f"Defenses: {total_trophies_defenses:-}\n"
            f"Gain/Loss: {total_trophies + total_trophies_defenses:+}\n"
        ),
        inline=False
    )
    embed.add_field(name="⚔️ Last Attacks", value=attacks_text, inline=False)
    embed.add_field(name="🛡️ Last Defenses", value=defenses_text, inline=False)
    return embed


class LegendView(discord.ui.View):
    def __init__(self, player_tag: str, day_offset: int = 0):
        super().__init__(timeout=300)
        self.player_tag = player_tag
        self.day_offset = day_offset
        self._update_buttons()

    def _update_buttons(self):
        self.next_day.disabled = self.day_offset >= 0

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_day(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.day_offset -= 1
        self._update_buttons()
        await self._refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_day(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.day_offset += 1
        self._update_buttons()
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction):
        session = Session()
        player = session.query(Player).filter_by(tag=self.player_tag).first()
        embed = build_legend_embed(player, session, self.day_offset)
        session.close()
        await interaction.response.edit_message(embed=embed, view=self)


@bot.tree.command(name="legend", description="Statystyki legendy dla konta")
async def legend(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()

    session = Session()

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    player = session.query(Player).filter_by(tag=tag).first()

    if not player:
        result = await add_player_to_db(tag, session)
        if not result["success"]:
            await interaction.followup.send("❌ " + result["error"])
            session.close()
            return
        player = session.query(Player).filter_by(tag=result["tag"]).first()

    embed = build_legend_embed(player, session, day_offset=0)
    session.close()

    await interaction.followup.send(embed=embed, view=LegendView(player.tag))

@legend.autocomplete("tag")
async def tag_autocomplete(interaction: discord.Interaction, current: str):
    session = Session()

    players = session.query(Player).all()
    current_lower = current.lower()
    choices = [
        app_commands.Choice(
            name=f"{p.name} ({p.tag})",  # <- wyświetlanie nazwy + tagu
            value=p.tag
        )
        for p in players
        if current_lower in p.tag.lower() or current_lower in p.name.lower()
    ]

    session.close()
    return choices[:25]


@bot.tree.command(name="hit_rate", description="Hit rate 3⭐ graczy klanu w legendzie")
@app_commands.describe(tag="Tag klanu, np. #ABC123")
async def hit_rate(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()

    session = Session()

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    clan = session.query(Clan).filter_by(tag=tag).first()
    if not clan:
        data = await get_clan(tag)
        if not data:
            await interaction.followup.send("❌ Klan o podanym tagu nie został znaleziony.")
            session.close()
            return
        clan_tag, name = data
        clan = Clan(tag=clan_tag, name=name)
        session.add(clan)
        session.commit()

    members = await get_clan_members(clan.tag)
    member_tags = {m["tag"] if isinstance(m, dict) else m for m in members}

    rows = []
    for member_tag in member_tags:
        player = session.query(Player).filter_by(tag=member_tag).first()
        if not player:
            continue
        total = session.query(Attack).filter_by(player_id=player.id, is_attack=True).count()
        triples = session.query(Attack).filter_by(player_id=player.id, is_attack=True, stars=3).count()
        if total == 0:
            continue
        rate = triples / total * 100
        rows.append((player.name, triples, total, rate))

    session.close()

    if not rows:
        await interaction.followup.send("Brak danych dla graczy tego klanu.")
        return

    rows.sort(key=lambda r: r[3], reverse=True)

    GOLD   = "\u001b[33m"
    SILVER = "\u001b[37m"
    BRONZE = "\u001b[31m"
    RESET  = "\u001b[0m"
    rank_colors = {1: GOLD, 2: SILVER, 3: BRONZE}

    header = f"{'#':>3}  {'RATE%':>6}  {'HITS':>7}  NAME\n"
    divider = "─" * 34 + "\n"

    text = "```ansi\n" + header + divider
    for i, (name, triples, total, rate) in enumerate(rows, 1):
        fraction = f"{triples}/{total}"
        line = f"{i:>3}.  {rate:>5.1f}%  {fraction:>7}  {name}\n"
        color = rank_colors.get(i, "")
        text += f"{color}{line}{RESET if color else ''}"
    text += "```"

    embed = discord.Embed(title=f"⚔️ Hit rate 3⭐ — {clan.name}", color=0x8B4513)
    embed.add_field(name="\u200b", value=text, inline=False)
    embed.set_footer(text=f"{clan.tag} • {len(rows)} graczy")
    await interaction.followup.send(embed=embed)

@hit_rate.autocomplete("tag")
async def clan_tag_autocomplete(_interaction: discord.Interaction, current: str):
    session = Session()
    clans = session.query(Clan).all()
    current_lower = current.lower()
    choices = [
        app_commands.Choice(name=f"{c.name} ({c.tag})", value=c.tag)
        for c in clans
        if current_lower in c.tag.lower() or current_lower in c.name.lower()
    ]
    session.close()
    return choices[:25]


@tasks.loop(minutes=10)
async def refresh_players():
    session = Session()
    players = session.query(Player).all()
    count = 0
    for p in players:
        tag = p.tag
        try:
            count += await fetch_player_attacks(session, p)
            print(f"Refreshed {p.name} ({tag}), total new attacks: {count}")
        except Exception as e:
            session.rollback()
            print(f"Error for {tag}: {e}")

        await asyncio.sleep(1.0)

    session.commit()
    session.close()
    print(f"Finished refresh cycle, total new attacks: {count}")

@refresh_players.before_loop
async def before_refresh():
    await bot.wait_until_ready()



@bot.tree.command(name="clan_add", description="Dodaje klan do monitorowania")
async def clan_add(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()

    session = Session()

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    data = await get_clan(tag)

    if not data:
        await interaction.followup.send("Klan o podanym tagu nie został znaleziony.")
        session.close()
        return

    clan_tag, name = data

    clan = session.query(Clan).filter_by(tag=clan_tag).first()

    if not clan:
        clan = Clan(tag=clan_tag, name=name)
        session.add(clan)
        message = f"Klan {name} ({clan_tag}) został dodany do monitorowania."
    else:
        clan.name = name
        message = f"Klan {name} ({clan_tag}) już był w bazie — zaktualizowano nazwę."

    session.commit()
    session.close()

    await interaction.followup.send(message)



@tasks.loop(hours=12)
async def refresh_clans():
    session = Session()
    clans = session.query(Clan).all()

    for clan in clans:
        clan_tag = clan.tag
        try:
            members = await get_clan_members(clan_tag)

            for member in members:
                tag = member if isinstance(member, str) else member["tag"]
                await add_player_to_db(tag, session, commit=False)
                await asyncio.sleep(0.5)

        except Exception as e:
            session.rollback()
            print(f"Error for clan {clan_tag}: {e}")

    session.commit()
    session.close()

@refresh_clans.before_loop
async def before_refresh_clans():
    await bot.wait_until_ready()



if __name__ == "__main__":
    init_db()
    bot.run(os.getenv("DISCORD_TOKEN"))