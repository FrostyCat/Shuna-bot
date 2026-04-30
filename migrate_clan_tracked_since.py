import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE clans
        ADD COLUMN IF NOT EXISTS tracked_since TIMESTAMP WITH TIME ZONE
    """))
    conn.commit()
    print("Migration complete: added tracked_since to clans.")
