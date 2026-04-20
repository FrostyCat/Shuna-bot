import asyncio
from sqlite3 import IntegrityError
from zoneinfo import ZoneInfo
from sqlalchemy import func

from discord.ext import tasks

import discord
from discord.ext import commands
from coc_api import get_battlelog, get_player, get_clan, get_clan_members, verify_player_token
from db import Session, init_db
from models import Attack
import os
from dotenv import load_dotenv
import json
from discord import app_commands
from models import Player, Clan, DiscordUser
from datetime import UTC, datetime, timedelta, time as dt_time


async def add_player_to_db(tag: str, session, commit=True):
    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    data = await get_player(tag)
    if not data:
        return {"success": False, "error": "Nie znaleziono gracza"}

    tag_api, name, *_ = data

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

        battle_time_str = b.get("battleTime")
        try:
            created_at = datetime.strptime(battle_time_str, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
        except (TypeError, ValueError):
            created_at = datetime.now(UTC)

        record = Attack(
            player_id=player.id,
            defender=b.get("opponentPlayerTag"),
            stars=stars,
            destruction=destruction,
            trophies=trophies,
            is_attack=is_attack,
            created_at=created_at
        )

        session.add(record)
        total_count += 1

    return total_count

def calculate_trophies(stars, destruction):
    if stars == 0:
        return 0
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
    snapshot_ranks.start()
    print(f"Zalogowano jako {bot.user}")



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


WARSAW = ZoneInfo("Europe/Warsaw")

def get_day_window(day_offset: int):
    now = datetime.now(WARSAW)
    if now.hour < 7:
        current_start = (now - timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    else:
        current_start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    start = (current_start + timedelta(days=day_offset)).astimezone(UTC)
    end = start + timedelta(days=1)
    return start, end


def build_legend_embed(player, session, day_offset: int, season_trophies: int | None = None, rank: int | None = None, initial_rank: int | None = None):
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
    net = total_trophies + total_trophies_defenses
    if rank is not None and initial_rank is not None:
        rank_diff = initial_rank - rank
        diff_str = f" ({rank_diff:+})" if rank_diff != 0 else ""
        rank_line = f"Rank: #{rank}{diff_str} (start: #{initial_rank})\n"
    elif rank is not None:
        rank_line = f"Rank: #{rank}\n"
    else:
        rank_line = ""
    trophy_line = f"Current: {season_trophies} 🏆\n" if season_trophies is not None else ""
    reset_line = f"Reset: {season_trophies - net} 🏆\n" if season_trophies is not None else ""
    embed.add_field(
        name="🏆 Overview",
        value=(
            f"{rank_line}"
            f"{trophy_line}"
            f"{reset_line}"
            f"⚔️ {total} / 🛡️ {len(last_8_defenses)}\n"
            f"Avg ⭐: {avg_stars:.2f}\n"
            f"Trophies: {total_trophies:+}\n"
            f"Defenses: {total_trophies_defenses:-}\n"
            f"Net: {net:+}\n"
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


@bot.tree.command(name="legend_day", description="Statystyki legendy dla konta")
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

    player_data = await get_player(player.tag)
    season_trophies = player_data[2] if player_data else None
    rank = player_data[3] if player_data else None

    embed = build_legend_embed(player, session, day_offset=0, season_trophies=season_trophies, rank=rank, initial_rank=player.initial_rank)
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


SEASON_EPOCH = datetime(2026, 4, 20, 7, 0, 0, tzinfo=WARSAW)
SEASON_DURATION = timedelta(days=28)

def get_season_window(season: int):
    start = SEASON_EPOCH - SEASON_DURATION * (season - 1)
    end = start + SEASON_DURATION
    return start, end


@bot.tree.command(name="clan_legend_stats", description="Hit rate 3⭐ graczy klanu w legendzie")
@app_commands.describe(tag="Tag klanu, np. #ABC123", season="Numer sezonu (1=aktualny, 2=poprzedni...). Puste = cała historia")
async def hit_rate(interaction: discord.Interaction, tag: str, season: int | None = None):
    await interaction.response.defer()

    session = Session()

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    clan = session.query(Clan).filter_by(tag=tag).first()
    is_new = not clan
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

    if is_new:
        await interaction.followup.send(f"⏳ Nowy klan — rejestruję {len(member_tags)} graczy...", wait=True)
        for member_tag in member_tags:
            await add_player_to_db(member_tag, session, commit=False)
            await asyncio.sleep(0.3)
        session.commit()

    season_start, season_end = get_season_window(season) if season else (None, None)

    rows = []
    for member_tag in member_tags:
        player = session.query(Player).filter_by(tag=member_tag).first()
        if not player:
            continue

        q = session.query(Attack).filter(Attack.player_id == player.id, Attack.is_attack == True)
        if season_start:
            q = q.filter(Attack.created_at >= season_start, Attack.created_at < season_end)

        total = q.count()
        triples = q.filter(Attack.stars == 3).count()
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

    lines = []
    for i, (name, triples, total, rate) in enumerate(rows, 1):
        fraction = f"{triples}/{total}"
        line = f"{i:>3}.  {rate:>5.1f}%  {fraction:>7}  {name}\n"
        color = rank_colors.get(i, "")
        lines.append(f"{color}{line}{RESET if color else ''}")

    # Dziel na chunki po ~20 wierszy żeby nie przekroczyć limitu 1024 znaków
    CHUNK = 20
    chunks = [lines[i:i + CHUNK] for i in range(0, len(lines), CHUNK)]

    season_label_str = season_label(season) if season else "Cała historia"
    embed = discord.Embed(title=f"⚔️ Hit rate 3⭐ — {clan.name} — {season_label_str}", color=0x8B4513)
    for idx, chunk in enumerate(chunks):
        block = "```ansi\n"
        if idx == 0:
            block += header + divider
        block += "".join(chunk) + "```"
        embed.add_field(name="\u200b", value=block, inline=False)

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

MONTHS_PL = ["Styczeń","Luty","Marzec","Kwiecień","Maj","Czerwiec",
             "Lipiec","Sierpień","Wrzesień","Październik","Listopad","Grudzień"]

def season_label(season: int) -> str:
    end = SEASON_EPOCH - SEASON_DURATION * (season - 1) + SEASON_DURATION
    return f"{MONTHS_PL[end.month - 1]} {end.year}"

@hit_rate.autocomplete("season")
async def season_autocomplete(_interaction: discord.Interaction, current: str):
    session = Session()
    oldest = session.query(func.min(Attack.created_at)).scalar()
    session.close()

    if not oldest:
        return []

    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)

    max_seasons = max(1, int((SEASON_EPOCH - oldest) / SEASON_DURATION) + 2)

    choices = []
    for i in range(1, max_seasons + 1):
        label = season_label(i)
        if not current or current.lower() in label.lower():
            choices.append(app_commands.Choice(name=label, value=i))
    return choices[:25]


@bot.tree.command(name="link", description="Połącz konto Clash of Clans z Discord")
@app_commands.describe(
    tag="Tag gracza, np. #ABC123",
    user="Użytkownik Discord",
    api_token="API token z ustawień konta w grze"
)
async def link(interaction: discord.Interaction, tag: str, user: discord.Member, api_token: str):
    await interaction.response.defer(ephemeral=True)

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    valid = await verify_player_token(tag, api_token)
    if not valid:
        await interaction.followup.send("❌ Nieprawidłowy API token. Sprawdź token w ustawieniach gry.", ephemeral=True)
        return

    session = Session()
    discord_id = str(user.id)

    player = session.query(Player).filter_by(tag=tag).first()
    if not player:
        result = await add_player_to_db(tag, session)
        if not result["success"]:
            await interaction.followup.send("❌ " + result["error"], ephemeral=True)
            session.close()
            return
        player = session.query(Player).filter_by(tag=result["tag"]).first()

    discord_user = session.query(DiscordUser).filter_by(discord_id=discord_id).first()
    if not discord_user:
        discord_user = DiscordUser(discord_id=discord_id)
        session.add(discord_user)
        session.flush()

    player_name = player.name

    if player.discord_user_id == discord_user.id:
        session.close()
        await interaction.followup.send(f"ℹ️ **{player_name}** jest już połączony z kontem {user.mention}.", ephemeral=True)
        return

    player.discord_user_id = discord_user.id
    session.commit()
    session.close()

    await interaction.followup.send(f"✅ Połączono **{player_name}** ({tag}) z kontem {user.mention}.", ephemeral=True)


@link.autocomplete("tag")
async def link_tag_autocomplete(_interaction: discord.Interaction, current: str):
    session = Session()
    players = session.query(Player).all()
    current_lower = current.lower()
    choices = [
        app_commands.Choice(name=f"{p.name} ({p.tag})", value=p.tag)
        for p in players
        if current_lower in p.tag.lower() or current_lower in p.name.lower()
    ]
    session.close()
    return choices[:25]


@bot.tree.command(name="unlink", description="Odłącz konto Clash of Clans od Discord")
@app_commands.describe(
    tag="Tag gracza, np. #ABC123",
    user="Użytkownik Discord",
    api_token="API token z ustawień konta w grze"
)
async def unlink(interaction: discord.Interaction, tag: str, user: discord.Member, api_token: str):
    await interaction.response.defer(ephemeral=True)

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    valid = await verify_player_token(tag, api_token)
    if not valid:
        await interaction.followup.send("❌ Nieprawidłowy API token.", ephemeral=True)
        return

    session = Session()
    player = session.query(Player).filter_by(tag=tag).first()

    if not player or not player.discord_user or player.discord_user.discord_id != str(user.id):
        session.close()
        await interaction.followup.send("❌ To konto nie jest połączone z podanym użytkownikiem.", ephemeral=True)
        return

    player_name = player.name
    player.discord_user_id = None
    session.commit()
    session.close()

    await interaction.followup.send(f"✅ Odłączono **{player_name}** ({tag}) od konta {user.mention}.", ephemeral=True)


@unlink.autocomplete("tag")
async def unlink_tag_autocomplete(_interaction: discord.Interaction, current: str):
    session = Session()
    players = session.query(Player).filter(Player.discord_user_id.isnot(None)).all()
    current_lower = current.lower()
    choices = [
        app_commands.Choice(name=f"{p.name} ({p.tag})", value=p.tag)
        for p in players
        if current_lower in p.tag.lower() or current_lower in p.name.lower()
    ]
    session.close()
    return choices[:25]


@bot.tree.command(name="profile", description="Pokaż konta CoC połączone z użytkownikiem Discord")
@app_commands.describe(user="Użytkownik Discord (domyślnie Ty)")
async def profile(interaction: discord.Interaction, user: discord.Member | None = None):
    target = user or interaction.user
    session = Session()

    discord_user = session.query(DiscordUser).filter_by(discord_id=str(target.id)).first()

    if not discord_user or not discord_user.players:
        await interaction.response.send_message(
            f"❌ {target.mention} nie ma połączonych kont CoC.", ephemeral=True
        )
        session.close()
        return

    lines = []
    for p in discord_user.players:
        tag_encoded = p.tag.replace("#", "%23")
        url = f"https://link.clashofclans.com/en?action=OpenPlayerProfile&tag={tag_encoded}"
        lines.append(f"• [{p.name} ({p.tag})]({url})")

    embed = discord.Embed(
        title=f"Konta CoC — {target.display_name}",
        description="\n".join(lines),
        color=0x8B4513
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    session.close()

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stats_user_legend", description="Statystyki legendy wszystkich kont użytkownika")
@app_commands.describe(user="Użytkownik Discord (domyślnie Ty)")
async def stats_user_legend(interaction: discord.Interaction, user: discord.Member | None = None):
    await interaction.response.defer()

    target = user or interaction.user
    session = Session()

    discord_user = session.query(DiscordUser).filter_by(discord_id=str(target.id)).first()
    if not discord_user or not discord_user.players:
        await interaction.followup.send(f"❌ {target.mention} nie ma połączonych kont CoC.")
        session.close()
        return

    start, end = get_day_window(0)

    rows = []
    for player in discord_user.players:
        player_data = await get_player(player.tag)
        season_trophies = player_data[2] if player_data else None

        attacks = session.query(Attack).filter(
            Attack.player_id == player.id,
            Attack.created_at >= start,
            Attack.created_at < end,
            Attack.is_attack == True
        ).order_by(Attack.created_at.asc()).all()[-8:]

        defenses = session.query(Attack).filter(
            Attack.player_id == player.id,
            Attack.created_at >= start,
            Attack.created_at < end,
            Attack.is_attack == False
        ).order_by(Attack.created_at.asc()).all()[-8:]

        atk_trophies = sum(a.trophies for a in attacks)
        def_trophies = sum(d.trophies for d in defenses)
        net = atk_trophies + def_trophies
        init = (season_trophies - net) if season_trophies is not None else None
        rows.append((player.name, player.tag, atk_trophies, def_trophies, net, init, season_trophies, player.initial_rank, len(attacks), len(defenses)))

    session.close()

    def fmt_name(name, tag):
        clean = ''.join(c for c in name if c.isascii() or '\u0100' <= c <= '\u024F').strip()
        return f"{clean} ({tag})"[:23]

    N, AD, ST, TR, RK = 23, 5, 4, 5, 5

    embed = discord.Embed(
        title=f"📊 Legend Day — {target.display_name}",
        color=0x8B4513
    )

    header = (
        f"`{'Gracz':<{N}}` "
        f"`{'A/D':^{AD}}` "
        f"`{'ATK':>{ST}}` "
        f"`{'DEF':>{ST}}` "
        f"`{'NET':>{ST}}` "
        f"`{'Reset':>{TR}}` "
        f"`{'Curr':>{TR}}` "
        f"`{'Rnk':>{RK}}`"
    )
    embed.add_field(name="", value=header, inline=False)

    for name, tag, atk, deff, net, init, final, rank, atk_n, def_n in rows:
        label     = fmt_name(name, tag)
        init_str  = str(init)  if init  is not None else "—"
        final_str = str(final) if final is not None else "—"
        rank_str  = f"#{rank}" if rank is not None else "—"
        ad_str    = f"{atk_n}/{def_n}"
        row = (
            f"`{label:<{N}}` "
            f"`{ad_str:^{AD}}` "
            f"`{atk:>+{ST}}` "
            f"`{deff:>+{ST}}` "
            f"`{net:>+{ST}}` "
            f"`{init_str:>{TR}}` "
            f"`{final_str:>{TR}}` "
            f"`{rank_str:>{RK}}`"
        )
        embed.add_field(name="", value=row, inline=False)

    await interaction.followup.send(embed=embed)


@tasks.loop(minutes=10)
async def refresh_players():
    session = Session()
    players = session.query(Player).all()
    count = 0
    for p in players:
        tag = p.tag
        try:
            count += await fetch_player_attacks(session, p)
            data = await get_player(tag)
            if data:
                p.current_rank = data[3]
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


@tasks.loop(time=dt_time(hour=6, minute=50, tzinfo=WARSAW))
async def snapshot_ranks():
    session = Session()
    players = session.query(Player).all()
    for p in players:
        if p.current_rank is not None:
            p.initial_rank = p.current_rank
    session.commit()
    session.close()
    print("Rank snapshot saved.")

@snapshot_ranks.before_loop
async def before_snapshot():
    await bot.wait_until_ready()





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