"""
System information commands — CPU, RAM, disk, temperature, GPU, battery, uptime.
"""

import platform
import time
from datetime import datetime, timedelta

import discord
import psutil
from discord.ext import commands

from services.database import db
from services.viz_service import (
    cpu_history_chart, disk_usage_chart, ram_donut_chart, system_dashboard, temperature_chart
)
from utils.config import Config
from utils.helpers import admin_only, build_embed, bytes_to_human, run_in_executor, seconds_to_human, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.system")


class System(commands.Cog):
    """System information and hardware monitoring commands."""

    def __init__(self, bot):
        self.bot = bot
        self._cpu_history = []
        self._ram_history = []
        self._net_sent_history = []
        self._net_recv_history = []
        self._prev_net = psutil.net_io_counters()
        self._prev_net_time = time.time()

    def _record_metrics(self):
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        net = psutil.net_io_counters()
        now = time.time()
        elapsed = max(now - self._prev_net_time, 0.1)

        sent_kbs = (net.bytes_sent - self._prev_net.bytes_sent) / elapsed / 1024
        recv_kbs = (net.bytes_recv - self._prev_net.bytes_recv) / elapsed / 1024
        self._prev_net = net
        self._prev_net_time = now

        self._cpu_history.append(cpu)
        self._ram_history.append(ram.percent)
        self._net_sent_history.append(max(0, sent_kbs))
        self._net_recv_history.append(max(0, recv_kbs))

        # Keep last 120 samples
        for h in (self._cpu_history, self._ram_history,
                  self._net_sent_history, self._net_recv_history):
            if len(h) > 120:
                h.pop(0)

    # ─── Commands ────────────────────────────────────────────────────────────

    @commands.command(name="sysinfo", aliases=["sys", "info"])
    async def sysinfo(self, ctx):
        """Full system overview."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "sysinfo")
        async with ctx.typing():
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()
            boot_time = psutil.boot_time()
            uptime = seconds_to_human(time.time() - boot_time)

            fields = [
                ("🖥️ OS", f"{platform.system()} {platform.release()} ({platform.machine()})", False),
                ("🔧 CPU", f"{psutil.cpu_count(logical=False)}C/{psutil.cpu_count()}T  |  {cpu:.1f}% used", True),
                ("🧠 RAM", f"{bytes_to_human(ram.used)} / {bytes_to_human(ram.total)} ({ram.percent:.1f}%)", True),
                ("💾 Disk", f"{bytes_to_human(disk.used)} / {bytes_to_human(disk.total)} ({disk.percent:.1f}%)", True),
                ("📡 Network", f"↑ {bytes_to_human(net.bytes_sent)}  ↓ {bytes_to_human(net.bytes_recv)}", True),
                ("⏱️ Uptime", uptime, True),
                ("🐍 Python", platform.python_version(), True),
                ("🏠 Hostname", platform.node(), True),
            ]

            embed = build_embed(
                "System Information",
                f"**{platform.node()}**",
                color=Config.COLOR_SYSTEM,
                fields=fields,
            )
            await ctx.send(embed=embed)

    @commands.command(name="cpu")
    async def cpu_info(self, ctx):
        """Detailed CPU information and per-core usage."""
        async with ctx.typing():
            self._record_metrics()
            overall = psutil.cpu_percent(interval=1)
            per_core = psutil.cpu_percent(interval=0.5, percpu=True)
            freq = psutil.cpu_freq()
            load = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0, 0, 0)

            core_str = "\n".join(
                [f"Core {i}: {'█' * int(p/5)}{'░' * (20 - int(p/5))} {p:.1f}%"
                 for i, p in enumerate(per_core)]
            )

            fields = [
                ("📊 Overall", f"{overall:.1f}%", True),
                ("⚡ Frequency", f"{freq.current:.0f} MHz (max {freq.max:.0f} MHz)" if freq else "N/A", True),
                ("🔢 Cores", f"{psutil.cpu_count(logical=False)} physical / {psutil.cpu_count()} logical", True),
                ("📈 Load Avg", f"1m: {load[0]:.2f}  5m: {load[1]:.2f}  15m: {load[2]:.2f}", True),
                ("📉 Per-Core", f"```\n{truncate(core_str, 1000)}\n```", False),
            ]

            buf = await run_in_executor(cpu_history_chart, self._cpu_history or [overall])
            embed = build_embed("CPU Information", color=Config.COLOR_SYSTEM, fields=fields)
            await ctx.send(embed=embed, file=discord.File(buf, "cpu.png"))

    @commands.command(name="ram", aliases=["memory", "mem"])
    async def ram_info(self, ctx):
        """RAM & virtual memory details."""
        async with ctx.typing():
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()

            fields = [
                ("Total", bytes_to_human(vm.total), True),
                ("Used", f"{bytes_to_human(vm.used)} ({vm.percent:.1f}%)", True),
                ("Available", bytes_to_human(vm.available), True),
                ("Swap Total", bytes_to_human(sw.total), True),
                ("Swap Used", f"{bytes_to_human(sw.used)} ({sw.percent:.1f}%)", True),
                ("Swap Free", bytes_to_human(sw.free), True),
            ]

            buf = await run_in_executor(ram_donut_chart,
                                        vm.used / 1024**3, vm.total / 1024**3)
            embed = build_embed("RAM & Memory", color=Config.COLOR_SYSTEM, fields=fields)
            await ctx.send(embed=embed, file=discord.File(buf, "ram.png"))

    @commands.command(name="disk", aliases=["storage", "drives"])
    async def disk_info(self, ctx):
        """Disk partitions and usage."""
        async with ctx.typing():
            disk_data = []
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disk_data.append({
                        "mount": part.mountpoint,
                        "fstype": part.fstype,
                        "used": usage.used / 1024**3,
                        "total": usage.total / 1024**3,
                        "free": usage.free / 1024**3,
                    })
                except PermissionError:
                    continue

            fields = [
                (d["mount"],
                 f"{d['fstype']} | {d['used']:.1f} / {d['total']:.1f} GB ({d['used']/d['total']*100:.1f}%)",
                 False)
                for d in disk_data
            ]

            buf = await run_in_executor(disk_usage_chart, disk_data)
            embed = build_embed("Disk Usage", color=Config.COLOR_SYSTEM, fields=fields)
            await ctx.send(embed=embed, file=discord.File(buf, "disk.png"))

    @commands.command(name="temp", aliases=["temperature", "temps"])
    async def temperature(self, ctx):
        """CPU/GPU temperatures."""
        async with ctx.typing():
            sensors = {}
            try:
                temps = psutil.sensors_temperatures()
                for name, entries in temps.items():
                    for entry in entries:
                        key = f"{name}/{entry.label or 'core'}"
                        sensors[key] = entry.current
            except AttributeError:
                sensors = {"N/A (Windows/macOS)": 0.0}

            if not sensors or all(v == 0.0 for v in sensors.values()):
                await ctx.send(embed=build_embed(
                    "Temperature",
                    "⚠️ Temperature sensors not available on this platform.",
                    color=Config.COLOR_WARNING,
                ))
                return

            buf = await run_in_executor(temperature_chart, sensors)
            embed = build_embed("Temperature Sensors", color=Config.COLOR_SYSTEM)
            await ctx.send(embed=embed, file=discord.File(buf, "temps.png"))

    @commands.command(name="battery", aliases=["bat"])
    async def battery(self, ctx):
        """Battery status."""
        battery = psutil.sensors_battery()
        if not battery:
            await ctx.send(embed=build_embed("Battery", "No battery detected.", color=Config.COLOR_WARNING))
            return

        status = "🔌 Charging" if battery.power_plugged else "🔋 Discharging"
        time_left = seconds_to_human(battery.secsleft) if battery.secsleft > 0 else "Calculating..."
        pct = battery.percent
        bar_len = 20
        filled = int(pct / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        fields = [
            ("Status", status, True),
            ("Level", f"{pct:.1f}%", True),
            ("Time Remaining", time_left, True),
            ("Visual", f"`[{bar}] {pct:.1f}%`", False),
        ]
        color = Config.COLOR_SUCCESS if pct > 50 else Config.COLOR_WARNING if pct > 20 else Config.COLOR_ERROR
        await ctx.send(embed=build_embed("Battery Status", color=color, fields=fields))

    @commands.command(name="uptime")
    async def uptime(self, ctx):
        """System and bot uptime."""
        boot = psutil.boot_time()
        sys_uptime = seconds_to_human(time.time() - boot)
        bot_uptime = seconds_to_human(
            (datetime.utcnow() - self.bot.startup_time).total_seconds()
        ) if self.bot.startup_time else "N/A"

        fields = [
            ("🖥️ System Uptime", sys_uptime, True),
            ("🤖 Bot Uptime", bot_uptime, True),
            ("🕐 Boot Time", datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M:%S"), False),
        ]
        await ctx.send(embed=build_embed("Uptime", color=Config.COLOR_INFO, fields=fields))

    @commands.command(name="gpu")
    async def gpu_info(self, ctx):
        """GPU information (requires GPUtil or pynvml)."""
        async with ctx.typing():
            try:
                import GPUtil
                gpus = GPUtil.getGPUs()
                if not gpus:
                    raise ValueError("No GPUs detected")
                fields = []
                for gpu in gpus:
                    fields += [
                        (f"GPU {gpu.id}: {gpu.name}", "", False),
                        ("VRAM", f"{gpu.memoryUsed:.0f} / {gpu.memoryTotal:.0f} MB ({gpu.memoryUtil*100:.1f}%)", True),
                        ("GPU Load", f"{gpu.load*100:.1f}%", True),
                        ("Temperature", f"{gpu.temperature:.1f}°C", True),
                    ]
                await ctx.send(embed=build_embed("GPU Information", color=Config.COLOR_SYSTEM, fields=fields))
            except ImportError:
                await ctx.send(embed=build_embed(
                    "GPU", "Install `GPUtil` for GPU stats: `pip install gputil`",
                    color=Config.COLOR_WARNING,
                ))
            except Exception as e:
                await ctx.send(embed=build_embed("GPU", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="dashboard", aliases=["dash"])
    async def dashboard(self, ctx):
        """Full system dashboard visualization."""
        async with ctx.typing():
            self._record_metrics()
            ram = psutil.virtual_memory()
            disk_data = []
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disk_data.append({
                        "mount": part.mountpoint,
                        "used": usage.used / 1024**3,
                        "total": usage.total / 1024**3,
                    })
                except PermissionError:
                    continue

            buf = await run_in_executor(
                system_dashboard,
                self._cpu_history or [psutil.cpu_percent()],
                ram.used / 1024**3,
                ram.total / 1024**3,
                disk_data,
                self._net_sent_history or [0],
                self._net_recv_history or [0],
            )
            await ctx.send(
                embed=build_embed("System Dashboard", color=Config.COLOR_SYSTEM),
                file=discord.File(buf, "dashboard.png"),
            )


async def setup(bot):
    await bot.add_cog(System(bot))
