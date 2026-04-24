from zoneinfo import ZoneInfo
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from coc_api import get_battlelog, get_player
from db import Session
from models import Attack, Player

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
