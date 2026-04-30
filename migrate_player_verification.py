import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE players
        ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP WITH TIME ZONE
    """))
    conn.commit()
    print("Migration complete: added is_verified, verified_at to players.")
