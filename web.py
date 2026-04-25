import json
import os
import secrets

import requests
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, session, url_for

from db import Session as DBSession
from models import Transcript

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "https://shuna-bot.com/callback")
DISCORD_API = "https://discord.com/api/v10"
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

ADMINISTRATOR = 0x8


def bot_guild_ids() -> set:
    r = requests.get(f"{DISCORD_API}/users/@me/guilds", headers={"Authorization": f"Bot {BOT_TOKEN}"})
    return {g["id"] for g in r.json()} if r.ok else set()


def guild_icon_url(guild: dict) -> str | None:
    if guild.get("icon"):
        return f"https://cdn.discordapp.com/icons/{guild['id']}/{guild['icon']}.png"
    return None


@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("guilds"))
    return render_template("login.html")


@app.route("/login")
def login():
    state = secrets.token_hex(16)
    session["oauth_state"] = state
    params = "&".join([
        f"client_id={DISCORD_CLIENT_ID}",
        f"redirect_uri={DISCORD_REDIRECT_URI}",
        "response_type=code",
        "scope=identify%20guilds",
        f"state={state}",
    ])
    return redirect(f"https://discord.com/oauth2/authorize?{params}")


@app.route("/callback")
def callback():
    if request.args.get("error"):
        return redirect(url_for("index"))

    if request.args.get("state") != session.pop("oauth_state", None):
        abort(403)

    token_resp = requests.post(f"{DISCORD_API}/oauth2/token", data={
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": request.args.get("code"),
        "redirect_uri": DISCORD_REDIRECT_URI,
    })
    access_token = token_resp.json().get("access_token")
    if not access_token:
        return redirect(url_for("index"))

    headers = {"Authorization": f"Bearer {access_token}"}
    session["user"] = requests.get(f"{DISCORD_API}/users/@me", headers=headers).json()
    session["guilds"] = requests.get(f"{DISCORD_API}/users/@me/guilds", headers=headers).json()

    return redirect(url_for("guilds"))


@app.route("/guilds")
def guilds():
    if "user" not in session:
        return redirect(url_for("index"))

    bot_ids = bot_guild_ids()
    manageable = [
        g for g in session.get("guilds", [])
        if (int(g["permissions"]) & ADMINISTRATOR) and g["id"] in bot_ids
    ]
    for g in manageable:
        g["icon_url"] = guild_icon_url(g)

    return render_template("guilds.html", user=session["user"], guilds=manageable)


@app.route("/dashboard/<guild_id>")
def dashboard(guild_id):
    if "user" not in session:
        return redirect(url_for("index"))

    bot_ids = bot_guild_ids()
    if guild_id not in bot_ids:
        abort(403)

    guild = next((
        g for g in session.get("guilds", [])
        if g["id"] == guild_id and (int(g["permissions"]) & ADMINISTRATOR)
    ), None)
    if not guild:
        abort(403)

    guild["icon_url"] = guild_icon_url(guild)
    return render_template("dashboard.html", user=session["user"], guild=guild)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/transcript/<string:token>")
def transcript(token):
    db = DBSession()
    t = db.query(Transcript).filter_by(token=token).first()
    db.close()
    if not t:
        abort(404)
    messages = json.loads(t.messages)
    return render_template("transcript.html", transcript=t, messages=messages)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
