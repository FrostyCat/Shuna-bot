import asyncio
import os
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands, tasks

from db import Session
from models import XSubscription

X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
X_API_BASE = "https://api.twitter.com/2"
X_ALLOWED_GUILDS = set(filter(None, os.environ.get("X_ALLOWED_GUILDS", "").split(",")))


class XFeedCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._http: aiohttp.ClientSession | None = None
        self.poll_x_feeds.start()

    def cog_unload(self):
        self.poll_x_feeds.cancel()
        if self._http and not self._http.closed:
            asyncio.create_task(self._http.close())

    def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"}
            )
        return self._http

    async def _get_user_id(self, username: str) -> str | None:
        url = f"{X_API_BASE}/users/by/username/{username}"
        async with self._get_http().get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("data", {}).get("id")

    async def _get_tweets(self, user_id: str, since_id: str | None = None) -> dict | None:
        params = {
            "max_results": 10,
            "tweet.fields": "created_at,text",
            "expansions": "author_id",
            "user.fields": "name,username,profile_image_url",
        }
        if since_id:
            params["since_id"] = since_id
        url = f"{X_API_BASE}/users/{user_id}/tweets"
        async with self._get_http().get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"[x_feed] API error {resp.status}: {text[:200]}")
                return None
            return await resp.json()

    @tasks.loop(minutes=15)
    async def poll_x_feeds(self):
        session = Session()
        try:
            subs = session.query(XSubscription).all()
            sub_data = [(s.id, s.username) for s in subs]
        except Exception as e:
            print(f"[x_feed] DB error loading subs: {e}")
            return
        finally:
            session.close()

        for sub_id, username in sub_data:
            try:
                await self._poll_one(sub_id)
            except Exception as e:
                print(f"[x_feed] Error polling @{username}: {e}")
            await asyncio.sleep(2)

    async def _poll_one(self, sub_id: int):
        session = Session()
        try:
            sub = session.query(XSubscription).filter_by(id=sub_id).first()
            if not sub:
                return

            if not sub.user_id:
                uid = await self._get_user_id(sub.username)
                if not uid:
                    print(f"[x_feed] Cannot resolve @{sub.username}")
                    return
                sub.user_id = uid
                session.commit()

            data = await self._get_tweets(sub.user_id, sub.last_tweet_id)
            tweets = (data or {}).get("data")
            if not tweets:
                return

            # First poll — just save latest ID, don't flood channel
            if not sub.last_tweet_id:
                sub.last_tweet_id = tweets[0]["id"]
                session.commit()
                print(f"[x_feed] @{sub.username} initialized, latest tweet: {tweets[0]['id']}")
                return

            users = {u["id"]: u for u in (data or {}).get("includes", {}).get("users", [])}
            author = users.get(sub.user_id, {})
            display_name = author.get("name", sub.username)
            avatar_url = author.get("profile_image_url")

            channel = self.bot.get_channel(int(sub.channel_id))
            if not channel:
                print(f"[x_feed] Channel {sub.channel_id} not found for @{sub.username}")
                return

            for tweet in reversed(tweets):
                tweet_url = f"https://x.com/{sub.username}/status/{tweet['id']}"
                embed = discord.Embed(
                    description=tweet["text"],
                    color=0x000000,
                    url=tweet_url,
                )
                embed.set_author(
                    name=f"{display_name} (@{sub.username})",
                    icon_url=avatar_url,
                    url=f"https://x.com/{sub.username}",
                )
                if tweet.get("created_at"):
                    embed.timestamp = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
                embed.set_footer(text="𝕏")
                await channel.send(embed=embed)
                await asyncio.sleep(0.5)

            sub.last_tweet_id = tweets[0]["id"]
            session.commit()
            print(f"[x_feed] Posted {len(tweets)} tweet(s) from @{sub.username}")
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    @poll_x_feeds.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    def _is_allowed(self, guild_id: int) -> bool:
        if not X_ALLOWED_GUILDS:
            return True
        return str(guild_id) in X_ALLOWED_GUILDS

    x_group = discord.SlashCommandGroup("x", "X (Twitter) feed subscriptions")

    @x_group.command(name="follow", description="Subskrybuj profil X na kanale")
    @discord.default_permissions(manage_guild=True)
    async def x_follow(
        self,
        ctx: discord.ApplicationContext,
        username: discord.Option(str, "Nazwa użytkownika X (bez @)"),
        channel: discord.Option(discord.TextChannel, "Kanał do postowania"),
    ):
        await ctx.defer(ephemeral=True)
        if not self._is_allowed(ctx.guild_id):
            await ctx.followup.send("❌ Ta funkcja nie jest dostępna na tym serwerze.")
            return
        username_clean = username.lstrip("@").lower()

        uid = await self._get_user_id(username_clean)
        if not uid:
            await ctx.followup.send(f"❌ Nie znaleziono użytkownika `@{username_clean}` na X.")
            return

        session = Session()
        try:
            existing = session.query(XSubscription).filter_by(
                guild_id=str(ctx.guild_id), username=username_clean
            ).first()
            if existing:
                existing.channel_id = str(channel.id)
                existing.user_id = uid
                session.commit()
                await ctx.followup.send(f"✅ Zaktualizowano — `@{username_clean}` → {channel.mention}")
            else:
                sub = XSubscription(
                    guild_id=str(ctx.guild_id),
                    channel_id=str(channel.id),
                    username=username_clean,
                    user_id=uid,
                )
                session.add(sub)
                session.commit()
                await ctx.followup.send(f"✅ Subskrybujesz `@{username_clean}` → {channel.mention}\nPierwszy post pojawi się przy następnym sprawdzaniu (do 15 min).")
        except Exception as e:
            session.rollback()
            await ctx.followup.send(f"❌ Błąd: {e}")
        finally:
            session.close()

    @x_group.command(name="unfollow", description="Odsubskrybuj profil X")
    @discord.default_permissions(manage_guild=True)
    async def x_unfollow(
        self,
        ctx: discord.ApplicationContext,
        username: discord.Option(str, "Nazwa użytkownika X (bez @)"),
    ):
        await ctx.defer(ephemeral=True)
        if not self._is_allowed(ctx.guild_id):
            await ctx.followup.send("❌ Ta funkcja nie jest dostępna na tym serwerze.")
            return
        username_clean = username.lstrip("@").lower()
        session = Session()
        try:
            sub = session.query(XSubscription).filter_by(
                guild_id=str(ctx.guild_id), username=username_clean
            ).first()
            if not sub:
                await ctx.followup.send(f"❌ Nie subskrybujesz `@{username_clean}`.")
                return
            session.delete(sub)
            session.commit()
            await ctx.followup.send(f"✅ Odsubskrybowano `@{username_clean}`.")
        except Exception as e:
            session.rollback()
            await ctx.followup.send(f"❌ Błąd: {e}")
        finally:
            session.close()

    @x_group.command(name="feeds", description="Lista aktywnych subskrypcji X")
    async def x_feeds(
        self,
        ctx: discord.ApplicationContext,
    ):
        await ctx.defer(ephemeral=True)
        if not self._is_allowed(ctx.guild_id):
            await ctx.followup.send("❌ Ta funkcja nie jest dostępna na tym serwerze.")
            return
        session = Session()
        try:
            subs = session.query(XSubscription).filter_by(guild_id=str(ctx.guild_id)).all()
            if not subs:
                await ctx.followup.send("Brak aktywnych subskrypcji X.")
                return
            lines = []
            for s in subs:
                ch = self.bot.get_channel(int(s.channel_id))
                ch_str = ch.mention if ch else f"<#{s.channel_id}>"
                lines.append(f"• `@{s.username}` → {ch_str}")
            embed = discord.Embed(
                title="Subskrypcje X",
                description="\n".join(lines),
                color=0x000000,
            )
            await ctx.followup.send(embed=embed)
        finally:
            session.close()


def setup(bot: discord.Bot):
    bot.add_cog(XFeedCog(bot))
