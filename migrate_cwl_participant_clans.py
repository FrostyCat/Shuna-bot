"""Create cwl_participant_clans table."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cwl_participant_clans (
            id SERIAL PRIMARY KEY,
            guild_id VARCHAR NOT NULL,
            season VARCHAR NOT NULL,
            gc_id INTEGER NOT NULL REFERENCES guild_clans(id) ON DELETE CASCADE,
            CONSTRAINT uq_cwl_participant_clan UNIQUE (guild_id, season, gc_id)
        )
    """))
    conn.commit()
    print("Migration complete: cwl_participant_clans created.")
