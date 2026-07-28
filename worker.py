import asyncio
from datetime import datetime, time as dt_time, timedelta

from db import init_db
from helpers import (
    WARSAW,
    refresh_all_clans,
    refresh_all_players,
    refresh_all_wars,
    snapshot_ranks,
    sync_top_clans,
)

REFRESH_PLAYERS_INTERVAL = 4 * 3600
REFRESH_CLANS_INTERVAL = 12 * 3600
REFRESH_WARS_INTERVAL = 30 * 60
SYNC_TOP_CLANS_INTERVAL = 168 * 3600

SNAPSHOT_RANKS_TIME = dt_time(hour=6, minute=50, tzinfo=WARSAW)
PRE_RESET_SWEEP_TIME = dt_time(hour=6, minute=55, tzinfo=WARSAW)


async def _run_periodic(name: str, interval_seconds: float, coro_fn, *, initial_delay: float = 0):
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await coro_fn()
        except Exception as e:
            print(f"[{name}] unhandled error: {e}")
        await asyncio.sleep(interval_seconds)


async def _run_daily_at(name: str, target_time: dt_time, coro_fn):
    while True:
        now = datetime.now(WARSAW)
        target = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await coro_fn()
        except Exception as e:
            print(f"[{name}] unhandled error: {e}")


async def _pre_reset_sweep():
    print("[pre_reset_sweep] Starting sweep...")
    await refresh_all_players(concurrency=5, sleep=0.2)
    print("[pre_reset_sweep] Done.")


async def main():
    init_db()
    print("[worker] Starting background data-fetch worker")

    await asyncio.gather(
        _run_periodic("refresh_players", REFRESH_PLAYERS_INTERVAL, refresh_all_players, initial_delay=10),
        _run_periodic("refresh_clans", REFRESH_CLANS_INTERVAL, refresh_all_clans, initial_delay=30),
        _run_periodic("refresh_wars", REFRESH_WARS_INTERVAL, refresh_all_wars, initial_delay=60),
        _run_periodic("sync_top_clans", SYNC_TOP_CLANS_INTERVAL, sync_top_clans),
        _run_daily_at("snapshot_ranks", SNAPSHOT_RANKS_TIME, snapshot_ranks),
        _run_daily_at("pre_reset_sweep", PRE_RESET_SWEEP_TIME, _pre_reset_sweep),
    )


if __name__ == "__main__":
    asyncio.run(main())
