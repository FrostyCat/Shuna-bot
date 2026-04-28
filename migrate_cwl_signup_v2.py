"""Recreate cwl_signups with panel_id FK (per-panel independent signups)."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("DROP TABLE IF EXISTS cwl_signups"))
    conn.execute(text("""
        CREATE TABLE cwl_signups (
            id SERIAL PRIMARY KEY,
            panel_id INTEGER NOT NULL REFERENCES cwl_signup_panels(id) ON DELETE CASCADE,
            guild_id VARCHAR NOT NULL,
            season VARCHAR NOT NULL,
            discord_id VARCHAR NOT NULL,
            player_tag VARCHAR NOT NULL,
            signed_up_at TIMESTAMP,
            CONSTRAINT uq_cwl_signup UNIQUE (panel_id, discord_id)
        )
    """))
    conn.commit()
    print("Migration complete: cwl_signups recreated with panel_id FK.")
