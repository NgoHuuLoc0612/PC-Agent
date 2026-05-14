"""
Visualizations cog — on-demand rich charts, live dashboard, heatmaps, sparklines.
"""

import asyncio
import io
import time
from typing import List

import discord
import psutil
from discord.ext import commands

from services.viz_service import (
    cpu_history_chart, disk_usage_chart, network_chart,
    process_bar_chart, ram_donut_chart, system_dashboard, temperature_chart
)
from utils.config import Config
from utils.helpers import build_embed, run_in_executor
from utils.logger import setup_logger

logger = setup_logger("cog.visualizations")


def _cpu_heatmap(per_core_history: List[List[float]]) -> io.BytesIO:
    """Generate a per-core CPU heatmap over time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    DARK_BG = "#1a1a2e"
    TEXT = "#e0e0e0"

    n_cores = len(per_core_history)
    n_samples = max(len(h) for h in per_core_history) if per_core_history else 1

    matrix = np.zeros((n_cores, n_samples))
    for i, hist in enumerate(per_core_history):
        for j, val in enumerate(hist):
            matrix[i, j] = val

    fig, ax = plt.subplots(figsize=(12, max(3, n_cores * 0.5)))
    fig.patch.set_facecolor(DARK_BG)
    ax.set_facecolor(DARK_BG)

    im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=100,
                   interpolation="nearest")
    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label("%", color=TEXT)
    cbar.ax.yaxis.set_tick_params(color=TEXT)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=TEXT)

    ax.set_yticks(range(n_cores))
    ax.set_yticklabels([f"Core {i}" for i in range(n_cores)], color=TEXT)
    ax.set_xlabel("Time (samples)", color=TEXT)
    ax.tick_params(colors=TEXT)
    ax.set_title("CPU Per-Core Heatmap", color=TEXT, fontsize=13, fontweight="bold")
    for spine in ax.spines.values():
        spine.set_edgecolor("#2a2a4a")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120, facecolor=DARK_BG)
    buf.seek(0)
    plt.close(fig)
    return buf


def _sparkline_embed(values: List[float], label: str, unit: str = "%") -> io.BytesIO:
    """Tiny sparkline chart for embedding."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    DARK_BG = "#1a1a2e"
    ACCENT = "#e94560"
    TEXT = "#e0e0e0"

    fig, ax = plt.subplots(figsize=(5, 1.5))
    fig.patch.set_facecolor(DARK_BG)
    ax.set_facecolor(DARK_BG)

    x = list(range(len(values)))
    ax.fill_between(x, values, alpha=0.3, color=ACCENT)
    ax.plot(x, values, color=ACCENT, linewidth=1.5)

    current = values[-1] if values else 0
    ax.text(0.98, 0.85, f"{current:.1f}{unit}", transform=ax.transAxes,
            ha="right", va="top", color=TEXT, fontsize=12, fontweight="bold")
    ax.set_xlim(0, max(len(values) - 1, 1))
    ax.set_ylim(0, max(max(values) * 1.1, 1) if values else 1)
    ax.axis("off")
    ax.set_title(label, color=TEXT, fontsize=9, pad=2)

    fig.tight_layout(pad=0.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100, facecolor=DARK_BG)
    buf.seek(0)
    plt.close(fig)
    return buf


