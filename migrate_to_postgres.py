"""
Run once to migrate data from SQLite to PostgreSQL.
Make sure DATABASE_URL is set in .env before running.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

sqlite_engine = create_engine("sqlite:///attacks.db")
pg_engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)

from models import Base, DiscordUser, Player, Attack, Clan

Base.metadata.create_all(pg_engine)

SqliteSession = sessionmaker(bind=sqlite_engine)
PgSession = sessionmaker(bind=pg_engine)

src = SqliteSession()
dst = PgSession()

print("Migrating discord_users...")
users = src.query(DiscordUser).all()
for u in users:
    if not dst.query(DiscordUser).filter_by(discord_id=u.discord_id).first():
        dst.add(DiscordUser(id=u.id, discord_id=u.discord_id))
dst.commit()
print(f"  {len(users)} rows")

print("Migrating clans...")
clans = src.query(Clan).all()
for c in clans:
    if not dst.query(Clan).filter_by(tag=c.tag).first():
        dst.add(Clan(id=c.id, tag=c.tag, name=c.name))
dst.commit()
print(f"  {len(clans)} rows")

print("Migrating players...")
players = src.query(Player).all()
for p in players:
    if not dst.query(Player).filter_by(tag=p.tag).first():
        dst.add(Player(
            id=p.id,
            tag=p.tag,
            name=p.name,
            initial_rank=p.initial_rank,
            current_rank=p.current_rank,
            discord_user_id=p.discord_user_id,
        ))
dst.commit()
print(f"  {len(players)} rows")

print("Migrating attacks...")
attacks = src.query(Attack).all()
batch = []
for i, a in enumerate(attacks):
    if not dst.query(Attack).filter_by(id=a.id).first():
        batch.append(Attack(
            id=a.id,
            player_id=a.player_id,
            defender=a.defender,
            stars=a.stars,
            destruction=a.destruction,
            trophies=a.trophies,
            is_attack=a.is_attack,
            created_at=a.created_at,
        ))
    if len(batch) >= 500:
        dst.bulk_save_objects(batch)
        dst.commit()
        batch = []
        print(f"  {i+1}/{len(attacks)}...")
if batch:
    dst.bulk_save_objects(batch)
    dst.commit()
print(f"  {len(attacks)} rows")

# Fix sequences so auto-increment works correctly after migration
with pg_engine.connect() as conn:
    for table, col in [("discord_users", "id"), ("players", "id"), ("attacks", "id"), ("clans", "id")]:
        conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), MAX({col})) FROM {table}"))
    conn.commit()

src.close()
dst.close()
print("Migration complete.")
