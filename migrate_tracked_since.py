"""Add tracked_since column to players table."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE players ADD COLUMN IF NOT EXISTS tracked_since TIMESTAMP WITH TIME ZONE"
    ))
    conn.commit()
    print("Migration complete: players.tracked_since added.")
