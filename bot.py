import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db import Session, init_db
from models import Player
from utils import fetch_player_attacks

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

COGS = [
    "cogs.legend",
    "cogs.link",
    "cogs.tasks",
]


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command()
async def fetch(ctx):
    session = Session()
    players = session.query(Player).all()

    if not players:
        await ctx.send("No accounts in database ❗ Add via /legend_day")
        session.close()
        return

    total_count = 0
    for p in players:
        total_count += await fetch_player_attacks(session, p)

    session.commit()
    session.close()
    await ctx.send(f"Added {total_count} new attacks 🚀")


if __name__ == "__main__":
    init_db()
    for cog in COGS:
        bot.load_extension(cog)
    bot.run(os.getenv("DISCORD_TOKEN"))
