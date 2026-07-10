import os
import platform
from datetime import datetime, timedelta

import discord
import psutil
from discord.ext import commands

_process = psutil.Process(os.getpid())


def _format_uptime(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


class StatusCog(discord.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="status",
        description="Show bot resource usage (CPU, RAM, uptime)",
        default_member_permissions=discord.Permissions(administrator=True),
    )
    async def status(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        proc_cpu = _process.cpu_percent(interval=0.5)
        proc_mem = _process.memory_info().rss / (1024 ** 2)
        proc_uptime = _format_uptime(datetime.now() - datetime.fromtimestamp(_process.create_time()))

        sys_cpu = psutil.cpu_percent(interval=0.5)
        sys_mem = psutil.virtual_memory()
        sys_uptime = _format_uptime(datetime.now() - datetime.fromtimestamp(psutil.boot_time()))

        bot_uptime = _format_uptime(datetime.now() - self.bot.launch_time) if hasattr(self.bot, "launch_time") else "—"

        embed = discord.Embed(title="🖥️ Bot Status", color=0x8B4513)
        embed.add_field(
            name="Process",
            value=(
                f"CPU: **{proc_cpu:.1f}%**\n"
                f"RAM: **{proc_mem:.1f} MB**\n"
                f"Uptime: **{proc_uptime}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="System",
            value=(
                f"CPU: **{sys_cpu:.1f}%**\n"
                f"RAM: **{sys_mem.percent:.1f}%** ({sys_mem.used / (1024 ** 3):.1f} / {sys_mem.total / (1024 ** 3):.1f} GB)\n"
                f"Uptime: **{sys_uptime}**"
            ),
            inline=True,
        )
        embed.add_field(name="Latency", value=f"**{self.bot.latency * 1000:.0f} ms**", inline=True)
        embed.set_footer(text=f"{platform.system()} {platform.release()} · PID {os.getpid()}")

        await ctx.followup.send(embed=embed)


def setup(bot: discord.Bot):
    bot.add_cog(StatusCog(bot))
