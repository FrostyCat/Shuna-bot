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
        return {"success": False, "error": "Player not found"}

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

        battle_time_str = b.get("battleTime")
        try:
            created_at = datetime.strptime(battle_time_str, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
        except (TypeError, ValueError):
            created_at = datetime.now(UTC)

        exists = session.query(Attack).filter_by(
            player_id=player.id,
            defender=b.get("opponentPlayerTag"),
            stars=stars,
            destruction=destruction,
            is_attack=is_attack,
        ).first()

        if exists:
            if abs((exists.created_at - created_at).total_seconds()) > 3600:
                exists.created_at = created_at
            continue

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
    if stars == 1:
        return min(15, 5 + destruction // 9)
    if stars == 2:
        if destruction < 50:
            return 0
        return min(32, 16 + (destruction - 50) // 3)
    if stars == 3:
        return 40
    return 0

load_dotenv()

import discord

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    refresh_players.start()
    refresh_clans.start()
    snapshot_ranks.start()
    print(f"Logged in as {bot.user}")



@bot.command()
async def fetch(ctx):
    session = Session()

    players = session.query(Player).all()

    if not players:
        await ctx.send("No accounts in database ❗ Add via /legend_day")
        session.close()
        return

    total_count = 0

    for p in players:
        total_count += await fetch_player_attacks(session, p)

    session.commit()
    session.close()

    await ctx.send(f"Added {total_count} new attacks 🚀")


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


@bot.tree.command(name="legend_day", description="Legend league stats for a player")
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
            name=f"{p.name} ({p.tag})",
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


@bot.tree.command(name="legend_stats_clan", description="3⭐ hit rate for clan members in legend league")
@app_commands.describe(tag="Clan tag, e.g. #ABC123", season="Season number (1=current, 2=previous...). Empty = all time")
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
            await interaction.followup.send("❌ Clan with the given tag was not found.")
            session.close()
            return
        clan_tag, name = data
        clan = Clan(tag=clan_tag, name=name)
        session.add(clan)
        session.commit()

    members = await get_clan_members(clan.tag)
    member_tags = {m["tag"] if isinstance(m, dict) else m for m in members}

    if is_new:
        await interaction.followup.send(f"⏳ New clan — registering {len(member_tags)} members...", wait=True)
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
        await interaction.followup.send("No data for this clan's members.")
        return

    rows.sort(key=lambda r: r[3], reverse=True)

    season_label_str = season_label(season) if season else "All time"
    header = f"‎`{'#':>3} {'RATE':>6} {'HITS':>7} `  **NAME**"
    lines = [header]
    for i, (name, triples, total, rate) in enumerate(rows, 1):
        fraction = f"{triples}/{total}"
        nums = f"{i:>3} {rate:>5.1f}% {fraction:>7} "
        clean_name = ''.join(c for c in name if c.isascii() or 'Ā' <= c <= 'ɏ').strip() or name
        lines.append(f"‎`{nums}` ‎{clean_name}")

    embed = discord.Embed(title=f"⚔️ Legend Stats — {clan.name} — {season_label_str}", color=0x8B4513)
    desc = "\n".join(lines)
    if len(desc) <= 4000:
        embed.description = desc
    else:
        block = header
        for line in lines[1:]:
            if len(block) + len(line) + 1 > 1024:
                embed.add_field(name="", value=block, inline=False)
                block = line
            else:
                block += "\n" + line
        if block:
            embed.add_field(name="", value=block, inline=False)

    embed.set_footer(text=f"{clan.tag} • {len(rows)} players")
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

MONTHS_EN = ["January","February","March","April","May","June",
             "July","August","September","October","November","December"]

def season_label(season: int) -> str:
    end = SEASON_EPOCH - SEASON_DURATION * (season - 1) + SEASON_DURATION
    return f"{MONTHS_EN[end.month - 1]} {end.year}"

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


@bot.tree.command(name="link", description="Link a Clash of Clans account to Discord")
@app_commands.describe(
    tag="Player tag, e.g. #ABC123",
    user="Discord user",
    api_token="API token from in-game settings"
)
async def link(interaction: discord.Interaction, tag: str, user: discord.Member, api_token: str):
    await interaction.response.defer(ephemeral=True)

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    valid = await verify_player_token(tag, api_token)
    if not valid:
        await interaction.followup.send("❌ Invalid API token. Check the token in your in-game settings.", ephemeral=True)
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
        await interaction.followup.send(f"ℹ️ **{player_name}** is already linked to {user.mention}.", ephemeral=True)
        return

    player.discord_user_id = discord_user.id
    session.commit()
    session.close()

    await interaction.followup.send(f"✅ Linked **{player_name}** ({tag}) to {user.mention}.", ephemeral=True)


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


@bot.tree.command(name="force_link", description="Link a CoC account to Discord without token verification (admin only)")
@app_commands.describe(tag="Player tag, e.g. #ABC123", user="Discord user")
@app_commands.default_permissions(administrator=True)
async def force_link(interaction: discord.Interaction, tag: str, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    session = Session()

    player = session.query(Player).filter_by(tag=tag).first()
    if not player:
        result = await add_player_to_db(tag, session)
        if not result["success"]:
            await interaction.followup.send("❌ " + result["error"], ephemeral=True)
            session.close()
            return
        player = session.query(Player).filter_by(tag=result["tag"]).first()

    discord_user = session.query(DiscordUser).filter_by(discord_id=str(user.id)).first()
    if not discord_user:
        discord_user = DiscordUser(discord_id=str(user.id))
        session.add(discord_user)
        session.flush()

    player_name = player.name

    if player.discord_user_id == discord_user.id:
        session.close()
        await interaction.followup.send(f"ℹ️ **{player_name}** is already linked to {user.mention}.", ephemeral=True)
        return

    player.discord_user_id = discord_user.id
    session.commit()
    session.close()

    await interaction.followup.send(f"✅ Linked **{player_name}** ({tag}) to {user.mention}.", ephemeral=True)


@force_link.autocomplete("tag")
async def force_link_tag_autocomplete(_interaction: discord.Interaction, current: str):
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


@bot.tree.command(name="unlink", description="Unlink a Clash of Clans account from Discord")
@app_commands.describe(
    tag="Player tag, e.g. #ABC123",
    user="Discord user",
    api_token="API token from in-game settings"
)
async def unlink(interaction: discord.Interaction, tag: str, user: discord.Member, api_token: str):
    await interaction.response.defer(ephemeral=True)

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    valid = await verify_player_token(tag, api_token)
    if not valid:
        await interaction.followup.send("❌ Invalid API token.", ephemeral=True)
        return

    session = Session()
    player = session.query(Player).filter_by(tag=tag).first()

    if not player or not player.discord_user or player.discord_user.discord_id != str(user.id):
        session.close()
        await interaction.followup.send("❌ This account is not linked to the given user.", ephemeral=True)
        return

    player_name = player.name
    player.discord_user_id = None
    session.commit()
    session.close()

    await interaction.followup.send(f"✅ Unlinked **{player_name}** ({tag}) from {user.mention}.", ephemeral=True)


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


@bot.tree.command(name="force_unlink", description="Unlink a CoC account from Discord without token verification (admin only)")
@app_commands.describe(tag="Player tag, e.g. #ABC123")
@app_commands.default_permissions(administrator=True)
async def force_unlink(interaction: discord.Interaction, tag: str):
    await interaction.response.defer(ephemeral=True)

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    session = Session()
    player = session.query(Player).filter_by(tag=tag).first()

    if not player or player.discord_user_id is None:
        session.close()
        await interaction.followup.send("❌ This account is not linked to any user.", ephemeral=True)
        return

    player_name = player.name
    player.discord_user_id = None
    session.commit()
    session.close()

    await interaction.followup.send(f"✅ Unlinked **{player_name}** ({tag}).", ephemeral=True)


@force_unlink.autocomplete("tag")
async def force_unlink_tag_autocomplete(_interaction: discord.Interaction, current: str):
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


@bot.tree.command(name="profile", description="Show CoC accounts linked to a Discord user")
@app_commands.describe(user="Discord user (defaults to you)")
async def profile(interaction: discord.Interaction, user: discord.Member | None = None):
    target = user or interaction.user
    session = Session()

    discord_user = session.query(DiscordUser).filter_by(discord_id=str(target.id)).first()

    if not discord_user or not discord_user.players:
        await interaction.response.send_message(
            f"❌ {target.mention} has no linked CoC accounts.", ephemeral=True
        )
        session.close()
        return

    lines = []
    for p in discord_user.players:
        tag_encoded = p.tag.replace("#", "%23")
        url = f"https://link.clashofclans.com/en?action=OpenPlayerProfile&tag={tag_encoded}"
        lines.append(f"• [{p.name} ({p.tag})]({url})")

    embed = discord.Embed(
        title=f"CoC Accounts — {target.display_name}",
        description="\n".join(lines),
        color=0x8B4513
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    session.close()

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="legend_day_user", description="Legend day stats for all linked accounts of a user")
@app_commands.describe(user="Discord user (defaults to you)")
async def legend_day_user(interaction: discord.Interaction, user: discord.Member | None = None):
    await interaction.response.defer()

    target = user or interaction.user
    session = Session()

    discord_user = session.query(DiscordUser).filter_by(discord_id=str(target.id)).first()
    if not discord_user or not discord_user.players:
        await interaction.followup.send(f"❌ {target.mention} has no linked CoC accounts.")
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

    embeds = build_legend_table_embeds(f"📊 Legend Day — {target.display_name}", rows)
    await interaction.followup.send(embeds=embeds)


def fmt_name(name, tag, width):
    clean = ''.join(c for c in name if c.isascii() or 'Ā' <= c <= 'ɏ').strip()
    return f"{clean} ({tag})"[:width]

def build_legend_table_embeds(title: str, rows: list) -> list[discord.Embed]:
    header = f"‎`{'A/D':<5} {'ATK':>4} {'DEF':>4} {'NET':>4}  {'Reset':>5}  {'Curr':>5}  {'Rnk':>6} `  **NAME**"
    lines = [header]
    for name, tag, atk, deff, net, init, final, rank, atk_n, def_n in rows:
        init_str  = str(init)  if init  is not None else "—"
        final_str = str(final) if final is not None else "—"
        rank_str  = f"#{rank}" if rank is not None else "—"
        ad_str    = f"{atk_n}/{def_n}"
        nums = f"{ad_str:<5} {atk:>+4} {deff:>+4} {net:>+4}  {init_str:>5}  {final_str:>5}  {rank_str:>6} "
        clean_name = ''.join(c for c in name if c.isascii() or 'Ā' <= c <= 'ɏ').strip() or name
        lines.append(f"‎`{nums}` ‎{clean_name}")

    embed = discord.Embed(title=title, color=0x8B4513)
    desc = "\n".join(lines)
    if len(desc) <= 4000:
        embed.description = desc
    else:
        block = header
        for line in lines[1:]:
            if len(block) + len(line) + 1 > 1024:
                embed.add_field(name="", value=block, inline=False)
                block = line
            else:
                block += "\n" + line
        if block:
            embed.add_field(name="", value=block, inline=False)

    return [embed]


@bot.tree.command(name="legend_day_role", description="Legend day stats for all accounts of users with a role")
@app_commands.describe(role="Discord role")
async def legend_day_role(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer()

    session = Session()
    start, end = get_day_window(0)

    rows = []
    unlinked = []
    for member in role.members:
        discord_user = session.query(DiscordUser).filter_by(discord_id=str(member.id)).first()
        if not discord_user:
            discord_user = DiscordUser(discord_id=str(member.id))
            session.add(discord_user)
            session.flush()
        if not discord_user.players:
            unlinked.append(member.display_name)
            continue

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

    session.commit()
    session.close()

    if not rows:
        await interaction.followup.send(f"❌ No linked CoC accounts found for role {role.mention}.")
        return

    rows = [r for r in rows if r[6] is not None and r[6] > 0]
    rows.sort(key=lambda r: (r[5] if r[5] is not None else 0, r[4]), reverse=True)

    embeds = build_legend_table_embeds(f"📊 Legend Day — {role.name}", rows)
    await interaction.followup.send(embeds=embeds)

    if unlinked:
        names = ", ".join(unlinked)
        await interaction.followup.send(f"⚠️ No linked accounts: {names}")


@bot.tree.command(name="legend_day_clan", description="Legend day stats for all members of a clan")
@app_commands.describe(tag="Clan tag, e.g. #ABC123")
async def legend_day_clan(interaction: discord.Interaction, tag: str):
    await interaction.response.defer()

    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    session = Session()

    clan = session.query(Clan).filter_by(tag=tag).first()
    if not clan:
        data = await get_clan(tag)
        if not data:
            await interaction.followup.send("❌ Clan with the given tag was not found.")
            session.close()
            return
        clan_tag, name = data
        clan = Clan(tag=clan_tag, name=name)
        session.add(clan)
        session.commit()

    members = await get_clan_members(clan.tag)
    member_tags = {m["tag"] if isinstance(m, dict) else m for m in members}

    start, end = get_day_window(0)

    rows = []
    for member_tag in member_tags:
        player = session.query(Player).filter_by(tag=member_tag).first()
        if not player:
            continue

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

    if not rows:
        await interaction.followup.send("❌ No data for this clan's members.")
        return

    rows = [r for r in rows if r[6] is not None and r[6] > 0]
    rows.sort(key=lambda r: (r[5] if r[5] is not None else 0, r[4]), reverse=True)

    embeds = build_legend_table_embeds(f"📊 Legend Day — {clan.name}", rows)
    await interaction.followup.send(embeds=embeds)


@legend_day_clan.autocomplete("tag")
async def legend_day_clan_tag_autocomplete(_interaction: discord.Interaction, current: str):
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
