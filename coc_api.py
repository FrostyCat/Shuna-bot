import requests
import os
from dotenv import load_dotenv

from models import Player

load_dotenv()

API_KEY = os.getenv("COC_API_KEY")
BASE_URL = "https://api.clashofclans.com/v1"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

def get_battlelog(tag):
    tag = tag.replace("#", "%23")
    url = f"{BASE_URL}/players/{tag}/battlelog"

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()["items"]
    else:
        print(response.text)
        return []
    
def get_player(tag):
    # 1. fetch z API
    tag = tag.replace("#", "%23")
    url = f"{BASE_URL}/players/{tag}"

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()["tag"], response.json()["name"]
    else:        
        print(response.text)
        return []