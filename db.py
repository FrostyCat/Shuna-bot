import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
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


def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS league_tier VARCHAR"
            )
        )
        conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE players ADD COLUMN IF NOT EXISTS season_trophies INTEGER"
            )
        )
        conn.execute(
            __import__("sqlalchemy").text(
                "ALTER TABLE attacks ADD COLUMN IF NOT EXISTS army_share_code VARCHAR"
            )
        )
        conn.commit()
