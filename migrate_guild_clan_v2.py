"""Add category and sort_order columns to guild_clans table."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE guild_clans ADD COLUMN IF NOT EXISTS category VARCHAR NOT NULL DEFAULT 'other'"
    ))
    conn.execute(text(
        "ALTER TABLE guild_clans ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
    ))
    conn.commit()
    print("Migration complete: guild_clans.category and sort_order added.")
