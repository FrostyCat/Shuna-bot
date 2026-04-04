import asyncio

from discord.ext import tasks

import discord
from discord.ext import commands
from requests import session
from coc_api import get_battlelog, get_player
from db import Session, init_db
from models import Attack
import os
from dotenv import load_dotenv
import json
from discord import app_commands
from models import Player
from datetime import UTC, datetime, timedelta


def fetch_player_attacks(session, player):
    total_count = 0

    battles = get_battlelog(player.tag)

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

init_db()

@bot.event
async def on_ready():
    await bot.tree.sync()
    refresh_players.start()
    print(f"Zalogowano jako {bot.user}")

## Dodaj konto do monitorowania
@bot.tree.command(name="add", description="Dodaj gracza po tagu")
@app_commands.describe(tag="Tag gracza, np. #ABC123")
async def add(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()

    session = Session()

    # normalizacja taga
    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag



    # fetch z API
    data = get_player(tag)

    if not data:
        await interaction.followup.send("❌ Nie znaleziono gracza")
        return

    tag_api, name = data

    # UPSERT do bazy
    player = session.query(Player).filter_by(tag=tag_api).first()

    if not player:
        player = Player(tag=tag_api, name=name)
        session.add(player)
    else:
        player.name = name

    session.commit()

    added = fetch_player_attacks(session, player)
    session.commit()

    await interaction.followup.send(f"✅ Dodano gracza: **{name}**\n📊 Dodano {added} ataków")
    await interaction.followup.send(f"✅ Dodano gracza: **{name}** ({tag_api})")

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
        total_count += fetch_player_attacks(session, p)

    session.commit()
    session.close()

    await ctx.send(f"Dodano {total_count} nowych ataków 🚀")


@bot.tree.command(name="legend", description="Statystyki legendy dla konta")
async def legend(interaction: discord.Interaction, tag: str):
    session = Session()

    tag = tag.upper()

    # 🔍 znajdź gracza
    player = session.query(Player).filter_by(tag=tag).first()

    if not player:
        await interaction.response.send_message("Nie znaleziono gracza ❗")
        session.close()
        return

    from datetime import datetime, UTC, timedelta

    now = datetime.now(UTC)

    # 🧠 legend day reset (5:00 UTC)
    if now.hour < 5:
        start = (now - timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=5, minute=0, second=0, microsecond=0)

    # 📊 pobierz ataki tego gracza
    attacks = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= start,
        Attack.is_attack == True
    ).order_by(Attack.created_at.asc()).all()

    defenses = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= start,
        Attack.is_attack == False
    ).order_by(Attack.created_at.asc()).all()
    


    # 📋 ostatnie 8 (chronologicznie)
    last_8 = attacks[::-1][:8]  # odwróć, żeby pokazać najnowsze jako pierwsze
    total = len(last_8)
    total_trophies = sum(a.trophies for a in last_8)
    avg_stars = sum(a.stars for a in last_8) / total if total else 0

    last_8_defenses = defenses[::-1][:8]
    total_trophies_defenses = sum(d.trophies for d in last_8_defenses)

    
    
    attacks_text = "```\n"
    for a in last_8:
        line = f"{a.defender:<10} {a.stars}⭐ {a.destruction:>3}% {a.trophies:+}"
        attacks_text += line + "\n"
    attacks_text += "```"

    defenses_text = "```\n"
    for d in last_8_defenses:
        line = f"{d.defender:<10} {d.stars}⭐ {d.destruction:>3}% {d.trophies:+}"
        defenses_text += line + "\n"
    defenses_text += "```"

    embed = discord.Embed(
        title=f"📊 {player.name} ({tag})",
        color=0x8B4513
    )

    embed.add_field(
        name="🏆 Overview",
        value=(
            f"{total}/8 attacks\n"
            f"Avg ⭐: {avg_stars:.2f}\n"
            f"Trophies: {total_trophies:+}\n"
            f"Defenses: {total_trophies_defenses:-}\n"
            f"Gain/Loss: {total_trophies+total_trophies_defenses:+}\n"
        ),
        inline=False
    )

    embed.add_field(
        name="⚔️ Last Attacks",
        value=attacks_text,
        inline=False
    )

    embed.add_field(
        name="🛡️ Last Defenses",
        value=defenses_text,
        inline=False
    )

    await interaction.response.send_message(embed=embed)

    session.close()

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


@tasks.loop(minutes=10)
async def refresh_players():
    session = Session()
    players = session.query(Player).all()
    count = 0
    for p in players:
        try:
            count += fetch_player_attacks(session, p)
            print(f"Refreshed {p.name} ({p.tag}), total new attacks: {count}")
        except Exception as e:
            print(f"Error for {p.tag}: {e}")

        await asyncio.sleep(1.0)  # 🔥 KLUCZOWE

    session.commit()
    session.close()
    print(f"Finished refresh cycle, total new attacks: {count}")

@refresh_players.before_loop
async def before_refresh():
    await bot.wait_until_ready()

bot.run(os.getenv("DISCORD_TOKEN"))