from zoneinfo import ZoneInfo
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from coc_api import get_battlelog, get_player, get_current_war, get_cwl_group, get_cwl_war
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

        created_at = datetime.now(UTC)

        stmt = pg_insert(Attack).values(
            player_id=player.id,
            defender=b.get("opponentPlayerTag"),
            stars=stars,
            destruction=destruction,
            trophies=trophies,
            is_attack=is_attack,
            created_at=created_at,
        ).on_conflict_do_nothing(
            index_elements=["player_id", "defender", "stars", "destruction", "is_attack"]
        )
        result = session.execute(stmt)
        total_count += result.rowcount

    return total_count


def _insert_war_attack(session, clan_tag, attack, war_type, war_id) -> int:
    stmt = pg_insert(WarAttack).values(
        clan_tag=clan_tag,
        attacker_tag=attack["attackerTag"],
        defender_tag=attack["defenderTag"],
        stars=attack["stars"],
        destruction=attack["destructionPercentage"],
        war_type=war_type,
        war_id=war_id,
    ).on_conflict_do_nothing(
        index_elements=["attacker_tag", "defender_tag", "war_id"]
    )
    return session.execute(stmt).rowcount


async def fetch_war_attacks(session, clan_tag: str) -> int:
    data = await get_current_war(clan_tag)
    if not data or data.get("state") not in ("inWar", "warEnded"):
        return 0

    war_id = data.get("startTime", "unknown")
    count = 0
    for member in data.get("clan", {}).get("members", []):
        for attack in member.get("attacks", []):
            count += _insert_war_attack(session, clan_tag, attack, "war", war_id)
    session.commit()
    return count


async def fetch_cwl_attacks(session, clan_tag: str) -> int:
    group = await get_cwl_group(clan_tag)
    if not group or "rounds" not in group:
        return 0

    count = 0
    for round_data in group.get("rounds", []):
        for war_tag in round_data.get("warTags", []):
            if war_tag == "#0":
                continue
            war = await get_cwl_war(war_tag)
            if not war or war.get("state") not in ("inWar", "warEnded"):
                continue

            # Find which side is our clan
            if war.get("clan", {}).get("tag") == clan_tag:
                our_side = war["clan"]
            elif war.get("opponent", {}).get("tag") == clan_tag:
                our_side = war["opponent"]
            else:
                continue

            for member in our_side.get("members", []):
                for attack in member.get("attacks", []):
                    count += _insert_war_attack(session, clan_tag, attack, "cwl", war_tag)

    session.commit()
    return count


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

    return {"success": True, "name": name, "tag": tag_api, "added_attacks": added}
