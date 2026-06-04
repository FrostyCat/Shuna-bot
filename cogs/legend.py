import asyncio
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import commands
from sqlalchemy import func

from coc_api import get_clan, get_clan_members, get_player
from db import Session
from models import Attack, Clan, Player
from helpers import WARSAW, add_player_to_db, fetch_player_attacks

SEASON_EPOCH = datetime(2026, 5, 19, 7, 0, 0, tzinfo=WARSAW)
SEASON_DURATION = timedelta(days=28)
MONTHS_EN = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]


def is_legend1(player_data) -> bool:
    return bool(player_data and len(player_data) > 5 and player_data[5] == "#0")


def is_first_day(player, day_offset: int = 0) -> bool:
    if not player.tracked_since:
        return False
    start, end = get_day_window(day_offset)
    ts = player.tracked_since.replace(tzinfo=UTC)
    return start <= ts < end


def build_first_day_embed(player) -> discord.Embed:
    ts = player.tracked_since.replace(tzinfo=UTC).astimezone(WARSAW).strftime("%Y-%m-%d")
    embed = discord.Embed(
        title="New Player Tracking Started",
        description=(
            f"**{player.name}** (`{player.tag}`) has been added to the tracking system.\n"
            f"Stats collection starts now — first-day data will be skipped to ensure accuracy."
        ),
        color=0xf472b6,
    )
    embed.set_footer(text=f"Tracked since: {ts}")
    return embed


def season_label(season: int) -> str:
    end = SEASON_EPOCH - SEASON_DURATION * (season - 1) + SEASON_DURATION
    return f"{MONTHS_EN[end.month - 1]} {end.year}"


def get_day_window(day_offset: int):
    now = datetime.now(WARSAW)
    if now.hour < 7:
        current_start = (now - timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    else:
        current_start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    start = (current_start + timedelta(days=day_offset)).astimezone(UTC)
    end = start + timedelta(days=1)
    return start, end


def get_season_window(season: int):
    start = SEASON_EPOCH - SEASON_DURATION * (season - 1)
    end = start + SEASON_DURATION
    return start, end


def get_filled_defense_trophies(session, player, day_offset: int, real_def_count: int) -> int:
    if day_offset == 0:
        return 0
    if real_def_count >= 8:
        return 0
    missing = 8 - real_def_count
    prev_trophies = []
    for offset in [day_offset - 1, day_offset - 2]:
        start, end = get_day_window(offset)
        defs = session.query(Attack).filter(
            Attack.player_id == player.id,
            Attack.created_at >= start,
            Attack.created_at < end,
            Attack.is_attack == False,
        ).all()
        prev_trophies.extend(d.trophies for d in defs)
    if not prev_trophies:
        return 0
    avg = sum(prev_trophies) / len(prev_trophies)
    return round(avg * missing)


def build_legend_embed(player, session, day_offset: int, season_trophies=None, rank=None, initial_rank=None):
    start, end = get_day_window(day_offset)

    attacks = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= start,
        Attack.created_at < end,
        Attack.is_attack == True,
    ).order_by(Attack.created_at.asc()).all()

    defenses = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= start,
        Attack.created_at < end,
        Attack.is_attack == False,
    ).order_by(Attack.created_at.asc()).all()

    last_8 = attacks[::-1][:8]
    total = len(last_8)
    total_trophies = sum(a.trophies for a in last_8)
    avg_stars = sum(a.stars for a in last_8) / total if total else 0

    last_8_def = defenses[::-1][:8]
    total_trophies_def = sum(d.trophies for d in last_8_def)
    filled_count = max(0, 8 - len(last_8_def))
    filled_trophies = get_filled_defense_trophies(session, player, day_offset, len(last_8_def))
    total_trophies_def_net = total_trophies_def + filled_trophies

    if day_offset == 0:
        day_label = "Today"
    elif day_offset == -1:
        day_label = "Yesterday"
    else:
        day_label = f"{abs(day_offset)} days ago"

    attacks_text = "```\n" + "".join(
        f"{a.defender:<10} {a.stars}⭐ {a.destruction:>3}% {a.trophies:+}\n" for a in last_8
    ) + "```"
    def_lines = "".join(
        f"{d.defender:<10} {d.stars}⭐ {d.destruction:>3}% {d.trophies:+}\n" for d in last_8_def
    )
    if filled_count > 0:
        def_lines += f"{'(filled)':<10} {'':>4}    {filled_trophies:+} x{filled_count}\n"
    defenses_text = "```\n" + def_lines + "```"

    net = total_trophies + total_trophies_def_net
    if rank is not None and initial_rank is not None:
        diff_str = f" ({initial_rank - rank:+})" if initial_rank != rank else ""
        rank_line = f"Rank: #{rank}{diff_str} (start: #{initial_rank})\n"
    elif rank is not None:
        rank_line = f"Rank: #{rank}\n"
    else:
        rank_line = ""

    trophy_line = f"Current: {season_trophies} 🏆\n" if season_trophies is not None else ""
    reset_line = f"Reset: {season_trophies - net} 🏆\n" if season_trophies is not None else ""

    tracked_line = ""
    if player.tracked_since:
        ts = player.tracked_since.replace(tzinfo=UTC).astimezone(WARSAW)
        tracked_line = f"Tracked since: {ts.strftime('%Y-%m-%d')}\n"

    embed = discord.Embed(title=f"📊 {player.name} ({player.tag}) — {day_label}", color=0x8B4513)
    embed.add_field(
        name="🏆 Overview",
        value=(
            f"{rank_line}{trophy_line}{reset_line}"
            f"⚔️ {total} / 🛡️ {len(last_8_def)}{f' (+{filled_count} filled)' if filled_count else ''}\n"
            f"Avg ⭐: {avg_stars:.2f}\n"
            f"Trophies: {total_trophies:+}\n"
            f"Defenses: {total_trophies_def_net:+}\n"
            f"Net: {net:+}\n"
            f"{tracked_line}"
        ),
        inline=False,
    )
    embed.add_field(name="⚔️ Last Attacks", value=attacks_text, inline=False)
    embed.add_field(name="🛡️ Last Defenses", value=defenses_text, inline=False)
    return embed


