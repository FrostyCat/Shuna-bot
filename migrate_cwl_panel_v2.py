"""Add embed_title and embed_description columns to cwl_signup_panels."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE cwl_signup_panels ADD COLUMN IF NOT EXISTS embed_title VARCHAR"))
    conn.execute(text("ALTER TABLE cwl_signup_panels ADD COLUMN IF NOT EXISTS embed_description TEXT"))
    conn.commit()
    print("Migration complete: cwl_signup_panels.embed_title/embed_description added.")