def _memory_waterfall(samples: List[float]) -> io.BytesIO:
    """Waterfall-style RAM usage chart."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    DARK_BG = "#1a1a2e"
    PANEL_BG = "#16213e"
    TEXT = "#e0e0e0"
    GRID = "#2a2a4a"

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor(DARK_BG)

    x = np.arange(len(samples))

    # Main chart
    ax1.set_facecolor(PANEL_BG)
    colors = ["#e94560" if v > 85 else "#f5a623" if v > 70 else "#00d4aa" for v in samples]
    ax1.bar(x, samples, color=colors, width=0.8, alpha=0.9)
    ax1.plot(x, samples, color="#ffffff", linewidth=1, alpha=0.5)
    ax1.set_ylim(0, 100)
    ax1.set_ylabel("RAM %", color=TEXT)
    ax1.set_title("RAM Usage Over Time", color=TEXT, fontsize=13, fontweight="bold")
    ax1.tick_params(colors=TEXT)
    ax1.grid(color=GRID, linewidth=0.5, alpha=0.5)
    for spine in ax1.spines.values():
        spine.set_edgecolor(GRID)

    # Delta chart (rate of change)
    ax2.set_facecolor(PANEL_BG)
    if len(samples) > 1:
        deltas = [samples[i] - samples[i-1] for i in range(1, len(samples))]
        delta_colors = ["#e94560" if d > 0 else "#00d4aa" for d in deltas]
        ax2.bar(x[1:], deltas, color=delta_colors, width=0.8, alpha=0.9)
    ax2.set_ylabel("Δ%", color=TEXT)
    ax2.axhline(0, color="#ffffff", linewidth=0.5, alpha=0.5)
    ax2.tick_params(colors=TEXT)
    ax2.grid(color=GRID, linewidth=0.5, alpha=0.5)
    for spine in ax2.spines.values():
        spine.set_edgecolor(GRID)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120, facecolor=DARK_BG)
    buf.seek(0)
    plt.close(fig)
    return buf


class Visualizations(commands.Cog):
    """Advanced charting and visualization commands."""

    def __init__(self, bot):
        self.bot = bot
        self._core_histories: List[List[float]] = []
        self._ram_samples: List[float] = []
        self._sampling = False
        self._sample_task = None

    async def _collect_cores(self):
        """Background: collect per-core CPU percentages."""
        while self._sampling:
            cores = psutil.cpu_percent(percpu=True)
            if not self._core_histories:
                self._core_histories = [[] for _ in cores]
            for i, val in enumerate(cores):
                if i < len(self._core_histories):
                    self._core_histories[i].append(val)
                    if len(self._core_histories[i]) > 120:
                        self._core_histories[i].pop(0)

            ram = psutil.virtual_memory().percent
            self._ram_samples.append(ram)
            if len(self._ram_samples) > 120:
                self._ram_samples.pop(0)

            await asyncio.sleep(2)

    @commands.command(name="viz", aliases=["charts", "visualize"])
    async def viz_menu(self, ctx):
        """Show available visualizations."""
        fields = [
            ("!dashboard", "Full system dashboard (CPU, RAM, Disk, Net)", False),
            ("!cpuhistory", "CPU usage over time (line chart)", False),
            ("!cpuheatmap", "Per-core CPU heatmap", False),
            ("!ram", "RAM usage donut chart", False),
            ("!ramwaterfall", "RAM waterfall + delta chart", False),
            ("!disk", "Disk usage horizontal bars", False),
            ("!pschart", "Top processes bar chart (cpu/mem)", False),
            ("!netchart", "Network I/O line chart", False),
            ("!temp", "Temperature gauge chart", False),
            ("!sparklines", "Quick multi-metric sparklines", False),
        ]
        await ctx.send(embed=build_embed(
            "📊 Available Visualizations",
            "All charts use dark-themed matplotlib with real-time data.",
            color=Config.COLOR_INFO,
            fields=fields,
        ))

    @commands.command(name="cpuheatmap", aliases=["coreheatmap"])
    async def cpu_heatmap(self, ctx):
        """Per-core CPU usage heatmap (requires sampling to be running)."""
        async with ctx.typing():
            if not self._core_histories:
                # Collect 10 quick samples
                await ctx.send(embed=build_embed("Heatmap", "⏳ Collecting 10 samples (20s)...", color=Config.COLOR_INFO))
                for _ in range(10):
                    cores = psutil.cpu_percent(percpu=True)
                    if not self._core_histories:
                        self._core_histories = [[] for _ in cores]
                    for i, val in enumerate(cores):
                        if i < len(self._core_histories):
                            self._core_histories[i].append(val)
                    await asyncio.sleep(2)

            buf = await run_in_executor(_cpu_heatmap, self._core_histories)
            await ctx.send(
                embed=build_embed("CPU Per-Core Heatmap", color=Config.COLOR_SYSTEM),
                file=discord.File(buf, "cpu_heatmap.png"),
            )

    @commands.command(name="ramwaterfall", aliases=["memwaterfall"])
    async def ram_waterfall(self, ctx):
        """RAM usage waterfall chart with delta."""
        async with ctx.typing():
            if not self._ram_samples or len(self._ram_samples) < 5:
                # Collect quick samples
                samples = []
                for _ in range(20):
                    samples.append(psutil.virtual_memory().percent)
                    await asyncio.sleep(0.5)
            else:
                samples = self._ram_samples

            buf = await run_in_executor(_memory_waterfall, samples)
            await ctx.send(
                embed=build_embed("RAM Waterfall", color=Config.COLOR_SYSTEM),
                file=discord.File(buf, "ram_waterfall.png"),
            )

    @commands.command(name="sparklines", aliases=["sparks"])
    async def sparklines(self, ctx):
        """Quick multi-metric sparklines for CPU, RAM, and Network."""
        async with ctx.typing():
            # Collect quick history
            cpu_h, ram_h, net_up, net_dn = [], [], [], []
            prev_net = psutil.net_io_counters()
            prev_t = time.time()

            await ctx.send(embed=build_embed("Sparklines", "⏳ Sampling 15 data points...", color=Config.COLOR_INFO))

            for _ in range(15):
                cpu_h.append(psutil.cpu_percent(interval=0.1))
                ram_h.append(psutil.virtual_memory().percent)
                net = psutil.net_io_counters()
                now = time.time()
                elapsed = max(now - prev_t, 0.1)
                net_up.append(max(0, (net.bytes_sent - prev_net.bytes_sent) / elapsed / 1024))
                net_dn.append(max(0, (net.bytes_recv - prev_net.bytes_recv) / elapsed / 1024))
                prev_net = net
                prev_t = now
                await asyncio.sleep(1)

            files = []
            for data, label, unit in [
                (cpu_h, "CPU", "%"),
                (ram_h, "RAM", "%"),
                (net_up, "Upload", "KB/s"),
                (net_dn, "Download", "KB/s"),
            ]:
                buf = await run_in_executor(_sparkline_embed, data, label, unit)
                files.append(discord.File(buf, f"spark_{label.lower()}.png"))

            embed = build_embed(
                "📈 System Sparklines",
                f"CPU: `{cpu_h[-1]:.1f}%` | RAM: `{ram_h[-1]:.1f}%` | "
                f"↑ `{net_up[-1]:.1f}` ↓ `{net_dn[-1]:.1f}` KB/s",
                color=Config.COLOR_SYSTEM,
            )
            await ctx.send(embed=embed)
            for f in files:
                await ctx.send(file=f)

    @commands.command(name="startsampling", aliases=["samplestart"])
    async def start_sampling(self, ctx):
        """Start background data sampling for rich visualizations."""
        if self._sampling:
            await ctx.send(embed=build_embed("Sampling", "Already running.", color=Config.COLOR_WARNING))
            return
        self._sampling = True
        self._sample_task = asyncio.create_task(self._collect_cores())
        await ctx.send(embed=build_embed("Sampling", "✅ Background sampling started (2s interval).", color=Config.COLOR_SUCCESS))

    @commands.command(name="stopsampling", aliases=["samplestop"])
    async def stop_sampling(self, ctx):
        """Stop background data sampling."""
        self._sampling = False
        if self._sample_task:
            self._sample_task.cancel()
        await ctx.send(embed=build_embed("Sampling", "⛔ Sampling stopped.", color=Config.COLOR_WARNING))

    @commands.command(name="vizstatus")
    async def viz_status(self, ctx):
        """Sampling status and data availability."""
        fields = [
            ("Sampling", "✅ Active" if self._sampling else "⛔ Inactive", True),
            ("CPU Cores tracked", str(len(self._core_histories)), True),
            ("RAM Samples", str(len(self._ram_samples)), True),
        ]
        if self._core_histories:
            fields.append(("Core samples each", str(len(self._core_histories[0])), True))
        await ctx.send(embed=build_embed("Visualization Status", color=Config.COLOR_INFO, fields=fields))

    @commands.command(name="metricsnapshot", aliases=["snapshot"])
    async def metric_snapshot(self, ctx):
        """Capture all charts in one multi-file burst."""
        async with ctx.typing():
            await ctx.send(embed=build_embed("Snapshot", "📸 Generating all charts...", color=Config.COLOR_INFO))

            ram = psutil.virtual_memory()
            disks = []
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                    disks.append({"mount": part.mountpoint, "used": u.used/1024**3, "total": u.total/1024**3})
                except PermissionError:
                    continue

            cpu_data = [psutil.cpu_percent(interval=0.2) for _ in range(5)]

            charts = [
                ("cpu.png", cpu_history_chart, (cpu_data,)),
                ("ram.png", ram_donut_chart, (ram.used/1024**3, ram.total/1024**3)),
                ("disk.png", disk_usage_chart, (disks,)),
            ]

            for name, fn, args in charts:
                try:
                    buf = await run_in_executor(fn, *args)
                    await ctx.send(file=discord.File(buf, name))
                except Exception as e:
                    logger.error(f"Snapshot chart {name}: {e}")

            await ctx.send(embed=build_embed("Snapshot", "✅ All charts generated.", color=Config.COLOR_SUCCESS))


async def setup(bot):
    await bot.add_cog(Visualizations(bot))