def build_season_embed(player, session, season_trophies: int | None) -> discord.Embed:
    season_start, season_end = get_season_window(1)

    all_attacks = session.query(Attack).filter(
        Attack.player_id == player.id,
        Attack.created_at >= season_start,
        Attack.created_at < season_end,
    ).order_by(Attack.created_at.asc()).all()

    days: dict[int, dict] = {}
    for a in all_attacks:
        local = a.created_at.replace(tzinfo=UTC).astimezone(WARSAW)
        if local.hour < 7:
            local -= timedelta(days=1)
        day_start = local.replace(hour=7, minute=0, second=0, microsecond=0)
        day_num = int((day_start - season_start.astimezone(WARSAW)).days) + 1
        if day_num not in days:
            days[day_num] = {"atk": 0, "def": 0}
        if a.is_attack:
            days[day_num]["atk"] += a.trophies
        else:
            days[day_num]["def"] += a.trophies

    total_net = sum(d["atk"] + d["def"] for d in days.values())
    starting = (season_trophies - total_net) if season_trophies is not None else None

    header = f"\u200E`{'DAY':>3} {'ATK':>5} {'DEF':>5} {'+/-':>5}  {'INIT':>5}  {'FINAL':>5} `"
    lines = [header]
    cumulative = 0
    for day_num in sorted(days.keys()):
        d = days[day_num]
        net = d["atk"] + d["def"]
        init = (starting + cumulative) if starting is not None else None
        final = (init + net) if init is not None else None
        init_str = str(init) if init is not None else "—"
        final_str = str(final) if final is not None else "—"
        nums = f"{day_num:>3} {d['atk']:>+5} {d['def']:>+5} {net:>+5}  {init_str:>5}  {final_str:>5} "
        lines.append(f"\u200E`{nums}`")
        cumulative += net

    now = datetime.now(WARSAW)
    month = MONTHS_EN[now.month - 1]
    embed = discord.Embed(
        title=f"📅 Season Log — {player.name}",
        description="\n".join(lines),
        color=0x8B4513,
    )
    embed.set_footer(text=f"{month} {now.year} • {len(days)} days played")
    return embed


