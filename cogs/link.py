import discord
from discord.ext import commands

from coc_api import get_player, verify_player_token
from db import Session
from models import DiscordUser, Player
from utils import add_player_to_db


async def player_tag_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    players = session.query(Player).all()
    current = ctx.value.lower()
    choices = [
        discord.OptionChoice(name=f"{p.name} ({p.tag})", value=p.tag)
        for p in players
        if current in p.tag.lower() or current in p.name.lower()
    ]
    session.close()
    return choices[:25]


async def linked_tag_autocomplete(ctx: discord.AutocompleteContext):
    session = Session()
    players = session.query(Player).filter(Player.discord_user_id.isnot(None)).all()
    current = ctx.value.lower()
    choices = [
        discord.OptionChoice(name=f"{p.name} ({p.tag})", value=p.tag)
        for p in players
        if current in p.tag.lower() or current in p.name.lower()
    ]
    session.close()
    return choices[:25]


class LinkCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="link", description="Link a Clash of Clans account to Discord")
    async def link(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=player_tag_autocomplete),
        user: discord.Option(discord.Member, "Discord user"),
        api_token: discord.Option(str, "API token from in-game settings"),
    ):
        await ctx.defer(ephemeral=True)

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        valid = await verify_player_token(tag, api_token)
        if not valid:
            await ctx.followup.send("❌ Invalid API token. Check the token in your in-game settings.", ephemeral=True)
            return

        session = Session()
        player = session.query(Player).filter_by(tag=tag).first()
        if not player:
            result = await add_player_to_db(tag, session)
            if not result["success"]:
                await ctx.followup.send("❌ " + result["error"], ephemeral=True)
                session.close()
                return
            player = session.query(Player).filter_by(tag=result["tag"]).first()

        discord_user = session.query(DiscordUser).filter_by(discord_id=str(user.id)).first()
        if not discord_user:
            discord_user = DiscordUser(discord_id=str(user.id))
            session.add(discord_user)
            session.flush()

        if player.discord_user_id == discord_user.id:
            session.close()
            await ctx.followup.send(f"ℹ️ **{player.name}** is already linked to {user.mention}.", ephemeral=True)
            return

        player.discord_user_id = discord_user.id
        session.commit()
        session.close()
        await ctx.followup.send(f"✅ Linked **{player.name}** ({tag}) to {user.mention}.", ephemeral=True)

    @discord.slash_command(
        name="force_link",
        description="Link a CoC account to Discord without token verification (admin only)",
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def force_link(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=player_tag_autocomplete),
        user: discord.Option(discord.Member, "Discord user"),
    ):
        await ctx.defer(ephemeral=True)

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        session = Session()
        player = session.query(Player).filter_by(tag=tag).first()
        if not player:
            result = await add_player_to_db(tag, session)
            if not result["success"]:
                await ctx.followup.send("❌ " + result["error"], ephemeral=True)
                session.close()
                return
            player = session.query(Player).filter_by(tag=result["tag"]).first()

        discord_user = session.query(DiscordUser).filter_by(discord_id=str(user.id)).first()
        if not discord_user:
            discord_user = DiscordUser(discord_id=str(user.id))
            session.add(discord_user)
            session.flush()

        if player.discord_user_id == discord_user.id:
            session.close()
            await ctx.followup.send(f"ℹ️ **{player.name}** is already linked to {user.mention}.", ephemeral=True)
            return

        player.discord_user_id = discord_user.id
        session.commit()
        session.close()
        await ctx.followup.send(f"✅ Linked **{player.name}** ({tag}) to {user.mention}.", ephemeral=True)

    @discord.slash_command(name="unlink", description="Unlink a Clash of Clans account from Discord")
    async def unlink(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=linked_tag_autocomplete),
        user: discord.Option(discord.Member, "Discord user"),
        api_token: discord.Option(str, "API token from in-game settings"),
    ):
        await ctx.defer(ephemeral=True)

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        valid = await verify_player_token(tag, api_token)
        if not valid:
            await ctx.followup.send("❌ Invalid API token.", ephemeral=True)
            return

        session = Session()
        player = session.query(Player).filter_by(tag=tag).first()

        if not player or not player.discord_user or player.discord_user.discord_id != str(user.id):
            session.close()
            await ctx.followup.send("❌ This account is not linked to the given user.", ephemeral=True)
            return

        player_name = player.name
        player.discord_user_id = None
        session.commit()
        session.close()
        await ctx.followup.send(f"✅ Unlinked **{player_name}** ({tag}) from {user.mention}.", ephemeral=True)

    @discord.slash_command(
        name="force_unlink",
        description="Unlink a CoC account from Discord without token verification (admin only)",
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def force_unlink(
        self,
        ctx: discord.ApplicationContext,
        tag: discord.Option(str, "Player tag, e.g. #ABC123", autocomplete=linked_tag_autocomplete),
    ):
        await ctx.defer(ephemeral=True)

        tag = tag.upper().replace("O", "0")
        if not tag.startswith("#"):
            tag = "#" + tag

        session = Session()
        player = session.query(Player).filter_by(tag=tag).first()

        if not player or player.discord_user_id is None:
            session.close()
            await ctx.followup.send("❌ This account is not linked to any user.", ephemeral=True)
            return

        player_name = player.name
        player.discord_user_id = None
        session.commit()
        session.close()
        await ctx.followup.send(f"✅ Unlinked **{player_name}** ({tag}).", ephemeral=True)

    @discord.slash_command(name="profile", description="Show CoC accounts linked to a Discord user")
    async def profile(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Option(discord.Member, "Discord user (defaults to you)", required=False, default=None),
    ):
        target = user or ctx.author
        session = Session()

        discord_user = session.query(DiscordUser).filter_by(discord_id=str(target.id)).first()
        if not discord_user or not discord_user.players:
            await ctx.respond(f"❌ {target.mention} has no linked CoC accounts.", ephemeral=True)
            session.close()
            return

        lines = [
            f"• [{p.name} ({p.tag})](https://link.clashofclans.com/en?action=OpenPlayerProfile&tag={p.tag.replace('#', '%23')})"
            for p in discord_user.players
        ]

        embed = discord.Embed(
            title=f"CoC Accounts — {target.display_name}",
            description="\n".join(lines),
            color=0x8B4513,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        session.close()
        await ctx.respond(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(LinkCog(bot))
