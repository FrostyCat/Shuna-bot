import json
import os
import secrets
from datetime import datetime, timedelta, UTC

import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from sqlalchemy import and_, or_
from db import Session as DBSession
import re
from models import Transcript, TicketPanel, TicketType, GuildConfig, GuildClan, Player, Attack, WarAttack, Clan, DiscordUser

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
app.jinja_env.filters["from_json"] = json.loads

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "https://shuna-bot.com/callback")
DISCORD_API = "https://discord.com/api/v10"
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

ADMINISTRATOR = 0x8

COC_API_KEY = os.getenv("COC_API_KEY")
COC_BASE_URL = "https://api.clashofclans.com/v1"


def coc_get(path: str):
    r = requests.get(f"{COC_BASE_URL}{path}",
                     headers={"Authorization": f"Bearer {COC_API_KEY}"})
    return r.json() if r.ok else None


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


def guild_roles(guild_id: str) -> list:
    r = requests.get(f"{DISCORD_API}/guilds/{guild_id}/roles",
                     headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if not r.ok:
        return []
    return sorted([ro for ro in r.json() if ro["name"] != "@everyone"],
                  key=lambda ro: -ro["position"])


def guild_categories(guild_id: str) -> list:
    r = requests.get(f"{DISCORD_API}/guilds/{guild_id}/channels",
                     headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if not r.ok:
        return []
    return sorted([c for c in r.json() if c["type"] == 4], key=lambda c: c["position"])


def guild_members(guild_id: str) -> list:
    r = requests.get(f"{DISCORD_API}/guilds/{guild_id}/members",
                     params={"limit": 1000},
                     headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if not r.ok:
        return []
    return sorted(
        [{"id": m["user"]["id"],
          "username": m["nick"] or m["user"]["username"],
          "roles": m.get("roles", [])}
         for m in r.json() if not m["user"].get("bot")],
        key=lambda u: u["username"].lower()
    )


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
        channel_id, message_id = panel.channel_id, panel.message_id
        db.delete(panel)
        db.commit()
        db.close()
        requests.delete(
            f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
        )
    else:
        db.close()
    flash("Panel deleted.", "success")
    return redirect(url_for("tickets", guild_id=guild_id))


@app.route("/dashboard/<guild_id>/transcripts")
def transcripts(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    items = (db.query(Transcript)
               .filter_by(guild_id=guild_id)
               .order_by(Transcript.closed_at.desc())
               .all())
    db.close()
    return render_template("transcripts.html", user=session["user"], guild=guild, transcripts=items)


@app.route("/dashboard/<guild_id>/settings", methods=["GET", "POST"])
def settings(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err

    roles = guild_roles(guild_id)
    channels = guild_text_channels(guild_id)
    categories = guild_categories(guild_id)

    db = DBSession()
    config = db.query(GuildConfig).filter_by(guild_id=guild_id).first()

    if request.method == "POST":
        if not config:
            config = GuildConfig(guild_id=guild_id)
            db.add(config)
        config.staff_role_id = request.form.get("staff_role_id") or None
        config.log_channel_id = request.form.get("log_channel_id") or None
        config.ticket_category_id = request.form.get("ticket_category_id") or None
        db.commit()
        db.close()
        flash("Settings saved!", "success")
        return redirect(url_for("settings", guild_id=guild_id))

    db.close()
    return render_template("settings.html", user=session["user"], guild=guild,
                           config=config, roles=roles, channels=channels, categories=categories)


@app.route("/dashboard/<guild_id>/coc")
def coc_manager(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    clans = db.query(GuildClan).filter_by(guild_id=guild_id).all()
    db.close()
    roles = guild_roles(guild_id)
    return render_template("coc.html", user=session["user"], guild=guild, clans=clans, roles=roles)


@app.route("/dashboard/<guild_id>/coc/role")
def coc_role_stats(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err

    role_id = request.args.get("role_id", "").strip()
    roles = guild_roles(guild_id)
    role = next((r for r in roles if r["id"] == role_id), None)
    if not role_id or not role:
        return redirect(url_for("coc_manager", guild_id=guild_id))

    all_members = guild_members(guild_id)
    role_members = [m for m in all_members if role_id in m["roles"]]

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    three_months_ago = now - timedelta(days=90)

    db = DBSession()
    stats = []

    for member in role_members:
        discord_id = member["id"]
        discord_username = member["username"]

        db_user = db.query(DiscordUser).filter_by(discord_id=discord_id).first()
        players = db_user.players if db_user else []

        if not players:
            stats.append({
                "discord_id": discord_id,
                "discord_username": discord_username,
                "tag": None,
                "name": None,
                "in_db": False,
                "war_month": 0, "cwl_month": 0,
                "war_3mo": 0,   "cwl_3mo": 0,
                "cwl_league": None, "legend_month": 0,
            })
        else:
            for player in players:
                war_month = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "war",
                    WarAttack.created_at >= month_start,
                ).count()
                cwl_month = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "cwl",
                    WarAttack.created_at >= month_start,
                ).count()
                war_3mo = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "war",
                    WarAttack.created_at >= three_months_ago,
                ).count()
                cwl_3mo = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "cwl",
                    WarAttack.created_at >= three_months_ago,
                ).count()
                legend_month = db.query(Attack).filter(
                    Attack.player_id == player.id,
                    Attack.is_attack == True,
                    Attack.created_at >= month_start,
                ).count()
                war_month_3star = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "war",
                    WarAttack.stars == 3,
                    WarAttack.created_at >= month_start,
                ).count()
                war_clean_total = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "war",
                    WarAttack.created_at >= month_start,
                    or_(WarAttack.stars >= 2,
                        and_(WarAttack.stars == 1, WarAttack.destruction >= 50)),
                ).count()
                legend_month_3star = db.query(Attack).filter(
                    Attack.player_id == player.id,
                    Attack.is_attack == True,
                    Attack.stars == 3,
                    Attack.created_at >= month_start,
                ).count()
                war_loot = db.query(WarAttack).filter(
                    WarAttack.attacker_tag == player.tag,
                    WarAttack.war_type == "war",
                    WarAttack.stars == 1,
                    WarAttack.destruction < 50,
                    WarAttack.created_at >= month_start,
                ).count()
                cwl_league = (
                    db.query(WarAttack.league)
                    .filter(
                        WarAttack.attacker_tag == player.tag,
                        WarAttack.war_type == "cwl",
                        WarAttack.league.isnot(None),
                    )
                    .order_by(WarAttack.created_at.desc())
                    .limit(1)
                    .scalar()
                )
                stats.append({
                    "discord_id": discord_id,
                    "discord_username": discord_username,
                    "tag": player.tag,
                    "name": player.name,
                    "in_db": True,
                    "war_month": war_month, "cwl_month": cwl_month,
                    "war_3mo": war_3mo,     "cwl_3mo": cwl_3mo,
                    "cwl_league": cwl_league, "legend_month": legend_month,
                    "war_month_3star": war_month_3star,
                    "war_clean_total": war_clean_total,
                    "legend_month_3star": legend_month_3star,
                    "war_loot": war_loot,
                })

    db.close()
    return render_template("coc_role.html", user=session["user"], guild=guild,
                           role=role, stats=stats, roles=roles)


@app.route("/dashboard/<guild_id>/coc/search")
def coc_clan_search(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return {"results": []}
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return {"results": []}
    data = coc_get(f"/clans?name={requests.utils.quote(q)}&limit=8")
    if not data:
        return {"results": []}
    return {"results": [
        {"tag": c["tag"], "name": c["name"], "members": c.get("members", 0)}
        for c in data.get("items", [])
    ]}


@app.route("/dashboard/<guild_id>/coc/add", methods=["POST"])
def coc_clan_add(guild_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    tag = request.form.get("clan_tag", "").strip().upper().replace("O", "0")
    if not tag.startswith("#"):
        tag = "#" + tag

    data = coc_get(f"/clans/{tag.replace('#', '%23')}")
    if not data or "tag" not in data:
        flash("Clan not found. Check the tag.", "danger")
        return redirect(url_for("coc_manager", guild_id=guild_id))

    clan_name = data.get("name", "")
    db = DBSession()
    if db.query(GuildClan).filter_by(guild_id=guild_id, clan_tag=tag).first():
        flash("This clan is already added.", "danger")
        db.close()
        return redirect(url_for("coc_manager", guild_id=guild_id))

    db.add(GuildClan(guild_id=guild_id, clan_tag=tag, clan_name=clan_name))
    if not db.query(Clan).filter_by(tag=tag).first():
        db.add(Clan(tag=tag, name=clan_name))
    db.commit()
    db.close()
    flash(f"Clan {clan_name} added!", "success")
    return redirect(url_for("coc_manager", guild_id=guild_id))


@app.route("/dashboard/<guild_id>/coc/<int:gc_id>")
def coc_clan(guild_id, gc_id):
    guild, err = require_guild(guild_id)
    if err:
        return err

    db = DBSession()
    gc = db.query(GuildClan).filter_by(id=gc_id, guild_id=guild_id).first()
    if not gc:
        db.close()
        abort(404)

    clan_data = coc_get(f"/clans/{gc.clan_tag.replace('#', '%23')}/members")
    members_api = clan_data.get("items", []) if clan_data else []

    # Ensure every current clan member exists in the players table
    new_players = False
    for m in members_api:
        if not db.query(Player).filter_by(tag=m["tag"]).first():
            db.add(Player(tag=m["tag"], name=m["name"]))
            new_players = True
    if new_players:
        db.commit()

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    three_months_ago = now - timedelta(days=90)

    stats = []
    for m in members_api:
        member_tag = m.get("tag")
        member_name = m.get("name")

        player = db.query(Player).filter_by(tag=member_tag).first()

        discord_id = None
        war_month = 0
        war_3mo = 0
        legend_month = 0

        war_month = cwl_month = war_3mo = cwl_3mo = legend_month = 0
        cwl_league = None

        if player:
            if player.discord_user:
                discord_id = player.discord_user.discord_id
            war_month = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "war",
                WarAttack.created_at >= month_start,
            ).count()
            cwl_month = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "cwl",
                WarAttack.created_at >= month_start,
            ).count()
            war_3mo = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "war",
                WarAttack.created_at >= three_months_ago,
            ).count()
            cwl_3mo = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "cwl",
                WarAttack.created_at >= three_months_ago,
            ).count()
            legend_month = db.query(Attack).filter(
                Attack.player_id == player.id,
                Attack.is_attack == True,
                Attack.created_at >= month_start,
            ).count()
            war_month_3star = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "war",
                WarAttack.stars == 3,
                WarAttack.created_at >= month_start,
            ).count()
            war_clean_total = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "war",
                WarAttack.created_at >= month_start,
                or_(WarAttack.stars >= 2,
                    and_(WarAttack.stars == 1, WarAttack.destruction >= 50)),
            ).count()
            legend_month_3star = db.query(Attack).filter(
                Attack.player_id == player.id,
                Attack.is_attack == True,
                Attack.stars == 3,
                Attack.created_at >= month_start,
            ).count()
            war_loot = db.query(WarAttack).filter(
                WarAttack.attacker_tag == member_tag,
                WarAttack.war_type == "war",
                WarAttack.stars == 1,
                WarAttack.destruction < 50,
                WarAttack.created_at >= month_start,
            ).count()
            cwl_league = (
                db.query(WarAttack.league)
                .filter(
                    WarAttack.attacker_tag == member_tag,
                    WarAttack.war_type == "cwl",
                    WarAttack.league.isnot(None),
                )
                .order_by(WarAttack.created_at.desc())
                .limit(1)
                .scalar()
            )

        stats.append({
            "tag": member_tag,
            "name": member_name,
            "discord_id": discord_id,
            "war_month": war_month,
            "cwl_month": cwl_month,
            "war_3mo": war_3mo,
            "cwl_3mo": cwl_3mo,
            "cwl_league": cwl_league,
            "legend_month": legend_month,
            "war_month_3star": war_month_3star if player else 0,
            "war_clean_total": war_clean_total if player else 0,
            "legend_month_3star": legend_month_3star if player else 0,
            "war_loot": war_loot if player else 0,
            "in_db": player is not None,
        })

    # Build discord_id → username map from guild members
    members_list = guild_members(guild_id)
    member_map = {m["id"]: m["username"] for m in members_list}

    for s in stats:
        if s["discord_id"]:
            s["discord_username"] = member_map.get(s["discord_id"], s["discord_id"])
        else:
            s["discord_username"] = None

    db.close()
    return render_template("coc_clan.html", user=session["user"], guild=guild, gc=gc,
                           stats=stats, members=members_list)