def build_legend_table_embeds(title: str, rows: list) -> list[discord.Embed]:
    header = f"‎`{'A/D':<5} {'ATK':>4} {'DEF':>4} {'NET':>4}  {'Reset':>5}  {'Curr':>5}  {'Rnk':>6} `  **NAME**"
    lines = [header]
    for name, _tag, atk, deff, net, init, final, rank, atk_n, def_n in rows:
        init_str = str(init) if init is not None else "—"
        final_str = str(final) if final is not None else "—"
        rank_str = f"#{rank}" if rank is not None else "—"
        ad_str = f"{atk_n}/{def_n}"
        nums = f"{ad_str:<5} {atk:>+4} {deff:>+4} {net:>+4}  {init_str:>5}  {final_str:>5}  {rank_str:>6} "
        clean_name = "".join(c for c in name if c.isascii() or "Ā" <= c <= "ɏ").strip() or name
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


class LegendView(discord.ui.View):
    def __init__(self, player_tag: str, day_offset: int = 0):
        super().__init__(timeout=3600)
        self.player_tag = player_tag
        self.day_offset = day_offset
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.next_day.disabled = self.day_offset >= 0

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_day(self, _button: discord.ui.Button, interaction: discord.Interaction):
        self.day_offset -= 1
        self._update_buttons()
        await self._render(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_day(self, _button: discord.ui.Button, interaction: discord.Interaction):
        self.day_offset += 1
        self._update_buttons()
        await self._render(interaction)

    @discord.ui.button(label="🔄", style=discord.ButtonStyle.secondary)
    async def refresh(self, _button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        session = Session()
        player = session.query(Player).filter_by(tag=self.player_tag).first()
        try:
            await fetch_player_attacks(session, player)
            await asyncio.get_running_loop().run_in_executor(None, session.commit)
        except Exception:
            await asyncio.get_running_loop().run_in_executor(None, session.rollback)
        player_data = await get_player(player.tag)
        season_trophies = player_data[2] if player_data else None
        rank = player_data[3] if player_data else None
        embed = build_legend_embed(player, session, self.day_offset, season_trophies=season_trophies, rank=rank, initial_rank=player.initial_rank)
        session.close()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="📅 Season", style=discord.ButtonStyle.primary)
    async def season_log(self, _button: discord.ui.Button, interaction: discord.Interaction):
        session = Session()
        player = session.query(Player).filter_by(tag=self.player_tag).first()
        player_data = await get_player(player.tag)
        season_trophies = player_data[2] if player_data else None
        embed = build_season_embed(player, session, season_trophies)
        session.close()
        season_view = SeasonView(self.player_tag)
        season_view.message = self.message
        await interaction.response.edit_message(embed=embed, view=season_view)

    async def _render(self, interaction: discord.Interaction):
        try:
            session = Session()
            player = session.query(Player).filter_by(tag=self.player_tag).first()
            embed = build_legend_embed(player, session, self.day_offset)
            session.close()
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"[LegendView._render] {e}")
            import traceback; traceback.print_exc()
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class SeasonView(discord.ui.View):
    def __init__(self, player_tag: str):
        super().__init__(timeout=3600)
        self.player_tag = player_tag
        self.message: discord.Message | None = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary)
    async def back(self, _button: discord.ui.Button, interaction: discord.Interaction):
        session = Session()
        player = session.query(Player).filter_by(tag=self.player_tag).first()
        embed = build_legend_embed(player, session, day_offset=0)
        session.close()
        legend_view = LegendView(self.player_tag)
        legend_view.message = self.message
        await interaction.response.edit_message(embed=embed, view=legend_view)


async def tag_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    players = session.query(Player).all()
    current = ctx.value.lower()
    choices = [
        discord.OptionChoice(name=f"{p.name} ({p.tag})", value=p.tag)
        for p in players
        if current in p.tag.lower() or current in p.name.lower()
    ]
    session.close()
    return choices[:25]


async def clan_tag_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    clans = session.query(Clan).all()
    current = ctx.value.lower()
    choices = [
        discord.OptionChoice(name=f"{c.name} ({c.tag})", value=c.tag)
        for c in clans
        if current in c.tag.lower() or current in c.name.lower()
    ]
    session.close()
    return choices[:25]


