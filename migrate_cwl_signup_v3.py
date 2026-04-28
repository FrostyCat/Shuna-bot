"""Change cwl_signups unique constraint from (panel_id, discord_id) to (panel_id, player_tag)."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE cwl_signups DROP CONSTRAINT IF EXISTS uq_cwl_signup"))
    conn.execute(text(
        "ALTER TABLE cwl_signups ADD CONSTRAINT uq_cwl_signup UNIQUE (panel_id, player_tag)"
    ))
    conn.commit()
    print("Migration complete: uq_cwl_signup now on (panel_id, player_tag).")
