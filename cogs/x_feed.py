import asyncio
import os
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands, tasks

from db import Session
from models import XSubscription, XGuildConfig

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

    async def _fetch_user(self, username: str) -> tuple[str, str, str] | None:
        """Returns (user_id, display_name, avatar_url) — charged once as User: Read."""
        url = f"{X_API_BASE}/users/by/username/{username}"
        params = {"user.fields": "name,profile_image_url"}
        async with self._get_http().get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = (await resp.json()).get("data", {})
            uid = data.get("id")
            if not uid:
                return None
            return uid, data.get("name", username), data.get("profile_image_url")

    async def _get_tweets(self, user_id: str, since_id: str | None = None) -> dict | None:
        params = {
            "max_results": 10,
            "tweet.fields": "created_at,text,attachments",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url,type",
            "exclude": "replies,retweets",
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
        print("[x_feed] Poll started", flush=True)
        session = Session()
        try:
            sub_data = [(s.id, s.username) for s in session.query(XSubscription).all()]
        except Exception as e:
            print(f"[x_feed] DB error loading subs: {e}", flush=True)
            return
        finally:
            session.close()

        print(f"[x_feed] {len(sub_data)} subscription(s) to poll", flush=True)
        for sub_id, username in sub_data:
            try:
                await self._poll_one(sub_id)
            except Exception as e:
                print(f"[x_feed] Error polling @{username}: {e}", flush=True)
            await asyncio.sleep(2)

    async def _poll_one(self, sub_id: int):
        session = Session()
        try:
            sub = session.query(XSubscription).filter_by(id=sub_id).first()
            if not sub:
                return

            # Resolve & cache user profile if missing (User: Read — paid once)
            if not sub.user_id or not sub.display_name:
                result = await self._fetch_user(sub.username)
                if not result:
                    print(f"[x_feed] Cannot resolve @{sub.username}")
                    return
                sub.user_id, sub.display_name, sub.avatar_url = result
                session.commit()

            data = await self._get_tweets(sub.user_id, sub.last_tweet_id)
            tweets = (data or {}).get("data")
            if not tweets:
                return

            channel = self.bot.get_channel(int(sub.channel_id))
            if not channel:
                print(f"[x_feed] Channel {sub.channel_id} not found for @{sub.username}")
                return

            # First poll — post only the latest tweet as a preview
            is_first = not sub.last_tweet_id
            tweets_to_post = [tweets[0]] if is_first else list(reversed(tweets))

            guild_cfg = session.query(XGuildConfig).filter_by(guild_id=sub.guild_id).first()
            mention = f"<@&{guild_cfg.mention_role_id}> " if guild_cfg and guild_cfg.mention_role_id else ""

            media_map = {
                m["media_key"]: m
                for m in (data or {}).get("includes", {}).get("media", [])
            }

            for tweet in tweets_to_post:
                tweet_url = f"https://x.com/{sub.username}/status/{tweet['id']}"
                ts = None
                if tweet.get("created_at"):
                    ts = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))

                media_keys = tweet.get("attachments", {}).get("media_keys", [])
                image_urls = []
                for key in media_keys:
                    m = media_map.get(key, {})
                    url = m.get("url") or m.get("preview_image_url")
                    if url:
                        image_urls.append(url)

                # Build embeds — multiple images need multiple embeds with same URL
                main_embed = discord.Embed(
                    description=tweet["text"],
                    color=0x000000,
                    url=tweet_url,
                )
                main_embed.set_author(
                    name=f"{sub.display_name} (@{sub.username})",
                    icon_url=sub.avatar_url,
                    url=f"https://x.com/{sub.username}",
                )
                if ts:
                    main_embed.timestamp = ts
                main_embed.set_footer(text="𝕏")

                content = f"{mention}{tweet_url}"
                if image_urls:
                    main_embed.set_image(url=image_urls[0])
                    extra_embeds = []
                    for img_url in image_urls[1:]:
                        e = discord.Embed(url=tweet_url, color=0x000000)
                        e.set_image(url=img_url)
                        extra_embeds.append(e)
                    await channel.send(content=content, embeds=[main_embed] + extra_embeds)
                else:
                    await channel.send(content=content, embed=main_embed)

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

        result = await self._fetch_user(username_clean)
        if not result:
            await ctx.followup.send(f"❌ Nie znaleziono użytkownika `@{username_clean}` na X.")
            return
        uid, display_name, avatar_url = result

        session = Session()
        try:
            existing = session.query(XSubscription).filter_by(
                guild_id=str(ctx.guild_id), username=username_clean
            ).first()
            if existing:
                existing.channel_id = str(channel.id)
                existing.user_id = uid
                existing.display_name = display_name
                existing.avatar_url = avatar_url
                session.commit()
                await ctx.followup.send(f"✅ Zaktualizowano — `@{username_clean}` → {channel.mention}")
            else:
                sub = XSubscription(
                    guild_id=str(ctx.guild_id),
                    channel_id=str(channel.id),
                    username=username_clean,
                    user_id=uid,
                    display_name=display_name,
                    avatar_url=avatar_url,
                )
                session.add(sub)
                session.commit()
                await ctx.followup.send(
                    f"✅ Subskrybujesz `@{username_clean}` → {channel.mention}\n"
                    f"Pierwsze nowe posty pojawią się przy następnym sprawdzaniu (do 15 min)."
                )
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

    @x_group.command(name="set_role", description="Ustaw rolę tagowaną przy każdym nowym poście X")
    @discord.default_permissions(manage_guild=True)
    async def x_set_role(
        self,
        ctx: discord.ApplicationContext,
        role: discord.Option(discord.Role, "Rola do tagowania (zostaw puste aby usunąć)", required=False),
    ):
        await ctx.defer(ephemeral=True)
        if not self._is_allowed(ctx.guild_id):
            await ctx.followup.send("❌ Ta funkcja nie jest dostępna na tym serwerze.")
            return
        session = Session()
        try:
            cfg = session.query(XGuildConfig).filter_by(guild_id=str(ctx.guild_id)).first()
            if not cfg:
                cfg = XGuildConfig(guild_id=str(ctx.guild_id))
                session.add(cfg)
            cfg.mention_role_id = str(role.id) if role else None
            session.commit()
            if role:
                await ctx.followup.send(f"✅ Przy każdym nowym poście X będzie tagowana rola {role.mention}.")
            else:
                await ctx.followup.send("✅ Usunięto tagowanie roli.")
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
