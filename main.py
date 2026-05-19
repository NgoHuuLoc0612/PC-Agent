"""
PC Agent - Discord Bot for Full PC Control
Author: PC Agent Team
Version: 2.0.0
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

from utils.logger import setup_logger
from utils.config import Config

logger = setup_logger("main")

COGS = [
    "cogs.system",
    "cogs.processes",
    "cogs.files",
    "cogs.network",
    "cogs.display",
    "cogs.audio",
    "cogs.automation",
    "cogs.monitoring",
    "cogs.power",
    "cogs.registry",
    "cogs.security",
    "cogs.visualizations",
    "cogs.scheduler",
    "cogs.clipboard",
    "cogs.remote",
    "cogs.help",
    # ── New upgraded cogs ──
    "cogs.permissions",
    "cogs.hardware",
    "cogs.remote_control",
    "cogs.macro",
    "cogs.network_plus",
    # ── Advanced monitoring & GPU cogs ──
    "cogs.gpu_detailed",      # Deep NVIDIA GPU via pynvml (nvidia-ml-py3)
    "cogs.bandwidth",         # Internet speed tests via speedtest-cli
    "cogs.perf_counters",     # Raw Windows PDH counters via native C++ binary
    "cogs.fps_counter",       # DXGI FPS counter via injected hook DLL
    "cogs.gpu_pipeline",      # GPU frame pipeline engine usage via PDH
]


class PCAgent(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=Config.PREFIX,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            description="Enterprise PC Control Agent",
        )
        self.config = Config
        self.startup_time = None

    async def setup_hook(self):
        """Load all cogs on startup."""
        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ Loaded cog: {cog}")
            except Exception as e:
                logger.error(f"❌ Failed to load cog {cog}: {e}")

        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f"🔄 Synced {len(synced)} slash commands")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        import datetime
        self.startup_time = datetime.datetime.utcnow()
        logger.info(f"🤖 PC Agent is online as {self.user}")
        logger.info(f"📡 Connected to {len(self.guilds)} guilds")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{Config.PREFIX}help | Controlling your PC",
            )
        )

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return  # Silently ignore unknown commands
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`")
            return
        logger.error(f"Unhandled error: {error}", exc_info=error)
        await ctx.send(f"❌ An error occurred: `{str(error)}`")

    async def on_message(self, message):
        if message.author.bot:
            return
        await self.process_commands(message)


async def main():
    bot = PCAgent()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN not set in environment!")
        sys.exit(1)
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
