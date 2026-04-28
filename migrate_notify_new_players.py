"""Add notify_new_players column to guild_configs."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE guild_configs
        ADD COLUMN IF NOT EXISTS notify_new_players BOOLEAN NOT NULL DEFAULT FALSE
    """))
    conn.commit()
    print("Migration complete: notify_new_players added to guild_configs.")
