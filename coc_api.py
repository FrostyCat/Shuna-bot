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
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            print(f"API error {response.status}: {await response.text()}")
            return None

async def get_battlelog(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/players/{tag}/battlelog")
    return data["items"] if data else []

async def get_player(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/players/{tag}")
    if not data:
        return None
    current_season = data.get("legendStatistics", {}).get("currentSeason", {})
    season_trophies = current_season.get("trophies")
    rank = current_season.get("rank")
    return (data["tag"], data["name"], season_trophies, rank)

async def get_clan(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}")
    return (data["tag"], data["name"]) if data else None

async def get_clan_members(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}/members")
    return data["items"] if data else []

async def verify_player_token(tag: str, token: str) -> bool:
    tag = tag.replace("#", "%23")
    url = f"{BASE_URL}/players/{tag}/verifytoken"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={"token": token}) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("status") == "ok"
            return False
