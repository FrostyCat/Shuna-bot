import asyncio
from zoneinfo import ZoneInfo
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from coc_api import get_battlelog, get_player, get_current_war, get_cwl_group, get_cwl_war, get_clan_war_league
from db import Session
from models import Attack, Player, WarAttack

WARSAW = ZoneInfo("Europe/Warsaw")


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


async def fetch_player_attacks(session, player):
    battles = await get_battlelog(player.tag)

    def _insert_all():
        count = 0
        for b in battles:
            if b.get("battleType") != "legend":
                continue
            if not b.get("opponentPlayerTag"):
                continue
            is_attack = b.get("attack", False)
            stars = b.get("stars", 0)
            destruction = b.get("destructionPercentage", 0)
            trophies = calculate_trophies(stars, destruction)
            if not is_attack:
                trophies = -trophies
            created_at = _parse_coc_time(b.get("battleTimestamp")) or datetime.now(UTC)
            stmt = pg_insert(Attack).values(
                player_id=player.id,
                defender=b.get("opponentPlayerTag"),
                stars=stars,
                destruction=destruction,
                trophies=trophies,
                is_attack=is_attack,
                created_at=created_at,
                army_share_code=b.get("armyShareCode"),
            ).on_conflict_do_nothing(
                index_elements=["player_id", "defender", "stars", "destruction", "is_attack"]
            )
            result = session.execute(stmt)
            count += result.rowcount
        return count

    return await asyncio.get_running_loop().run_in_executor(None, _insert_all)


def _parse_coc_time(s):
    if not s:
        return datetime.now(UTC)
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
    except Exception:
        return datetime.now(UTC)


def _insert_war_attack(session, clan_tag, attack, war_type, war_id, league=None, created_at=None) -> int:
    stmt = pg_insert(WarAttack).values(
        clan_tag=clan_tag,
        attacker_tag=attack["attackerTag"],
        defender_tag=attack["defenderTag"],
        stars=attack["stars"],
        destruction=attack["destructionPercentage"],
        war_type=war_type,
        war_id=war_id,
        league=league,
        created_at=created_at or datetime.now(UTC),
    ).on_conflict_do_nothing(
        index_elements=["attacker_tag", "defender_tag", "war_id"]
    )
    return session.execute(stmt).rowcount


async def fetch_war_attacks(session, clan_tag: str) -> int:
    data = await get_current_war(clan_tag)
    if not data or data.get("state") not in ("inWar", "warEnded"):
        return 0

    war_id = data.get("startTime", "unknown")
    war_date = _parse_coc_time(data.get("endTime") or data.get("startTime"))
    attacks = [
        attack
        for member in data.get("clan", {}).get("members", [])
        for attack in member.get("attacks", [])
    ]

    def _insert_all():
        count = 0
        for attack in attacks:
            count += _insert_war_attack(session, clan_tag, attack, "war", war_id, created_at=war_date)
        session.commit()
        return count

    return await asyncio.get_running_loop().run_in_executor(None, _insert_all)


async def fetch_cwl_attacks(session, clan_tag: str) -> int:
    group = await get_cwl_group(clan_tag)
    if not group or "rounds" not in group:
        return 0

    league = await get_clan_war_league(clan_tag)

    war_attacks = []
    for round_data in group.get("rounds", []):
        for war_tag in round_data.get("warTags", []):
            if war_tag == "#0":
                continue
            war = await get_cwl_war(war_tag)
            if not war or war.get("state") not in ("inWar", "warEnded"):
                continue
            if war.get("clan", {}).get("tag") == clan_tag:
                our_side = war["clan"]
            elif war.get("opponent", {}).get("tag") == clan_tag:
                our_side = war["opponent"]
            else:
                continue
            war_date = _parse_coc_time(war.get("endTime") or war.get("startTime"))
            for member in our_side.get("members", []):
                for attack in member.get("attacks", []):
                    war_attacks.append((attack, war_tag, war_date))

    def _insert_all():
        count = 0
        for attack, war_tag, war_date in war_attacks:
            count += _insert_war_attack(session, clan_tag, attack, "cwl", war_tag, league=league, created_at=war_date)
        session.commit()
        return count

    return await asyncio.get_running_loop().run_in_executor(None, _insert_all)


async def add_player_to_db(tag: str, session, commit=True, fetch_attacks=True):
    tag = tag.upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    data = await get_player(tag)
    if not data:
        return {"success": False, "error": "Player not found"}

    tag_api, name, _trophies, _rank, th_level = data[0], data[1], data[2] if len(data) > 2 else None, data[3] if len(data) > 3 else None, data[4] if len(data) > 4 else None

    is_new = False

    def _get_or_create():
        nonlocal is_new
        p = session.query(Player).filter_by(tag=tag_api).first()
        if not p:
            p = Player(tag=tag_api, name=name, tracked_since=datetime.now(UTC), th_level=th_level)
            session.add(p)
            session.flush()
            is_new = True
        else:
            p.name = name
            if th_level is not None:
                p.th_level = th_level
        if commit:
            session.commit()
        return p

    player = await asyncio.get_running_loop().run_in_executor(None, _get_or_create)

    added = 0
    if fetch_attacks:
        added = await fetch_player_attacks(session, player)
        if commit:
            await asyncio.get_running_loop().run_in_executor(None, session.commit)

    return {"success": True, "name": name, "tag": tag_api, "added_attacks": added, "is_new": is_new}
