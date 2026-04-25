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
    connect_args={"connect_timeout": 10},
    pool_timeout=10,
    pool_recycle=300,
)
Session = sessionmaker(bind=engine, autoflush=False)


def init_db():
    Base.metadata.create_all(engine)
