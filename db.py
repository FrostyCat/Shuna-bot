import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 5,
        "keepalives_count": 3,
        "options": "-c statement_timeout=60000 -c lock_timeout=5000",
    },
)
Session = sessionmaker(bind=engine, autoflush=False)


_MIGRATIONS = [
    "ALTER TABLE players ADD COLUMN IF NOT EXISTS league_tier VARCHAR",
    "ALTER TABLE players ADD COLUMN IF NOT EXISTS season_trophies INTEGER",
    "ALTER TABLE attacks ADD COLUMN IF NOT EXISTS army_share_code VARCHAR",
    "ALTER TABLE guild_configs ADD COLUMN IF NOT EXISTS stats_channel_id VARCHAR",
]


def init_db():
    Base.metadata.create_all(engine)
    for stmt in _MIGRATIONS:
        try:
            with engine.connect() as conn:
                conn.execute(text(stmt))
                conn.commit()
        except Exception as e:
            print(f"[init_db] migration failed, skipping: {stmt!r} -> {e}")
