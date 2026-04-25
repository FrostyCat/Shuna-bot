import json
import os
import secrets

import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from db import Session as DBSession
import re
from models import Transcript, TicketPanel, TicketType

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


def sanitize_type(name: str) -> str:
    return re.sub(r'[^a-z0-9_]', '', name.strip().lower().replace(' ', '_'))


def fetch_discord_message(channel_id: str, message_id: str) -> dict:
    r = requests.get(f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
                     headers={"Authorization": f"Bot {BOT_TOKEN}"})
    return r.json() if r.ok else {}


def sync_panel_buttons(panel, ticket_types: list):
    buttons = [
        {
            "type": 2,
            "label": tt.name,
            "style": tt.button_color or 1,
            "emoji": {"name": "🎫"},
            "custom_id": f"ticket:open:{tt.name.lower()}",
        }
        for tt in ticket_types[:5]
    ]
    requests.patch(
        f"{DISCORD_API}/channels/{panel.channel_id}/messages/{panel.message_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"components": [{"type": 1, "components": buttons}] if buttons else []},
    )


def build_panel_embed(form) -> dict:
    color_hex = form.get("panel_color") or "5865f2"
    try:
        color_int = int(color_hex.lstrip("#"), 16)
    except ValueError:
        color_int = 0x5865F2
    embed = {
        "title": form.get("panel_title") or "🎫 Support",
        "description": form.get("panel_description") or "",
        "color": color_int,
    }
    thumbnail = form.get("panel_thumbnail") or None
    image = form.get("panel_image") or None
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}
    if image:
        embed["image"] = {"url": image}
    return embed


