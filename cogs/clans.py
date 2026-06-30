import asyncio

import discord

from coc_api import get_clan
from db import Session
from helpers import fetch_cwl_attacks
from models import Clan, GuildClan


def _require_admin(ctx: discord.ApplicationContext) -> bool:
    return (
        ctx.author.guild_permissions.administrator
        or ctx.author.guild_permissions.manage_guild
    )


async def _guild_clan_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    guild_id = str(ctx.interaction.guild_id)
    current = f"%{ctx.value}%"
    rows = (
        session.query(GuildClan)
        .filter(
            GuildClan.guild_id == guild_id,
            (GuildClan.clan_name.ilike(current)) | (GuildClan.clan_tag.ilike(current)),
        )
        .order_by(GuildClan.sort_order)
        .limit(25)
        .all()
    )
    session.close()
    return [
        discord.OptionChoice(name=f"{gc.clan_name or gc.clan_tag} ({gc.clan_tag})", value=gc.clan_tag)
        for gc in rows
    ]


class ClansCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    clan = discord.SlashCommandGroup("clan", "Clan registration for this server")

    @clan.command(name="add", description="Register a clan so the bot tracks its CWL and war data")
    async def add(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Clan tag, e.g. #ABC123"),
    ):
        if not _require_admin(ctx):
            await ctx.respond("❌ You need Manage Server permission.", ephemeral=True)
            return

        await ctx.defer()

        tag = tag.upper().strip().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        guild_id = str(ctx.guild_id)
        loop = asyncio.get_running_loop()

        def _check_existing():
            session = Session()
            try:
                return session.query(GuildClan).filter_by(guild_id=guild_id, clan_tag=tag).first()
            finally:
                session.close()

        if await loop.run_in_executor(None, _check_existing):
            await ctx.followup.send(f"❌ Clan `{tag}` is already registered on this server.")
            return

        data = await get_clan(tag)
        if not data:
            await ctx.followup.send(f"❌ Clan `{tag}` not found. Check the tag and try again.")
            return

        clan_tag, clan_name = data

        def _insert():
            session = Session()
            try:
                if not session.query(Clan).filter_by(tag=clan_tag).first():
                    session.add(Clan(tag=clan_tag, name=clan_name))
                gc = GuildClan(guild_id=guild_id, clan_tag=clan_tag, clan_name=clan_name)
                session.add(gc)
                session.commit()
            finally:
                session.close()

        await loop.run_in_executor(None, _insert)

        # kick off first CWL fetch immediately
        session = Session()
        try:
            await fetch_cwl_attacks(session, clan_tag)
            session.commit()
        except Exception as e:
            print(f"[clan add] initial CWL fetch failed for {clan_tag}: {e}")
        finally:
            session.close()

        embed = discord.Embed(
            title="✅ Clan Registered",
            description=f"**{clan_name}** (`{clan_tag}`) has been added to this server.\nThe bot will now track CWL and war attacks for this clan.",
            color=0x8B4513,
        )
        await ctx.followup.send(embed=embed)

    @clan.command(name="remove", description="Unregister a clan from this server")
    async def remove(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Clan tag", autocomplete=_guild_clan_autocomplete),
    ):
        if not _require_admin(ctx):
            await ctx.respond("❌ You need Manage Server permission.", ephemeral=True)
            return

        await ctx.defer()

        tag = tag.upper().strip().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        guild_id = str(ctx.guild_id)
        loop = asyncio.get_running_loop()

        def _delete():
            session = Session()
            try:
                gc = session.query(GuildClan).filter_by(guild_id=guild_id, clan_tag=tag).first()
                if not gc:
                    return None
                name = gc.clan_name or tag
                session.delete(gc)
                session.commit()
                return name
            finally:
                session.close()

        name = await loop.run_in_executor(None, _delete)
        if name is None:
            await ctx.followup.send(f"❌ Clan `{tag}` is not registered on this server.")
            return

        embed = discord.Embed(
            description=f"**{name}** (`{tag}`) removed from this server.",
            color=0x8B4513,
        )
        await ctx.followup.send(embed=embed)

    @clan.command(name="list", description="Show all clans registered on this server")
    async def list(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        guild_id = str(ctx.guild_id)
        loop = asyncio.get_running_loop()

        def _fetch():
            session = Session()
            try:
                return (
                    session.query(GuildClan)
                    .filter_by(guild_id=guild_id)
                    .order_by(GuildClan.sort_order, GuildClan.clan_name)
                    .all()
                )
            finally:
                session.close()

        clans = await loop.run_in_executor(None, _fetch)

        if not clans:
            await ctx.followup.send("No clans registered on this server yet. Use `/clan add` to add one.")
            return

        header = f"‎`{'#':>3}  {'Tag':<12} {'Clan'}`"
        lines = [header]
        for i, gc in enumerate(clans, 1):
            lines.append(f"‎`{i:>3}  {gc.clan_tag:<12} {gc.clan_name or '—'}`")

        embed = discord.Embed(
            title=f"⚔️ Registered Clans — {ctx.guild.name}",
            description="\n".join(lines),
            color=0x8B4513,
        )
        embed.set_footer(text=f"{len(clans)} clan(s)")
        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(ClansCog(bot))
