"""
Shared helpers, decorators, and utilities for PC Agent.
"""

import asyncio
import functools
import platform
import time
from typing import Callable, Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.logger import setup_logger

logger = setup_logger("helpers")


def admin_only():
    """Check if user is in OWNER_IDS or has admin role."""
    async def predicate(ctx):
        if ctx.author.id in Config.OWNER_IDS:
            return True
        if Config.REQUIRE_ADMIN_ROLE:
            role = discord.utils.get(ctx.author.roles, name=Config.ADMIN_ROLE_NAME)
            if role:
                return True
        if ctx.author.guild_permissions.administrator:
            return True
        raise commands.CheckFailure("You need administrator privileges for this command.")
    return commands.check(predicate)


def windows_only():
    """Restrict command to Windows only."""
    async def predicate(ctx):
        if platform.system() != "Windows":
            await ctx.send("❌ This command is only available on Windows.")
            return False
        return True
    return commands.check(predicate)


def timed_command(func):
    """Decorator that logs execution time of a command."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        elapsed = (time.perf_counter() - start) * 1000
        logger.debug(f"{func.__name__} took {elapsed:.2f}ms")
        return result
    return wrapper


def build_embed(
    title: str,
    description: str = "",
    color: int = Config.COLOR_INFO,
    fields: list = None,
    footer: str = "PC Agent",
    thumbnail: str = None,
    image_url: str = None,
) -> discord.Embed:
    """Build a richly formatted Discord embed."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=footer)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=str(value)[:1024], inline=inline)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image_url:
        embed.set_image(url=image_url)
    return embed


def bytes_to_human(n: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} EB"


def seconds_to_human(secs: float) -> str:
    """Convert seconds to d/h/m/s format."""
    secs = int(secs)
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def truncate(text: str, max_len: int = 1024) -> str:
    """Truncate string to fit in embed field."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def run_in_executor(func, *args):
    """Run a blocking function in a thread pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


def platform_info() -> dict:
    """Return structured platform info."""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "node": platform.node(),
        "release": platform.release(),
    }
