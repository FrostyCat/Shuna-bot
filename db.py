from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from models import Base

engine = create_engine("sqlite:///attacks.db", connect_args={"timeout": 30})
Session = sessionmaker(bind=engine, autoflush=False)

@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        for col in ["initial_rank INTEGER", "current_rank INTEGER"]:
            try:
                conn.execute(__import__("sqlalchemy").text(f"ALTER TABLE players ADD COLUMN {col}"))
                conn.commit()
            except Exception:
                pass