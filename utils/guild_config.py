from db import Session
from models import GuildConfig

DEFAULTS = {
    "staff_role_id": None,
    "ticket_category_id": None,
    "log_channel_id": None,
}


def get(guild_id: int, key: str):
    session = Session()
    config = session.query(GuildConfig).filter_by(guild_id=str(guild_id)).first()
    session.close()
    if not config:
        return DEFAULTS.get(key)
    return getattr(config, key, DEFAULTS.get(key))


def get_all(guild_id: int) -> dict:
    session = Session()
    config = session.query(GuildConfig).filter_by(guild_id=str(guild_id)).first()
    session.close()
    if not config:
        return dict(DEFAULTS)
    return {k: getattr(config, k, v) for k, v in DEFAULTS.items()}


def set_value(guild_id: int, key: str, value):
    session = Session()
    config = session.query(GuildConfig).filter_by(guild_id=str(guild_id)).first()
    if not config:
        config = GuildConfig(guild_id=str(guild_id), **DEFAULTS)
        session.add(config)
    setattr(config, key, str(value) if value is not None else None)
    session.commit()
    session.close()
