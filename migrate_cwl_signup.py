"""Add cwl_signups, cwl_signup_panels, cwl_roster_slots tables."""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cwl_signups (
            id SERIAL PRIMARY KEY,
            guild_id VARCHAR NOT NULL,
            season VARCHAR NOT NULL,
            discord_id VARCHAR NOT NULL,
            player_tag VARCHAR NOT NULL,
            signed_up_at TIMESTAMP,
            CONSTRAINT uq_cwl_signup UNIQUE (guild_id, season, discord_id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cwl_signup_panels (
            id SERIAL PRIMARY KEY,
            guild_id VARCHAR,
            season VARCHAR,
            message_id VARCHAR UNIQUE,
            channel_id VARCHAR
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS cwl_roster_slots (
            id SERIAL PRIMARY KEY,
            guild_id VARCHAR NOT NULL,
            season VARCHAR NOT NULL,
            player_tag VARCHAR NOT NULL,
            gc_id INTEGER NOT NULL REFERENCES guild_clans(id),
            CONSTRAINT uq_cwl_roster_slot UNIQUE (guild_id, season, player_tag)
        )
    """))
    conn.commit()
    print("Migration complete: cwl_signups, cwl_signup_panels, cwl_roster_slots added.")
