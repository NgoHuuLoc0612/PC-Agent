"""
Real-time system monitoring — polling loop, threshold alerts, history export.
"""

import asyncio
import csv
import io
import json
import time
from typing import Optional

import discord
import psutil
from discord.ext import commands, tasks

from services.database import db
from services.viz_service import cpu_history_chart, network_chart, system_dashboard
from utils.config import Config
from utils.helpers import build_embed, bytes_to_human, run_in_executor, seconds_to_human
from utils.logger import setup_logger

logger = setup_logger("cog.monitoring")

MAX_HISTORY = 300  # 5 minutes at 1-second resolution


class Monitoring(commands.Cog):
    """Continuous monitoring, alerting, and history."""

    def __init__(self, bot):
        self.bot = bot
        self.alert_channel_id: Optional[int] = None
        self.monitoring_active = False

        # Metric histories
        self.cpu_hist: list = []
        self.ram_hist: list = []
        self.net_sent_hist: list = []
        self.net_recv_hist: list = []
        self.timestamps: list = []

        self._prev_net = psutil.net_io_counters()
        self._prev_net_time = time.time()

        # Alert state (prevent spam)
        self._alerted: dict = {"cpu": False, "ram": False, "disk": False}

        self.polling_loop.start()

    def cog_unload(self):
        self.polling_loop.cancel()

    @tasks.loop(seconds=Config.MONITOR_INTERVAL)
    async def polling_loop(self):
        if not self.monitoring_active:
            return
        await self._collect_metrics()
        await self._check_thresholds()

    async def _collect_metrics(self):
        def _sample():
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory().percent
            net = psutil.net_io_counters()
            return cpu, ram, net

        cpu, ram, net = await run_in_executor(_sample)
        now = time.time()
        elapsed = max(now - self._prev_net_time, 0.1)
        sent_kbs = max(0, (net.bytes_sent - self._prev_net.bytes_sent) / elapsed / 1024)
        recv_kbs = max(0, (net.bytes_recv - self._prev_net.bytes_recv) / elapsed / 1024)
        self._prev_net = net
        self._prev_net_time = now

        self.cpu_hist.append(cpu)
        self.ram_hist.append(ram)
        self.net_sent_hist.append(sent_kbs)
        self.net_recv_hist.append(recv_kbs)
        self.timestamps.append(now)

        for lst in (self.cpu_hist, self.ram_hist, self.net_sent_hist,
                    self.net_recv_hist, self.timestamps):
            if len(lst) > MAX_HISTORY:
                lst.pop(0)

    async def _check_thresholds(self):
        channel = None
        if self.alert_channel_id:
            channel = self.bot.get_channel(self.alert_channel_id)
        if not channel:
            return

        latest_cpu = self.cpu_hist[-1] if self.cpu_hist else 0
        latest_ram = self.ram_hist[-1] if self.ram_hist else 0

        if latest_cpu > Config.CPU_ALERT_THRESHOLD and not self._alerted["cpu"]:
            self._alerted["cpu"] = True
            db.log_alert("cpu", f"CPU at {latest_cpu:.1f}%", self.alert_channel_id)
            await channel.send(embed=build_embed(
                "⚠️ HIGH CPU ALERT",
                f"CPU usage is **{latest_cpu:.1f}%** (threshold: {Config.CPU_ALERT_THRESHOLD}%)",
                color=Config.COLOR_ERROR,
            ))
        elif latest_cpu <= Config.CPU_ALERT_THRESHOLD - 5:
            self._alerted["cpu"] = False

        if latest_ram > Config.RAM_ALERT_THRESHOLD and not self._alerted["ram"]:
            self._alerted["ram"] = True
            db.log_alert("ram", f"RAM at {latest_ram:.1f}%", self.alert_channel_id)
            await channel.send(embed=build_embed(
                "⚠️ HIGH RAM ALERT",
                f"RAM usage is **{latest_ram:.1f}%** (threshold: {Config.RAM_ALERT_THRESHOLD}%)",
                color=Config.COLOR_ERROR,
            ))
        elif latest_ram <= Config.RAM_ALERT_THRESHOLD - 5:
            self._alerted["ram"] = False

        # Disk check
        try:
            disk = psutil.disk_usage("/")
            if disk.percent > Config.DISK_ALERT_THRESHOLD and not self._alerted["disk"]:
                self._alerted["disk"] = True
                db.log_alert("disk", f"Disk at {disk.percent:.1f}%", self.alert_channel_id)
                await channel.send(embed=build_embed(
                    "⚠️ DISK FULL ALERT",
                    f"Disk usage is **{disk.percent:.1f}%** (threshold: {Config.DISK_ALERT_THRESHOLD}%)",
                    color=Config.COLOR_ERROR,
                ))
            elif disk.percent <= Config.DISK_ALERT_THRESHOLD - 5:
                self._alerted["disk"] = False
        except Exception:
            pass

    # ─── Commands ─────────────────────────────────────────────────────────────

    @commands.command(name="startmonitor", aliases=["monitor", "monstart"])
    async def start_monitor(self, ctx, interval: int = None):
        """Start background monitoring with alerts in this channel."""
        if interval:
            Config.MONITOR_INTERVAL = max(5, interval)
        self.alert_channel_id = ctx.channel.id
        self.monitoring_active = True
        db.set_setting("monitor_channel", str(ctx.channel.id))
        db.set_setting("monitor_active", "1")

        embed = build_embed(
            "Monitoring Started",
            f"✅ Monitoring active in this channel.\n"
            f"**Interval:** {Config.MONITOR_INTERVAL}s\n"
            f"**CPU Alert:** >{Config.CPU_ALERT_THRESHOLD}%\n"
            f"**RAM Alert:** >{Config.RAM_ALERT_THRESHOLD}%\n"
            f"**Disk Alert:** >{Config.DISK_ALERT_THRESHOLD}%",
            color=Config.COLOR_SUCCESS,
        )
        await ctx.send(embed=embed)

    @commands.command(name="stopmonitor", aliases=["monstop"])
    async def stop_monitor(self, ctx):
        """Stop background monitoring."""
        self.monitoring_active = False
        db.set_setting("monitor_active", "0")
        await ctx.send(embed=build_embed("Monitoring Stopped", "⛔ Monitoring disabled.", color=Config.COLOR_WARNING))

    @commands.command(name="monitorstatus", aliases=["monstatus"])
    async def monitor_status(self, ctx):
        """Check monitoring status and recent stats."""
        fields = [
            ("Status", "✅ Active" if self.monitoring_active else "⛔ Stopped", True),
            ("Channel", f"<#{self.alert_channel_id}>" if self.alert_channel_id else "None", True),
            ("Interval", f"{Config.MONITOR_INTERVAL}s", True),
            ("Samples", str(len(self.cpu_hist)), True),
        ]
        if self.cpu_hist:
            fields += [
                ("CPU Now", f"{self.cpu_hist[-1]:.1f}%", True),
                ("CPU Avg", f"{sum(self.cpu_hist)/len(self.cpu_hist):.1f}%", True),
                ("CPU Max", f"{max(self.cpu_hist):.1f}%", True),
                ("RAM Now", f"{self.ram_hist[-1]:.1f}%", True),
                ("RAM Avg", f"{sum(self.ram_hist)/len(self.ram_hist):.1f}%", True),
                ("RAM Max", f"{max(self.ram_hist):.1f}%", True),
            ]
        await ctx.send(embed=build_embed("Monitor Status", color=Config.COLOR_MONITOR, fields=fields))

    @commands.command(name="cpuhistory", aliases=["cpuhist"])
    async def cpu_history(self, ctx):
        """Chart CPU usage history."""
        if not self.cpu_hist:
            await ctx.send(embed=build_embed("CPU History", "No data yet. Start monitoring first.", color=Config.COLOR_WARNING))
            return
        async with ctx.typing():
            buf = await run_in_executor(cpu_history_chart, self.cpu_hist)
            await ctx.send(
                embed=build_embed("CPU History", color=Config.COLOR_MONITOR),
                file=discord.File(buf, "cpu_history.png"),
            )

    @commands.command(name="nethistory", aliases=["nethist"])
    async def net_history(self, ctx):
        """Chart network I/O history."""
        if not self.net_sent_hist:
            await ctx.send(embed=build_embed("Net History", "No data yet.", color=Config.COLOR_WARNING))
            return
        async with ctx.typing():
            buf = await run_in_executor(network_chart, self.net_sent_hist, self.net_recv_hist)
            await ctx.send(
                embed=build_embed("Network I/O History", color=Config.COLOR_MONITOR),
                file=discord.File(buf, "net_history.png"),
            )

    @commands.command(name="exportmetrics", aliases=["export"])
    async def export_metrics(self, ctx, fmt: str = "csv"):
        """Export monitoring data as CSV or JSON."""
        if not self.cpu_hist:
            await ctx.send(embed=build_embed("Export", "No data to export.", color=Config.COLOR_WARNING))
            return

        async with ctx.typing():
            rows = list(zip(self.timestamps, self.cpu_hist, self.ram_hist,
                            self.net_sent_hist, self.net_recv_hist))

            if fmt.lower() == "json":
                data = [
                    {"ts": t, "cpu": c, "ram": r, "net_sent_kbs": s, "net_recv_kbs": v}
                    for t, c, r, s, v in rows
                ]
                buf = io.BytesIO(json.dumps(data, indent=2).encode())
                filename = "metrics.json"
            else:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["timestamp", "cpu_pct", "ram_pct", "net_sent_kbs", "net_recv_kbs"])
                writer.writerows(rows)
                buf = io.BytesIO(output.getvalue().encode())
                filename = "metrics.csv"

            await ctx.send(
                embed=build_embed("Export Metrics", f"✅ {len(rows)} samples exported as {fmt.upper()}.",
                                   color=Config.COLOR_SUCCESS),
                file=discord.File(buf, filename),
            )

    @commands.command(name="setalert", aliases=["alertthreshold"])
    async def set_alert(self, ctx, metric: str, threshold: float):
        """Set alert threshold. metric: cpu|ram|disk"""
        metric = metric.lower()
        if metric == "cpu":
            Config.CPU_ALERT_THRESHOLD = threshold
        elif metric == "ram":
            Config.RAM_ALERT_THRESHOLD = threshold
        elif metric == "disk":
            Config.DISK_ALERT_THRESHOLD = threshold
        else:
            await ctx.send(embed=build_embed("Alert", "Invalid metric. Use: cpu, ram, disk", color=Config.COLOR_ERROR))
            return
        db.set_setting(f"alert_{metric}", str(threshold))
        await ctx.send(embed=build_embed("Alert", f"✅ {metric.upper()} alert set to **{threshold}%**", color=Config.COLOR_SUCCESS))

    @commands.command(name="alerts", aliases=["recentalerts"])
    async def recent_alerts(self, ctx, limit: int = 10):
        """Show recent system alerts."""
        rows_data = db.get_recent_alerts(limit)
        if not rows_data:
            await ctx.send(embed=build_embed("Alerts", "No alerts recorded.", color=Config.COLOR_INFO))
            return
        rows = []
        for r in rows_data:
            rows.append(f"`{r['ts']}` **{r['alert_type'].upper()}**: {r['message']}")
        await ctx.send(embed=build_embed(
            f"Recent Alerts (last {limit})",
            "\n".join(rows),
            color=Config.COLOR_WARNING,
        ))


async def setup(bot):
    await bot.add_cog(Monitoring(bot))
