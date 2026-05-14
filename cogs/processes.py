"""
Process management commands — list, kill, find, suspend, resume, set priority.
"""

import os
import signal
import platform

import discord
import psutil
from discord.ext import commands

from services.database import db
from services.viz_service import process_bar_chart
from utils.config import Config
from utils.helpers import admin_only, build_embed, bytes_to_human, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.processes")


class Processes(commands.Cog):
    """Full process management."""

    def __init__(self, bot):
        self.bot = bot

    def _get_processes(self) -> list:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info",
                                       "status", "username", "create_time"]):
            try:
                info = p.info
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"] or "?",
                    "cpu": info["cpu_percent"] or 0.0,
                    "mem": info["memory_info"].rss / 1024**2 if info["memory_info"] else 0,
                    "status": info["status"],
                    "user": info["username"] or "?",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return procs

    @commands.command(name="ps", aliases=["processes", "tasklist"])
    async def list_processes(self, ctx, sort_by: str = "cpu", limit: int = 20):
        """List running processes. sort_by: cpu|mem|name|pid"""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None,
                       "ps", f"sort={sort_by} limit={limit}")
        async with ctx.typing():
            procs = await run_in_executor(self._get_processes)
            key_map = {"cpu": "cpu", "mem": "mem", "name": "name", "pid": "pid"}
            key = key_map.get(sort_by.lower(), "cpu")
            procs.sort(key=lambda x: x[key], reverse=(key in ("cpu", "mem")))
            top = procs[:limit]

            rows = [f"`{'PID':>6}` `{'CPU%':>5}` `{'MEM MB':>7}` `{'STATUS':>10}` `NAME`"]
            for p in top:
                rows.append(
                    f"`{p['pid']:>6}` `{p['cpu']:>5.1f}` `{p['mem']:>7.1f}` "
                    f"`{p['status']:>10}` `{p['name'][:30]}`"
                )

            embed = build_embed(
                f"Processes (sorted by {sort_by}, top {limit})",
                truncate("\n".join(rows), 4000),
                color=Config.COLOR_INFO,
            )
            embed.set_footer(text=f"Total processes: {len(procs)}")
            await ctx.send(embed=embed)

    @commands.command(name="pschart", aliases=["psgraph"])
    async def process_chart(self, ctx, metric: str = "cpu"):
        """Bar chart of top processes by CPU or memory."""
        async with ctx.typing():
            procs = await run_in_executor(self._get_processes)
            buf = await run_in_executor(process_bar_chart, procs, metric)
            await ctx.send(
                embed=build_embed(f"Top Processes by {metric.upper()}", color=Config.COLOR_INFO),
                file=discord.File(buf, "processes.png"),
            )

    @commands.command(name="kill", aliases=["killproc", "terminate"])
    @admin_only()
    async def kill_process(self, ctx, pid_or_name: str):
        """Kill a process by PID or name."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None,
                       "kill", pid_or_name)
        killed = []
        errors = []

        try:
            targets = []
            if pid_or_name.isdigit():
                targets = [psutil.Process(int(pid_or_name))]
            else:
                targets = [p for p in psutil.process_iter(["name"])
                           if pid_or_name.lower() in (p.info["name"] or "").lower()]

            for proc in targets:
                try:
                    name = proc.name()
                    proc.kill()
                    killed.append(f"{name} (PID {proc.pid})")
                except psutil.AccessDenied:
                    errors.append(f"Access denied to PID {proc.pid}")
                except psutil.NoSuchProcess:
                    errors.append(f"PID {proc.pid} already gone")

        except psutil.NoSuchProcess:
            await ctx.send(embed=build_embed("Kill", f"PID {pid_or_name} not found.", color=Config.COLOR_ERROR))
            return

        desc = ""
        if killed:
            desc += "✅ Killed:\n" + "\n".join(f"• {k}" for k in killed)
        if errors:
            desc += "\n⚠️ Errors:\n" + "\n".join(f"• {e}" for e in errors)

        color = Config.COLOR_SUCCESS if killed and not errors else Config.COLOR_WARNING if killed else Config.COLOR_ERROR
        await ctx.send(embed=build_embed("Kill Process", desc, color=color))

    @commands.command(name="suspend", aliases=["pause"])
    @admin_only()
    async def suspend_process(self, ctx, pid: int):
        """Suspend (pause) a process by PID."""
        try:
            proc = psutil.Process(pid)
            proc.suspend()
            await ctx.send(embed=build_embed("Suspend", f"⏸️ Suspended `{proc.name()}` (PID {pid})", color=Config.COLOR_WARNING))
        except psutil.NoSuchProcess:
            await ctx.send(embed=build_embed("Suspend", f"PID {pid} not found.", color=Config.COLOR_ERROR))
        except psutil.AccessDenied:
            await ctx.send(embed=build_embed("Suspend", "Access denied.", color=Config.COLOR_ERROR))

    @commands.command(name="resume", aliases=["unpause"])
    @admin_only()
    async def resume_process(self, ctx, pid: int):
        """Resume a suspended process."""
        try:
            proc = psutil.Process(pid)
            proc.resume()
            await ctx.send(embed=build_embed("Resume", f"▶️ Resumed `{proc.name()}` (PID {pid})", color=Config.COLOR_SUCCESS))
        except psutil.NoSuchProcess:
            await ctx.send(embed=build_embed("Resume", f"PID {pid} not found.", color=Config.COLOR_ERROR))
        except psutil.AccessDenied:
            await ctx.send(embed=build_embed("Resume", "Access denied.", color=Config.COLOR_ERROR))

    @commands.command(name="findproc", aliases=["psfind", "psgrep"])
    async def find_process(self, ctx, *, name: str):
        """Search for processes by name."""
        matches = []
        for p in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
            try:
                if name.lower() in (p.info["name"] or "").lower():
                    mem = p.info["memory_info"].rss / 1024**2 if p.info["memory_info"] else 0
                    matches.append(
                        f"`PID {p.info['pid']:>6}` | `{p.info['status']:>8}` | "
                        f"`{p.info['cpu_percent']:>5.1f}%` | `{mem:>7.1f} MB` | `{p.info['name']}`"
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not matches:
            await ctx.send(embed=build_embed("Find Process", f"No processes matching `{name}`.", color=Config.COLOR_WARNING))
            return

        embed = build_embed(
            f"Processes matching '{name}'",
            truncate("\n".join(matches), 4000),
            color=Config.COLOR_INFO,
        )
        embed.set_footer(text=f"{len(matches)} found")
        await ctx.send(embed=embed)

    @commands.command(name="procinfo", aliases=["pidinfo"])
    async def process_info(self, ctx, pid: int):
        """Detailed info for a specific PID."""
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                mem = p.memory_info()
                fields = [
                    ("Name", p.name(), True),
                    ("PID", str(pid), True),
                    ("Status", p.status(), True),
                    ("CPU %", f"{p.cpu_percent(interval=0.3):.2f}%", True),
                    ("RSS Memory", bytes_to_human(mem.rss), True),
                    ("VMS Memory", bytes_to_human(mem.vms), True),
                    ("Username", p.username(), True),
                    ("Nice", str(p.nice()), True),
                    ("Threads", str(p.num_threads()), True),
                    ("Exe", truncate(p.exe() or "N/A", 80), False),
                    ("CWD", truncate(p.cwd() or "N/A", 80), False),
                    ("Cmdline", truncate(" ".join(p.cmdline() or []), 200), False),
                ]
            await ctx.send(embed=build_embed(f"Process Info: {p.name()}", color=Config.COLOR_INFO, fields=fields))
        except psutil.NoSuchProcess:
            await ctx.send(embed=build_embed("Process Info", f"PID {pid} not found.", color=Config.COLOR_ERROR))
        except psutil.AccessDenied:
            await ctx.send(embed=build_embed("Process Info", "Access denied.", color=Config.COLOR_ERROR))

    @commands.command(name="setpriority", aliases=["nice", "renice"])
    @admin_only()
    async def set_priority(self, ctx, pid: int, priority: str = "normal"):
        """Set process priority. Levels: idle, low, normal, high, realtime"""
        priority_map = {
            "idle": psutil.IDLE_PRIORITY_CLASS if platform.system() == "Windows" else 19,
            "low": psutil.BELOW_NORMAL_PRIORITY_CLASS if platform.system() == "Windows" else 10,
            "normal": psutil.NORMAL_PRIORITY_CLASS if platform.system() == "Windows" else 0,
            "high": psutil.HIGH_PRIORITY_CLASS if platform.system() == "Windows" else -10,
            "realtime": psutil.REALTIME_PRIORITY_CLASS if platform.system() == "Windows" else -20,
        }
        nice_val = priority_map.get(priority.lower())
        if nice_val is None:
            await ctx.send(embed=build_embed("Priority", "Invalid priority. Use: idle/low/normal/high/realtime", color=Config.COLOR_ERROR))
            return
        try:
            proc = psutil.Process(pid)
            if platform.system() == "Windows":
                proc.nice(nice_val)
            else:
                os.setpriority(os.PRIO_PROCESS, pid, nice_val)
            await ctx.send(embed=build_embed("Priority", f"✅ Set `{proc.name()}` (PID {pid}) to **{priority}** priority.", color=Config.COLOR_SUCCESS))
        except psutil.NoSuchProcess:
            await ctx.send(embed=build_embed("Priority", f"PID {pid} not found.", color=Config.COLOR_ERROR))
        except psutil.AccessDenied:
            await ctx.send(embed=build_embed("Priority", "Access denied.", color=Config.COLOR_ERROR))

    @commands.command(name="topcpu", aliases=["topprocscpu"])
    async def top_cpu(self, ctx, limit: int = 10):
        """Top N processes by CPU usage."""
        procs = await run_in_executor(self._get_processes)
        top = sorted(procs, key=lambda x: x["cpu"], reverse=True)[:limit]
        rows = [f"`{'CPU%':>5}` `{'MEM MB':>7}` `{'PID':>6}` `NAME`"]
        for p in top:
            rows.append(f"`{p['cpu']:>5.1f}` `{p['mem']:>7.1f}` `{p['pid']:>6}` `{p['name'][:30]}`")
        await ctx.send(embed=build_embed(f"Top {limit} by CPU", "\n".join(rows), color=Config.COLOR_INFO))

    @commands.command(name="topmem", aliases=["topprocsram"])
    async def top_mem(self, ctx, limit: int = 10):
        """Top N processes by memory usage."""
        procs = await run_in_executor(self._get_processes)
        top = sorted(procs, key=lambda x: x["mem"], reverse=True)[:limit]
        rows = [f"`{'MEM MB':>7}` `{'CPU%':>5}` `{'PID':>6}` `NAME`"]
        for p in top:
            rows.append(f"`{p['mem']:>7.1f}` `{p['cpu']:>5.1f}` `{p['pid']:>6}` `{p['name'][:30]}`")
        await ctx.send(embed=build_embed(f"Top {limit} by Memory", "\n".join(rows), color=Config.COLOR_INFO))


async def setup(bot):
    await bot.add_cog(Processes(bot))
