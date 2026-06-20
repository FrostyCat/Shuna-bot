from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text, UniqueConstraint
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
    tracked_since = Column(DateTime, nullable=True)
    th_level = Column(Integer, nullable=True)
    league_tier = Column(String, nullable=True)
    is_verified = Column(Boolean, default=False, nullable=False, server_default="false")
    verified_at = Column(DateTime, nullable=True)

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
    player = relationship("Player", back_populates="attacks")

    __table_args__ = (
        UniqueConstraint("player_id", "defender", "stars", "destruction", "is_attack", name="uq_attack"),
    )


class Clan(Base):
    __tablename__ = "clans"

    id = Column(Integer, primary_key=True)
    tag = Column(String, unique=True)
    name = Column(String)
    tracked_since = Column(DateTime, nullable=True)


class GuildClan(Base):
    __tablename__ = "guild_clans"

    id = Column(Integer, primary_key=True)
    guild_id = Column(String)
    clan_tag = Column(String)
    clan_name = Column(String, nullable=True)
    category = Column(String, nullable=False, server_default="other")
    sort_order = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("guild_id", "clan_tag", name="uq_guild_clan"),
    )


class WarAttack(Base):
    __tablename__ = "war_attacks"

    id = Column(Integer, primary_key=True)
    clan_tag = Column(String)
    attacker_tag = Column(String)
    defender_tag = Column(String)
    stars = Column(Integer)
    destruction = Column(Integer)
    war_type = Column(String)  # "war" | "cwl"
    war_id = Column(String)    # startTime for regular wars, warTag for CWL
    league = Column(String, nullable=True)  # CWL league name, e.g. "Champion League I"
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("attacker_tag", "defender_tag", "war_id", name="uq_war_attack"),
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True)
    guild_id = Column(String)
    channel_name = Column(String)
    closed_by = Column(String)
    closed_at = Column(DateTime, default=lambda: datetime.now(UTC))
    messages = Column(Text)


class TicketPanel(Base):
    __tablename__ = "ticket_panels"

    id = Column(Integer, primary_key=True)
    guild_id = Column(String)
    message_id = Column(String, unique=True)
    channel_id = Column(String)
    types = Column(String, nullable=True)
    msg_title = Column(String, nullable=True)
    msg_description = Column(Text, nullable=True)
    msg_color = Column(String, nullable=True)
    msg_thumbnail = Column(String, nullable=True)
    msg_image = Column(String, nullable=True)


class TicketType(Base):
    __tablename__ = "ticket_types"

    id = Column(Integer, primary_key=True)
    panel_id = Column(Integer, ForeignKey("ticket_panels.id", ondelete="CASCADE"))
    name = Column(String)
    button_color = Column(Integer, default=1, nullable=True)  # 1=blurple 2=gray 3=green 4=red
    msg_title = Column(String, nullable=True)
    msg_description = Column(Text, nullable=True)
    msg_color = Column(String, nullable=True)
    msg_thumbnail = Column(String, nullable=True)
    msg_image = Column(String, nullable=True)


class ClanMember(Base):
    __tablename__ = "clan_members"

    id = Column(Integer, primary_key=True)
    guild_clan_id = Column(Integer, ForeignKey("guild_clans.id", ondelete="CASCADE"))
    player_tag = Column(String)

    __table_args__ = (
        UniqueConstraint("guild_clan_id", "player_tag", name="uq_clan_member"),
    )


class CwlSignup(Base):
    __tablename__ = "cwl_signups"

    id = Column(Integer, primary_key=True)
    panel_id = Column(Integer, ForeignKey("cwl_signup_panels.id", ondelete="CASCADE"), nullable=False)
    guild_id = Column(String, nullable=False)
    season = Column(String, nullable=False)
    discord_id = Column(String, nullable=False)
    player_tag = Column(String, nullable=False)
    signed_up_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("panel_id", "player_tag", name="uq_cwl_signup"),
    )


class CwlSignupPanel(Base):
    __tablename__ = "cwl_signup_panels"

    id = Column(Integer, primary_key=True)
    guild_id = Column(String)
    season = Column(String)
    message_id = Column(String, unique=True)
    channel_id = Column(String)
    embed_title = Column(String, nullable=True)
    embed_description = Column(Text, nullable=True)
    is_open = Column(Boolean, default=True, nullable=False, server_default="true")


class CwlParticipantClan(Base):
    __tablename__ = "cwl_participant_clans"

    id = Column(Integer, primary_key=True)
    guild_id = Column(String, nullable=False)
    season = Column(String, nullable=False)
    gc_id = Column(Integer, ForeignKey("guild_clans.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("guild_id", "season", "gc_id", name="uq_cwl_participant_clan"),
    )


class CwlRosterSlot(Base):
    __tablename__ = "cwl_roster_slots"

    id = Column(Integer, primary_key=True)
    guild_id = Column(String, nullable=False)
    season = Column(String, nullable=False)
    player_tag = Column(String, nullable=False)
    gc_id = Column(Integer, ForeignKey("guild_clans.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("guild_id", "season", "player_tag", name="uq_cwl_roster_slot"),
    )


class XGuildConfig(Base):
    __tablename__ = "x_guild_configs"

    guild_id = Column(String, primary_key=True)
    mention_role_id = Column(String, nullable=True)


class XSubscription(Base):
    __tablename__ = "x_subscriptions"

    id = Column(Integer, primary_key=True)
    guild_id = Column(String, nullable=False)
    channel_id = Column(String, nullable=False)
    username = Column(String, nullable=False)
    user_id = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    last_tweet_id = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("guild_id", "username", name="uq_x_subscription"),
    )


class GuildConfig(Base):
    __tablename__ = "guild_configs"

    guild_id = Column(String, primary_key=True)
    staff_role_id = Column(String, nullable=True)
    clan_member_role_id = Column(String, nullable=True)
    ticket_category_id = Column(String, nullable=True)
    log_channel_id = Column(String, nullable=True)
    notify_new_players = Column(Boolean, default=False, nullable=False, server_default="false")
    ticket_types = Column(String, nullable=True)
    ticket_msg_title = Column(String, nullable=True)
    ticket_msg_description = Column(Text, nullable=True)
    ticket_msg_color = Column(String, nullable=True)
    ticket_msg_thumbnail = Column(String, nullable=True)
    ticket_msg_image = Column(String, nullable=True)
