"""Add is_open column to cwl_signup_panels."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("""
        ALTER TABLE cwl_signup_panels
        ADD COLUMN IF NOT EXISTS is_open BOOLEAN NOT NULL DEFAULT TRUE
    """))
    conn.commit()
    print("Migration complete: is_open added to cwl_signup_panels.")
