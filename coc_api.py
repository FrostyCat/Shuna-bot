import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("COC_API_KEY")
BASE_URL = "https://api.clashofclans.com/v1"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

async def _get(url):
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                print(f"API error {response.status}: {await response.text()}")
                return None
        except asyncio.TimeoutError:
            print(f"API timeout: {url}")
            return None
        except aiohttp.ClientError as e:
            print(f"API connection error: {e}")
            return None

async def get_battlelog(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/players/{tag}/battlelog")
    return data["items"] if data else []

async def get_player_profile(tag: str) -> dict | None:
    tag = tag.replace("#", "%23")
    return await _get(f"{BASE_URL}/players/{tag}")


async def get_player(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/players/{tag}")
    if not data:
        return None
    current_season = data.get("legendStatistics", {}).get("currentSeason", {})
    season_trophies = current_season.get("trophies")
    rank = current_season.get("rank")
    league_tier = data.get("leagueTier", {}).get("name", "")
    if season_trophies is None:
        season_trophies = data.get("trophies")
    return (data["tag"], data["name"], season_trophies, rank, data.get("townHallLevel"), league_tier)

async def get_clan(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}")
    return (data["tag"], data["name"]) if data else None

async def get_clan_war_league(tag: str) -> str | None:
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}")
    if not data:
        return None
    return data.get("warLeague", {}).get("name")

async def get_clan_members(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}/members")
    return data["items"] if data else []

async def get_current_war(clan_tag: str) -> dict | None:
    clan_tag = clan_tag.replace("#", "%23")
    return await _get(f"{BASE_URL}/clans/{clan_tag}/currentwar")


async def get_cwl_group(clan_tag: str) -> dict | None:
    clan_tag = clan_tag.replace("#", "%23")
    return await _get(f"{BASE_URL}/clans/{clan_tag}/currentwar/leaguegroup")


async def get_cwl_war(war_tag: str) -> dict | None:
    war_tag = war_tag.replace("#", "%23")
    return await _get(f"{BASE_URL}/clanwarleagues/wars/{war_tag}")


async def verify_player_token(tag: str, token: str) -> bool:
    tag = tag.replace("#", "%23")
    url = f"{BASE_URL}/players/{tag}/verifytoken"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(url, headers=headers, json={"token": token}) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("status") == "ok"
                return False
        except (asyncio.TimeoutError, aiohttp.ClientError):
            return False
