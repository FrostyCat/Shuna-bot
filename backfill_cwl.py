#!/usr/bin/env python3
"""
One-time backfill script for historical CWL data using Clash King API.
Usage: python backfill_cwl.py
       python backfill_cwl.py --months 6   (default: 12)
"""

import asyncio
import sys
import aiohttp
from datetime import datetime, UTC

from db import Session, init_db
from models import Clan, WarAttack
from sqlalchemy.dialects.postgresql import insert as pg_insert

CK_BASE = "https://api.clashk.ing"


def past_seasons(n):
    now = datetime.now(UTC)
    seasons = []
    for i in range(1, n + 1):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        seasons.append(f"{year}-{month:02d}")
    return seasons


async def ck_get(session, url):
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            return None
    except Exception as e:
        print(f"    fetch error: {e}")
        return None


def parse_coc_time(s):
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
    except Exception:
        return datetime.now(UTC)


def insert_attack(db, clan_tag, attack, war_id, war_start):
    stmt = pg_insert(WarAttack).values(
        clan_tag=clan_tag,
        attacker_tag=attack["attackerTag"],
        defender_tag=attack["defenderTag"],
        stars=attack["stars"],
        destruction=attack["destructionPercentage"],
        war_type="cwl",
        war_id=war_id,
        league=None,
        created_at=war_start,
    ).on_conflict_do_nothing(
        index_elements=["attacker_tag", "defender_tag", "war_id"]
    )
    return db.execute(stmt).rowcount


async def backfill_clan(clan_tag, seasons):
    tag_enc = clan_tag.replace("#", "%23")
    timeout = aiohttp.ClientTimeout(total=30)
    clan_total = 0

    async with aiohttp.ClientSession(timeout=timeout) as http:
        for season in seasons:
            data = await ck_get(http, f"{CK_BASE}/cwl/{tag_enc}/{season}")
            if not data or "rounds" not in data:
                print(f"  {season}: no data")
                await asyncio.sleep(0.3)
                continue

            db = Session()
            count = 0
            try:
                for round_data in data.get("rounds", []):
                    for war in round_data.get("warTags", []):
                        if not isinstance(war, dict):
                            continue
                        if war.get("state") not in ("inWar", "warEnded"):
                            continue

                        raw_start = war.get("startTime") or ""
                        war_id = raw_start or f"ck_{season}"
                        war_start = parse_coc_time(raw_start) if raw_start else datetime.now(UTC)

                        if war.get("clan", {}).get("tag") == clan_tag:
                            our_side = war["clan"]
                        elif war.get("opponent", {}).get("tag") == clan_tag:
                            our_side = war["opponent"]
                        else:
                            continue

                        for member in our_side.get("members", []):
                            for attack in member.get("attacks", []):
                                count += insert_attack(db, clan_tag, attack, war_id, war_start)

                db.commit()
                clan_total += count
                print(f"  {season}: {count} new attacks saved")
            except Exception as e:
                db.rollback()
                print(f"  {season}: error — {e}")
            finally:
                db.close()

            await asyncio.sleep(0.3)

    return clan_total


async def main():
    months = 12
    if "--months" in sys.argv:
        idx = sys.argv.index("--months")
        months = int(sys.argv[idx + 1])

    init_db()
    seasons = past_seasons(months)

    db = Session()
    clans = [c.tag for c in db.query(Clan).all()]
    db.close()

    if not clans:
        print("No clans in database. Add clans via the dashboard first.")
        return

    print(f"Clans: {clans}")
    print(f"Seasons to check ({months} months): {seasons}\n")

    grand_total = 0
    for tag in clans:
        print(f"Processing {tag}...")
        total = await backfill_clan(tag, seasons)
        grand_total += total
        print(f"  → {total} new attacks for this clan\n")
        await asyncio.sleep(1)

    print(f"Done! Total new CWL attacks inserted: {grand_total}")


if __name__ == "__main__":
    asyncio.run(main())
