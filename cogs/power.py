"""
Power management — shutdown, restart, hibernate, sleep, logoff, cancel timers.
"""

import asyncio
import platform
import subprocess
from typing import Optional

from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed
from utils.logger import setup_logger

logger = setup_logger("cog.power")


class Power(commands.Cog):
    """PC power and session management."""

    def __init__(self, bot):
        self.bot = bot
        self._pending_timer: Optional[asyncio.Task] = None

    def _exec(self, *cmd):
        subprocess.Popen(cmd)

    @commands.command(name="shutdown", aliases=["poweroff"])
    @admin_only()
    async def shutdown(self, ctx, delay: int = 0):
        """Shut down the PC. Optional delay in seconds."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None,
                       "shutdown", str(delay))
        msg = f"🔴 Shutting down in **{delay}s**..." if delay > 0 else "🔴 Shutting down NOW!"

        confirm = build_embed("⚠️ SHUTDOWN", msg, color=Config.COLOR_ERROR)
        discord_msg = await ctx.send(embed=confirm)
        await discord_msg.add_reaction("✅")
        await discord_msg.add_reaction("❌")

        def check(r, u):
            return u == ctx.author and str(r.emoji) in ["✅", "❌"] and r.message.id == discord_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30, check=check)
        except asyncio.TimeoutError:
            await ctx.send(embed=build_embed("Shutdown", "Timed out — cancelled.", color=Config.COLOR_INFO))
            return

        if str(reaction.emoji) == "❌":
            await ctx.send(embed=build_embed("Shutdown", "Cancelled.", color=Config.COLOR_INFO))
            return

        if platform.system() == "Windows":
            self._exec("shutdown", "/s", "/t", str(delay))
        else:
            if delay > 0:
                self._exec("sudo", "shutdown", "-h", f"+{delay//60}")
            else:
                self._exec("sudo", "shutdown", "-h", "now")

        await ctx.send(embed=build_embed("Shutdown", msg, color=Config.COLOR_ERROR))

    @commands.command(name="restart", aliases=["reboot"])
    @admin_only()
    async def restart(self, ctx, delay: int = 0):
        """Restart the PC."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "restart")
        confirm_msg = await ctx.send(embed=build_embed(
            "⚠️ RESTART",
            f"Restarting in **{delay}s**. React ✅ to confirm.",
            color=Config.COLOR_WARNING,
        ))
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(r, u):
            return u == ctx.author and str(r.emoji) in ["✅", "❌"] and r.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30, check=check)
        except asyncio.TimeoutError:
            await ctx.send(embed=build_embed("Restart", "Cancelled (timeout).", color=Config.COLOR_INFO))
            return

        if str(reaction.emoji) == "❌":
            await ctx.send(embed=build_embed("Restart", "Cancelled.", color=Config.COLOR_INFO))
            return

        if platform.system() == "Windows":
            self._exec("shutdown", "/r", "/t", str(delay))
        else:
            self._exec("sudo", "reboot")

        await ctx.send(embed=build_embed("Restart", f"🔄 Restarting in {delay}s...", color=Config.COLOR_WARNING))

    @commands.command(name="sleep", aliases=["sleepnow"])
    @admin_only()
    async def sleep_cmd(self, ctx):
        """Put the PC to sleep/suspend."""
        try:
            if platform.system() == "Windows":
                import ctypes
                ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)
            elif platform.system() == "Linux":
                subprocess.Popen(["systemctl", "suspend"])
            elif platform.system() == "Darwin":
                subprocess.Popen(["pmset", "sleepnow"])
            await ctx.send(embed=build_embed("Sleep", "💤 Going to sleep...", color=Config.COLOR_INFO))
        except Exception as e:
            await ctx.send(embed=build_embed("Sleep", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="hibernate")
    @admin_only()
    async def hibernate(self, ctx):
        """Hibernate the PC."""
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["shutdown", "/h"])
            elif platform.system() == "Linux":
                subprocess.Popen(["systemctl", "hibernate"])
            await ctx.send(embed=build_embed("Hibernate", "💾 Hibernating...", color=Config.COLOR_INFO))
        except Exception as e:
            await ctx.send(embed=build_embed("Hibernate", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="logoff", aliases=["logout", "signout"])
    @admin_only()
    async def logoff(self, ctx):
        """Log off the current user."""
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["shutdown", "/l"])
            elif platform.system() == "Linux":
                subprocess.Popen(["pkill", "-KILL", "-u", subprocess.getoutput("whoami")])
            elif platform.system() == "Darwin":
                subprocess.Popen(["osascript", "-e", 'tell app "System Events" to log out'])
            await ctx.send(embed=build_embed("Log Off", "👋 Logging off...", color=Config.COLOR_INFO))
        except Exception as e:
            await ctx.send(embed=build_embed("Log Off", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="canceltimer", aliases=["abortshutdown"])
    @admin_only()
    async def cancel_timer(self, ctx):
        """Cancel a pending shutdown/restart timer."""
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["shutdown", "/a"])
            else:
                subprocess.Popen(["sudo", "shutdown", "-c"])
            if self._pending_timer and not self._pending_timer.done():
                self._pending_timer.cancel()
            await ctx.send(embed=build_embed("Cancel Timer", "✅ Shutdown/restart timer cancelled.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Cancel Timer", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="powerstatus", aliases=["powerinfo"])
    async def power_status(self, ctx):
        """Show power plan and battery status."""
        import psutil
        from utils.helpers import bytes_to_human, seconds_to_human

        battery = psutil.sensors_battery()
        fields = []

        if battery:
            status = "🔌 Charging" if battery.power_plugged else "🔋 Discharging"
            time_left = seconds_to_human(battery.secsleft) if battery.secsleft > 0 else "N/A"
            pct = battery.percent
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            fields += [
                ("Battery", f"{status} — {pct:.1f}%", True),
                ("Time Left", time_left, True),
                ("Visual", f"`[{bar}]`", False),
            ]
        else:
            fields.append(("Battery", "No battery (Desktop/AC)", True))

        # Power plan (Windows)
        if platform.system() == "Windows":
            try:
                output = subprocess.check_output(
                    ["powercfg", "/getactivescheme"], text=True, timeout=5
                )
                plan = output.strip().split(":")[-1].strip() if ":" in output else output.strip()
                fields.append(("Power Plan", plan[:100], False))
            except Exception:
                pass

        await ctx.send(embed=build_embed("Power Status", color=Config.COLOR_INFO, fields=fields))

    @commands.command(name="timedshutdown", aliases=["scheduleshutdown"])
    @admin_only()
    async def timed_shutdown(self, ctx, minutes: int):
        """Schedule a shutdown in N minutes."""
        secs = minutes * 60
        if platform.system() == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", str(secs)])
        else:
            subprocess.Popen(["sudo", "shutdown", "-h", f"+{minutes}"])
        await ctx.send(embed=build_embed(
            "Timed Shutdown",
            f"⏱️ PC will shut down in **{minutes} minutes**.\nUse `!canceltimer` to abort.",
            color=Config.COLOR_WARNING,
        ))


async def setup(bot):
    await bot.add_cog(Power(bot))