@app.route("/dashboard/<guild_id>/coc/<int:gc_id>/link", methods=["POST"])
def coc_link_player(guild_id, gc_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    player_tag = request.form.get("player_tag", "").strip()
    discord_id = request.form.get("discord_id", "").strip()
    if not player_tag or not discord_id:
        flash("Select a Discord user.", "danger")
        return redirect(url_for("coc_clan", guild_id=guild_id, gc_id=gc_id))
    db = DBSession()
    player = db.query(Player).filter_by(tag=player_tag).first()
    if not player:
        flash("Player not found.", "danger")
        db.close()
        return redirect(url_for("coc_clan", guild_id=guild_id, gc_id=gc_id))
    discord_user = db.query(DiscordUser).filter_by(discord_id=discord_id).first()
    if not discord_user:
        discord_user = DiscordUser(discord_id=discord_id)
        db.add(discord_user)
        db.flush()
    player.discord_user_id = discord_user.id
    db.commit()
    db.close()
    flash("Player linked!", "success")
    return redirect(url_for("coc_clan", guild_id=guild_id, gc_id=gc_id))


@app.route("/dashboard/<guild_id>/coc/<int:gc_id>/unlink", methods=["POST"])
def coc_unlink_player(guild_id, gc_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    player_tag = request.form.get("player_tag", "").strip()
    db = DBSession()
    player = db.query(Player).filter_by(tag=player_tag).first()
    if player:
        player.discord_user_id = None
        db.commit()
        flash("Player unlinked.", "success")
    db.close()
    return redirect(url_for("coc_clan", guild_id=guild_id, gc_id=gc_id))


@app.route("/dashboard/<guild_id>/coc/<int:gc_id>/remove", methods=["POST"])
def coc_clan_remove(guild_id, gc_id):
    guild, err = require_guild(guild_id)
    if err:
        return err
    db = DBSession()
    gc = db.query(GuildClan).filter_by(id=gc_id, guild_id=guild_id).first()
    if gc:
        db.delete(gc)
        db.commit()
        flash("Clan removed.", "success")
    db.close()
    return redirect(url_for("coc_manager", guild_id=guild_id))


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
