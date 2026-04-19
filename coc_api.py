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
    return (data["tag"], data["name"]) if data else None

async def get_clan(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}")
    return (data["tag"], data["name"]) if data else None

async def get_clan_members(tag):
    tag = tag.replace("#", "%23")
    data = await _get(f"{BASE_URL}/clans/{tag}/members")
    return data["items"] if data else []