async def season_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    oldest = session.query(func.min(Attack.created_at)).scalar()
    session.close()

    if not oldest:
        return []
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)

    max_seasons = max(1, int((SEASON_EPOCH - oldest) / SEASON_DURATION) + 2)
    current = ctx.value.lower()
    choices = []
    for i in range(1, max_seasons + 1):
        label = season_label(i)
        if not current or current in label.lower():
            choices.append(discord.OptionChoice(name=label, value=i))
    return choices[:25]


class LegendCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="legend_day", description="Legend league stats for a player")
    async def legend_day(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=tag_autocomplete),
    ):
        await ctx.defer()
        session = Session()

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        player = session.query(Player).filter_by(tag=tag).first()
        if not player:
            result = await add_player_to_db(tag, session)
            if not result["success"]:
                await ctx.followup.send("❌ " + result["error"])
                session.close()
                return
            player = session.query(Player).filter_by(tag=result["tag"]).first()

        try:
            await fetch_player_attacks(session, player)
            await asyncio.get_running_loop().run_in_executor(None, session.commit)
        except Exception as e:
            print(f"[legend_day] fetch failed for {player.tag}: {e}")
            await asyncio.get_running_loop().run_in_executor(None, session.rollback)

        if is_first_day(player):
            embed = build_first_day_embed(player)
            session.close()
            await ctx.followup.send(embed=embed)
            return

        player_data = await get_player(player.tag)
        if not is_legend1(player_data):
            session.close()
            await ctx.followup.send("❌ This player is not currently in Legend League 1.")
            return
        season_trophies = player_data[2]
        rank = player_data[3]

        view = LegendView(player.tag)
        embed = build_legend_embed(player, session, day_offset=0, season_trophies=season_trophies, rank=rank, initial_rank=player.initial_rank)
        session.close()
        view.message = await ctx.followup.send(embed=embed, view=view)

    @discord.slash_command(name="legend_day_user", description="Legend day stats for all linked accounts of a user")
    async def legend_day_user(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "Discord user (defaults to you)", required=False, default=None),
    ):
        await ctx.defer()
        target = user or ctx.author
        session = Session()

        from models import DiscordUser
        discord_user = session.query(DiscordUser).filter_by(discord_id=str(target.id)).first()
        if not discord_user or not discord_user.players:
            await ctx.followup.send(f"❌ {target.mention} has no linked CoC accounts.")
            session.close()
            return

        start, end = get_day_window(0)
        rows = []
        for player in discord_user.players:
            player_data = await get_player(player.tag)
            if not is_legend1(player_data):
                continue
            season_trophies = player_data[2]
            attacks = session.query(Attack).filter(
                Attack.player_id == player.id,
                Attack.created_at >= start,
                Attack.created_at < end,
                Attack.is_attack == True,
            ).order_by(Attack.created_at.asc()).all()[-8:]
            defenses = session.query(Attack).filter(
                Attack.player_id == player.id,
                Attack.created_at >= start,
                Attack.created_at < end,
                Attack.is_attack == False,
            ).order_by(Attack.created_at.asc()).all()[-8:]
            atk = sum(a.trophies for a in attacks)
            deff = sum(d.trophies for d in defenses)
            net = atk + deff
            init = (season_trophies - net) if season_trophies is not None else None
            rows.append((player.name, player.tag, atk, deff, net, init, season_trophies, player.initial_rank, len(attacks), len(defenses)))

        session.close()
        if not rows:
            await ctx.followup.send(f"❌ {target.mention} has no accounts in Legend League 1.")
            return
        embeds = build_legend_table_embeds(f"📊 Legend Day — {target.display_name}", rows)
        await ctx.followup.send(embeds=embeds)

    @discord.slash_command(name="legend_day_role", description="Legend day stats for all accounts of users with a role")
    async def legend_day_role(
        self,
        ctx: discord.ApplicationContext,
        role: discord.Option(discord.Role, "Discord role"),
    ):
        await ctx.defer()
        session = Session()
        start, end = get_day_window(0)

        from models import DiscordUser
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
                if not is_legend1(player_data):
                    continue
                season_trophies = player_data[2]
                attacks = session.query(Attack).filter(
                    Attack.player_id == player.id,
                    Attack.created_at >= start,
                    Attack.created_at < end,
                    Attack.is_attack == True,
                ).order_by(Attack.created_at.asc()).all()[-8:]
                defenses = session.query(Attack).filter(
                    Attack.player_id == player.id,
                    Attack.created_at >= start,
                    Attack.created_at < end,
                    Attack.is_attack == False,
                ).order_by(Attack.created_at.asc()).all()[-8:]
                atk = sum(a.trophies for a in attacks)
                deff = sum(d.trophies for d in defenses)
                net = atk + deff
                init = (season_trophies - net) if season_trophies is not None else None
                rows.append((player.name, player.tag, atk, deff, net, init, season_trophies, player.initial_rank, len(attacks), len(defenses)))

        session.commit()
        session.close()

        if not rows:
            await ctx.followup.send(f"❌ No linked CoC accounts found for role {role.mention}.")
            return

        rows = [r for r in rows if r[6] is not None and r[6] > 0]
        rows.sort(key=lambda r: (r[5] if r[5] is not None else 0, r[4]), reverse=True)

        embeds = build_legend_table_embeds(f"📊 Legend Day — {role.name}", rows)
        await ctx.followup.send(embeds=embeds)

        if unlinked:
            await ctx.followup.send(f"⚠️ No linked accounts: {', '.join(unlinked)}")

    @discord.slash_command(name="legend_day_clan", description="Legend day stats for all members of a clan")
    async def legend_day_clan(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Clan tag, e.g. #ABC123", autocomplete=clan_tag_autocomplete),
    ):
        await ctx.defer()

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        session = Session()
        clan = session.query(Clan).filter_by(tag=tag).first()
        if not clan:
            data = await get_clan(tag)
            if not data:
                await ctx.followup.send("❌ Clan with the given tag was not found.")
                session.close()
                return
            clan_tag, name = data
            clan = Clan(tag=clan_tag, name=name, tracked_since=datetime.now(UTC))
            session.add(clan)
            session.commit()

        members = await get_clan_members(clan.tag)
        member_tags = {m["tag"] if isinstance(m, dict) else m for m in members}
        start, end = get_day_window(0)

        rows = []
        first_day_players = []
        for member_tag in member_tags:
            player = session.query(Player).filter_by(tag=member_tag).first()
            if not player:
                continue
            if is_first_day(player):
                first_day_players.append(player.name)
                continue
            player_data = await get_player(player.tag)
            if not is_legend1(player_data):
                continue
            season_trophies = player_data[2]
            attacks = session.query(Attack).filter(
                Attack.player_id == player.id,
                Attack.created_at >= start,
                Attack.created_at < end,
                Attack.is_attack == True,
            ).order_by(Attack.created_at.asc()).all()[-8:]
            defenses = session.query(Attack).filter(
                Attack.player_id == player.id,
                Attack.created_at >= start,
                Attack.created_at < end,
                Attack.is_attack == False,
            ).order_by(Attack.created_at.asc()).all()[-8:]
            atk = sum(a.trophies for a in attacks)
            deff = sum(d.trophies for d in defenses)
            net = atk + deff
            init = (season_trophies - net) if season_trophies is not None else None
            rows.append((player.name, player.tag, atk, deff, net, init, season_trophies, player.initial_rank, len(attacks), len(defenses)))

        session.close()

        if not rows:
            await ctx.followup.send("❌ No data for this clan's members.")
            return

        rows = [r for r in rows if r[6] is not None and r[6] > 0]
        rows.sort(key=lambda r: (r[5] if r[5] is not None else 0, r[4]), reverse=True)

        embeds = build_legend_table_embeds(f"📊 Legend Day — {clan.name}", rows)
        if first_day_players and embeds:
            embeds[0].add_field(
                name="🆕 New to tracking",
                value="\n".join(f"• {n}" for n in first_day_players) + "\n*First-day data skipped.*",
                inline=False,
            )
        if clan.tracked_since and embeds:
            ts = clan.tracked_since.replace(tzinfo=UTC).astimezone(WARSAW)
            embeds[0].set_footer(text=f"{clan.tag} • tracked since {ts.strftime('%Y-%m-%d')}")
        await ctx.followup.send(embeds=embeds)

    @discord.slash_command(name="legend_stats_clan", description="3⭐ hit rate for clan members in legend league")
    async def legend_stats_clan(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Clan tag, e.g. #ABC123", autocomplete=clan_tag_autocomplete),
        season: discord.Option(int, "Season number (1=current, 2=previous...). 0 = all time", required=False, autocomplete=season_autocomplete, default=1),
    ):
        await ctx.defer()
        session = Session()

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        clan = session.query(Clan).filter_by(tag=tag).first()
        is_new = not clan
        if not clan:
            data = await get_clan(tag)
            if not data:
                await ctx.followup.send("❌ Clan with the given tag was not found.")
                session.close()
                return
            clan_tag, name = data
            clan = Clan(tag=clan_tag, name=name, tracked_since=datetime.now(UTC))
            session.add(clan)
            session.commit()

        members = await get_clan_members(clan.tag)
        member_tags = {m["tag"] if isinstance(m, dict) else m for m in members}

        if is_new:
            await ctx.followup.send(f"⏳ New clan — registering {len(member_tags)} members...")
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
            rows.append((player.name, triples, total, triples / total * 100))

        session.close()

        if not rows:
            await ctx.followup.send("No data for this clan's members.")
            return

        rows.sort(key=lambda r: r[3], reverse=True)

        season_label_str = season_label(season) if season else "All time"
        header = f"‎`{'#':>3} {'RATE':>6} {'HITS':>7} `  **NAME**"
        lines = [header]
        for i, (name, triples, total, rate) in enumerate(rows, 1):
            fraction = f"{triples}/{total}"
            nums = f"{i:>3} {rate:>5.1f}% {fraction:>7} "
            clean_name = "".join(c for c in name if c.isascii() or "Ā" <= c <= "ɏ").strip() or name
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

        tracked_str = f" • tracked since {clan.tracked_since.replace(tzinfo=UTC).astimezone(WARSAW).strftime('%Y-%m-%d')}" if clan.tracked_since else ""
        embed.set_footer(text=f"{clan.tag} • {len(rows)} players{tracked_str}")
        await ctx.followup.send(embed=embed)

    @discord.slash_command(name="legend_stats_role", description="3⭐ hit rate for all linked accounts of members with a role")
    async def legend_stats_role(
        self,
        ctx: discord.ApplicationContext,
        role: discord.Option(discord.Role, "Discord role"),
        season: discord.Option(int, "Season number (1=current, 2=previous...). 0 = all time", required=False, autocomplete=season_autocomplete, default=1),
    ):
        await ctx.defer()
        session = Session()

        from models import DiscordUser
        season_start, season_end = get_season_window(season) if season else (None, None)
        season_label_str = season_label(season) if season else "All time"

        rows = []
        for member in role.members:
            discord_user = session.query(DiscordUser).filter_by(discord_id=str(member.id)).first()
            if not discord_user or not discord_user.players:
                continue
            for player in discord_user.players:
                q = session.query(Attack).filter(Attack.player_id == player.id, Attack.is_attack == True)
                if season_start:
                    q = q.filter(Attack.created_at >= season_start, Attack.created_at < season_end)
                total = q.count()
                triples = q.filter(Attack.stars == 3).count()
                if total == 0:
                    continue
                rows.append((player.name, triples, total, triples / total * 100))

        session.close()

        if not rows:
            await ctx.followup.send(f"❌ No attack data found for role {role.mention}.")
            return

        rows.sort(key=lambda r: r[3], reverse=True)

        header = f"‎`{'#':>3} {'RATE':>6} {'HITS':>7} `  **NAME**"
        lines = [header]
        for i, (name, triples, total, rate) in enumerate(rows, 1):
            fraction = f"{triples}/{total}"
            nums = f"{i:>3} {rate:>5.1f}% {fraction:>7} "
            clean_name = "".join(c for c in name if c.isascii() or "Ā" <= c <= "ɏ").strip() or name
            lines.append(f"‎`{nums}` ‎{clean_name}")

        embed = discord.Embed(title=f"⚔️ Legend Stats — {role.name} — {season_label_str}", color=0x8B4513)
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

        embed.set_footer(text=f"{role.name} • {len(rows)} accounts")
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(LegendCog(bot))