def guild_text_channels(guild_id: str) -> list:
    r = requests.get(f"{DISCORD_API}/guilds/{guild_id}/channels",
                     headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if not r.ok:
        return []
    return sorted([c for c in r.json() if c["type"] == 0], key=lambda c: c["position"])


def guild_icon_url(guild: dict) -> str | None:
    if guild.get("icon"):
        return f"https://cdn.discordapp.com/icons/{guild['id']}/{guild['icon']}.png"
    return None


def require_guild(guild_id: str):
    if "user" not in session:
        return None, redirect(url_for("index"))
    bot_ids = bot_guild_ids()
    if guild_id not in bot_ids:
        return None, abort(403)
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    resp = requests.get(f"{DISCORD_API}/users/@me/guilds", headers=headers)
    if not resp.ok:
        # Token expired or invalid — force re-login
        session.clear()
        return None, redirect(url_for("index"))
    user_guilds = resp.json()
    if not isinstance(user_guilds, list):
        session.clear()
        return None, redirect(url_for("index"))
    guild = next((g for g in user_guilds if g["id"] == guild_id and (int(g["permissions"]) & ADMINISTRATOR)), None)
    if not guild:
        return None, abort(403)
    guild["icon_url"] = guild_icon_url(guild)
    return guild, None


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
    user = requests.get(f"{DISCORD_API}/users/@me", headers=headers).json()
    session["user"] = {"id": user["id"], "username": user["username"], "avatar": user.get("avatar")}
    session["access_token"] = access_token
    return redirect(url_for("guilds"))


@app.route("/guilds")
def guilds():
    if "user" not in session:
        return redirect(url_for("index"))
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    user_guilds = requests.get(f"{DISCORD_API}/users/@me/guilds", headers=headers).json()
    bot_ids = bot_guild_ids()
    manageable = [g for g in user_guilds if (int(g["permissions"]) & ADMINISTRATOR) and g["id"] in bot_ids]
    for g in manageable:
        g["icon_url"] = guild_icon_url(g)
    return render_template("guilds.html", user=session["user"], guilds=manageable)


@app.route("/dashboard/<guild_id>")
def dashboard(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    return render_template("dashboard.html", user=session["user"], guild=guild)


@app.route("/dashboard/<guild_id>/tickets")
def tickets(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    panels = db.query(TicketPanel).filter_by(guild_id=guild_id).all()
    db.close()
    return render_template("tickets.html", user=session["user"], guild=guild, panels=panels)


@app.route("/dashboard/<guild_id>/tickets/new", methods=["GET", "POST"])
def ticket_panel_new(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err

    channels = guild_text_channels(guild_id)

    if request.method == "POST":
        channel_id = request.form.get("channel_id", "")
        types_raw = request.form.get("types", "Support")
        types = [t.strip() for t in types_raw.split(",") if t.strip()] or ["Support"]

        embed = build_panel_embed(request.form)

        buttons = [
            {
                "type": 2,
                "label": t,
                "style": 1,
                "emoji": {"name": "🎫"},
                "custom_id": f"ticket:open:{t.lower()}",
            }
            for t in types[:5]
        ]
        components = [{"type": 1, "components": buttons}]

        r = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
            json={"embeds": [embed], "components": components},
        )
        if not r.ok:
            flash(f"Discord error: {r.json().get('message', 'Unknown error')}", "danger")
            return render_template("ticket_panel_new.html", user=session["user"],
                                   guild=guild, channels=channels)

        message_id = r.json()["id"]

        db = DBSession()
        panel = TicketPanel(
            guild_id=guild_id,
            message_id=message_id,
            channel_id=channel_id,
            types=",".join(types),
        )
        db.add(panel)
        db.flush()
        for t in types:
            db.add(TicketType(panel_id=panel.id, name=t))
        db.commit()
        db.close()

        flash("Panel created!", "success")
        return redirect(url_for("tickets", guild_id=guild_id))

    return render_template("ticket_panel_new.html", user=session["user"],
                           guild=guild, channels=channels)


@app.route("/dashboard/<guild_id>/tickets/<int:panel_id>", methods=["GET", "POST"])
def ticket_panel(guild_id, panel_id):
    guild, err = require_guild(guild_id)
    if err:
        return err

    db = DBSession()
    panel = db.query(TicketPanel).filter_by(id=panel_id, guild_id=guild_id).first()
    if not panel:
        db.close()
        abort(404)

    if request.method == "POST":
        channel_id = panel.channel_id
        message_id = panel.message_id
        db.commit()
        db.close()
        new_embed = build_panel_embed(request.form)
        requests.patch(
            f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
            headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
            json={"embeds": [new_embed]},
        )
        flash("Panel updated!", "success")
        return redirect(url_for("ticket_panel", guild_id=guild_id, panel_id=panel_id))

    channel_id = panel.channel_id
    message_id = panel.message_id
    ticket_types = db.query(TicketType).filter_by(panel_id=panel.id).all()
    db.close()
    discord_msg = fetch_discord_message(channel_id, message_id)
    current_embed = discord_msg.get("embeds", [{}])[0] if discord_msg.get("embeds") else {}
    return render_template("ticket_panel.html", user=session["user"], guild=guild,
                           panel=panel, current_embed=current_embed, ticket_types=ticket_types)


def _get_panel_or_404(db, panel_id, guild_id):
    panel = db.query(TicketPanel).filter_by(id=panel_id, guild_id=guild_id).first()
    if not panel:
        db.close()
        abort(404)
    return panel


@app.route("/dashboard/<guild_id>/tickets/<int:panel_id>/types/new", methods=["GET", "POST"])
def ticket_type_new(guild_id, panel_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    panel = _get_panel_or_404(db, panel_id, guild_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Button name is required.", "danger")
            db.close()
            return render_template("ticket_type.html", user=session["user"], guild=guild,
                                   panel=panel, tt=None)
        db.add(TicketType(
            panel_id=panel.id,
            name=name,
            button_color=int(request.form.get("button_color", 1)),
            msg_title=request.form.get("msg_title") or None,
            msg_description=request.form.get("msg_description") or None,
            msg_color=request.form.get("msg_color") or None,
            msg_thumbnail=request.form.get("msg_thumbnail") or None,
            msg_image=request.form.get("msg_image") or None,
        ))
        db.commit()
        sync_panel_buttons(panel, db.query(TicketType).filter_by(panel_id=panel.id).all())
        db.close()
        flash("Button added!", "success")
        return redirect(url_for("ticket_panel", guild_id=guild_id, panel_id=panel_id))

    db.close()
    return render_template("ticket_type.html", user=session["user"], guild=guild,
                           panel=panel, tt=None)


@app.route("/dashboard/<guild_id>/tickets/<int:panel_id>/types/<int:type_id>", methods=["GET", "POST"])
def ticket_type_edit(guild_id, panel_id, type_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    panel = _get_panel_or_404(db, panel_id, guild_id)
    tt = db.query(TicketType).filter_by(id=type_id, panel_id=panel.id).first()
    if not tt:
        db.close()
        abort(404)

    if request.method == "POST":
        tt.name = request.form.get("name", "").strip() or tt.name
        tt.button_color = int(request.form.get("button_color", 1))
        tt.msg_title = request.form.get("msg_title") or None
        tt.msg_description = request.form.get("msg_description") or None
        tt.msg_color = request.form.get("msg_color") or None
        tt.msg_thumbnail = request.form.get("msg_thumbnail") or None
        tt.msg_image = request.form.get("msg_image") or None
        db.commit()
        sync_panel_buttons(panel, db.query(TicketType).filter_by(panel_id=panel.id).all())
        db.close()
        flash("Button updated!", "success")
        return redirect(url_for("ticket_panel", guild_id=guild_id, panel_id=panel_id))

    db.close()
    return render_template("ticket_type.html", user=session["user"], guild=guild,
                           panel=panel, tt=tt)


@app.route("/dashboard/<guild_id>/tickets/<int:panel_id>/types/<int:type_id>/delete", methods=["POST"])
def ticket_type_delete(guild_id, panel_id, type_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    panel = _get_panel_or_404(db, panel_id, guild_id)
    tt = db.query(TicketType).filter_by(id=type_id, panel_id=panel.id).first()
    if tt:
        db.delete(tt)
        db.commit()
        sync_panel_buttons(panel, db.query(TicketType).filter_by(panel_id=panel.id).all())
    db.close()
    flash("Button deleted.", "success")
    return redirect(url_for("ticket_panel", guild_id=guild_id, panel_id=panel_id))


@app.route("/dashboard/<guild_id>/tickets/<int:panel_id>/delete", methods=["POST"])
def ticket_panel_delete(guild_id, panel_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    panel = db.query(TicketPanel).filter_by(id=panel_id, guild_id=guild_id).first()
    if panel:
        db.delete(panel)
        db.commit()
    db.close()
    flash("Panel deleted.", "success")
    return redirect(url_for("tickets", guild_id=guild_id))


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
