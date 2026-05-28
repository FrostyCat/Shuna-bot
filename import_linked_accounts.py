import csv
import sys
from datetime import datetime, UTC
from db import Session
from models import DiscordUser, Player

CSV_FILE = "SavageStars_LinkedAccounts.csv"

def run():
    session = Session()
    created_users = 0
    created_players = 0
    linked = 0
    skipped = 0

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Importing {len(rows)} rows...")

    for row in rows:
        discord_id = row["Discord ID"].strip()
        player_name = row["Player Name"].strip()
        player_tag = row["Player Tag"].strip().upper().replace("O", "0")

        if not player_tag.startswith("#"):
            player_tag = "#" + player_tag

        # Get or create DiscordUser
        discord_user = session.query(DiscordUser).filter_by(discord_id=discord_id).first()
        if not discord_user:
            discord_user = DiscordUser(discord_id=discord_id)
            session.add(discord_user)
            session.flush()
            created_users += 1

        # Get or create Player
        player = session.query(Player).filter_by(tag=player_tag).first()
        if not player:
            player = Player(
                tag=player_tag,
                name=player_name,
                tracked_since=datetime.now(UTC),
            )
            session.add(player)
            session.flush()
            created_players += 1
        else:
            player.name = player_name

        # Link if not already linked
        if player.discord_user_id == discord_user.id:
            skipped += 1
        else:
            player.discord_user_id = discord_user.id
            linked += 1

    session.commit()
    session.close()

    print(f"Done.")
    print(f"  Discord users created: {created_users}")
    print(f"  Players created:       {created_players}")
    print(f"  Links created:         {linked}")
    print(f"  Already linked:        {skipped}")

if __name__ == "__main__":
    run()
