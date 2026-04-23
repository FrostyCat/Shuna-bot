from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, UTC

Base = declarative_base()

class DiscordUser(Base):
    __tablename__ = "discord_users"

    id = Column(Integer, primary_key=True)
    discord_id = Column(String, unique=True)

    players = relationship("Player", back_populates="discord_user")


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    tag = Column(String, unique=True)
    name = Column(String)
    initial_rank = Column(Integer, nullable=True)
    current_rank = Column(Integer, nullable=True)
    discord_user_id = Column(Integer, ForeignKey("discord_users.id"), nullable=True)

    attacks = relationship("Attack", back_populates="player")
    discord_user = relationship("DiscordUser", back_populates="players")


class Attack(Base):
    __tablename__ = "attacks"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    defender = Column(String)
    stars = Column(Integer)
    destruction = Column(Integer)
    trophies = Column(Integer)
    is_attack = Column(Boolean)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    army_share_code = Column(String, nullable=True)

    player = relationship("Player", back_populates="attacks")


class Clan(Base):
    __tablename__ = "clans"

    id = Column(Integer, primary_key=True)
    tag = Column(String, unique=True)
    name = Column(String)


class GuildConfig(Base):
    __tablename__ = "guild_configs"

    guild_id = Column(String, primary_key=True)
    staff_role_id = Column(String, nullable=True)
    ticket_category_id = Column(String, nullable=True)
    log_channel_id = Column(String, nullable=True)
