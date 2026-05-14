"""
Remote access cog — live screenshot stream, system status ping, remote wake-on-LAN.
"""

import asyncio
import io
import time
from typing import Optional

import discord
import psutil
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, bytes_to_human, run_in_executor, seconds_to_human
from utils.logger import setup_logger

logger = setup_logger("cog.remote")


class Remote(commands.Cog):
    """Remote access and live monitoring features."""

    def __init__(self, bot):
        self.bot = bot
        self._stream_active = False
        self._stream_channel_id: Optional[int] = None
        self._stream_task: Optional[asyncio.Task] = None

    def _grab_screen(self) -> io.BytesIO:
        try:
            import mss, mss.tools
            with mss.mss() as sct:
                img = sct.grab(sct.monitors[0])
                buf = io.BytesIO(mss.tools.to_png(img.rgb, img.size))
                buf.seek(0)
                return buf
        except Exception:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf

    async def _stream_loop(self, interval: int):
        """Stream screenshots at regular intervals."""
        channel = self.bot.get_channel(self._stream_channel_id)
        if not channel:
            self._stream_active = False
            return

        frame = 0
        while self._stream_active:
            try:
                buf = await run_in_executor(self._grab_screen)
                frame += 1
                await channel.send(
                    content=f"🖥️ **Frame #{frame}** — `{time.strftime('%H:%M:%S')}`",
                    file=discord.File(buf, f"stream_{frame:04d}.png"),
                )
            except discord.HTTPException as e:
                logger.warning(f"Stream send error: {e}")
            await asyncio.sleep(interval)

        await channel.send(embed=build_embed("Stream", "⛔ Screen stream stopped.", color=Config.COLOR_WARNING))

    @commands.command(name="stream", aliases=["screenstream", "livescreen"])
    @admin_only()
    async def start_stream(self, ctx, interval: int = 5):
        """Stream live screenshots to this channel. interval = seconds between frames (min 3)."""
        if self._stream_active:
            await ctx.send(embed=build_embed("Stream", "⚠️ Stream already active. Use `!stopstream` first.", color=Config.COLOR_WARNING))
            return

        interval = max(3, min(60, interval))
        self._stream_active = True
        self._stream_channel_id = ctx.channel.id
        self._stream_task = asyncio.create_task(self._stream_loop(interval))

        await ctx.send(embed=build_embed(
            "🖥️ Screen Stream Started",
            f"Streaming every **{interval}s** to this channel.\n"
            f"Use `!stopstream` to stop.",
            color=Config.COLOR_SUCCESS,
        ))

    @commands.command(name="stopstream", aliases=["streamstop"])
    @admin_only()
    async def stop_stream(self, ctx):
        """Stop the live screenshot stream."""
        if not self._stream_active:
            await ctx.send(embed=build_embed("Stream", "No stream is active.", color=Config.COLOR_WARNING))
            return
        self._stream_active = False
        if self._stream_task:
            self._stream_task.cancel()
        await ctx.send(embed=build_embed("Stream", "⛔ Stream stopped.", color=Config.COLOR_SUCCESS))

    @commands.command(name="quickstatus", aliases=["qs", "status"])
    async def quick_status(self, ctx):
        """One-line PC status summary — fast response."""
        cpu = psutil.cpu_percent(interval=0.3)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime = seconds_to_human(time.time() - psutil.boot_time())
        battery = psutil.sensors_battery()
        bat_str = f"🔋{battery.percent:.0f}%" if battery else "🔌AC"

        status_line = (
            f"🖥️ `{psutil.Process().name()}` | "
            f"CPU `{cpu:.1f}%` | "
            f"RAM `{ram.percent:.1f}%` | "
            f"Disk `{disk.percent:.1f}%` | "
            f"Up `{uptime}` | "
            f"{bat_str}"
        )
        await ctx.send(status_line)

    @commands.command(name="ping2", aliases=["botping", "latency"])
    async def ping_bot(self, ctx):
        """Show bot latency and PC responsiveness."""
        start = time.perf_counter()
        msg = await ctx.send("🏓 Pinging...")
        rtt = (time.perf_counter() - start) * 1000
        ws = self.bot.latency * 1000

        fields = [
            ("WebSocket", f"{ws:.2f} ms", True),
            ("Message RTT", f"{rtt:.2f} ms", True),
        ]
        await msg.edit(
            content=None,
            embed=build_embed("🏓 Pong!", color=Config.COLOR_SUCCESS, fields=fields),
        )

    @commands.command(name="wol", aliases=["wakeonlan"])
    @admin_only()
    async def wake_on_lan(self, ctx, mac_address: str):
        """Send a Wake-on-LAN magic packet to a MAC address."""
        try:
            # Normalize MAC
            mac = mac_address.replace(":", "").replace("-", "").upper()
            if len(mac) != 12:
                await ctx.send(embed=build_embed("WoL", "Invalid MAC address.", color=Config.COLOR_ERROR))
                return

            magic = bytes.fromhex("F" * 12 + mac * 16)
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic, ("<broadcast>", 9))
            sock.close()

            await ctx.send(embed=build_embed(
                "Wake-on-LAN",
                f"✅ Magic packet sent to `{mac_address}`",
                color=Config.COLOR_SUCCESS,
            ))
        except Exception as e:
            await ctx.send(embed=build_embed("WoL", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="syshealth", aliases=["health", "healthcheck"])
    async def sys_health(self, ctx):
        """Comprehensive system health check."""
        async with ctx.typing():
            issues = []
            ok = []

            cpu = psutil.cpu_percent(interval=1)
            if cpu > 90:
                issues.append(f"🔴 CPU critical: {cpu:.1f}%")
            elif cpu > 70:
                issues.append(f"🟡 CPU high: {cpu:.1f}%")
            else:
                ok.append(f"🟢 CPU OK: {cpu:.1f}%")

            ram = psutil.virtual_memory()
            if ram.percent > 90:
                issues.append(f"🔴 RAM critical: {ram.percent:.1f}%")
            elif ram.percent > 75:
                issues.append(f"🟡 RAM high: {ram.percent:.1f}%")
            else:
                ok.append(f"🟢 RAM OK: {ram.percent:.1f}%")

            for part in psutil.disk_partitions(all=False):
                try:
                    du = psutil.disk_usage(part.mountpoint)
                    if du.percent > 95:
                        issues.append(f"🔴 Disk `{part.mountpoint}` critical: {du.percent:.1f}%")
                    elif du.percent > 80:
                        issues.append(f"🟡 Disk `{part.mountpoint}` high: {du.percent:.1f}%")
                    else:
                        ok.append(f"🟢 Disk `{part.mountpoint}` OK: {du.percent:.1f}%")
                except Exception:
                    continue

            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged and battery.percent < 15:
                issues.append(f"🔴 Battery low: {battery.percent:.1f}%")
            elif battery:
                ok.append(f"🟢 Battery: {battery.percent:.1f}%")

            all_items = issues + ok
            overall = "🔴 CRITICAL" if any("🔴" in i for i in issues) else \
                      "🟡 WARNING" if issues else "🟢 HEALTHY"

            color = Config.COLOR_ERROR if "CRITICAL" in overall else \
                    Config.COLOR_WARNING if "WARNING" in overall else Config.COLOR_SUCCESS

            await ctx.send(embed=build_embed(
                f"System Health: {overall}",
                "\n".join(all_items),
                color=color,
            ))

    @commands.command(name="remoteinfo", aliases=["remotehelp"])
    async def remote_info(self, ctx):
        """Show remote access tips and connection info."""
        import platform, socket
        hostname = socket.gethostname()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = "Unknown"

        fields = [
            ("Hostname", hostname, True),
            ("Local IP", ip, True),
            ("Platform", platform.system(), True),
            ("Screen Stream", "`!stream [interval]`", False),
            ("Live Screenshot", "`!screenshot`", False),
            ("Remote Command", "`!run <command>`", False),
            ("WoL", "`!wol <mac_address>`", False),
        ]
        await ctx.send(embed=build_embed("Remote Access Info", color=Config.COLOR_INFO, fields=fields))


async def setup(bot):
    await bot.add_cog(Remote(bot))
