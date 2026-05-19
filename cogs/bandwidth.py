"""
bandwidth.py — Internet bandwidth testing via speedtest-cli.

Features:
  - Download / upload / ping via Speedtest.net
  - Server selection (closest, best, or by ID)
  - History tracking with per-channel results
  - Chart of past speed-test results
  - Latency / jitter breakdown
  - ISP and server metadata display

Requires:
    pip install speedtest-cli
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import build_embed, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.bandwidth")

try:
    import speedtest as st_lib   # speedtest-cli
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False
    logger.warning("speedtest-cli not installed. Run: pip install speedtest-cli")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SpeedResult:
    ts:          float
    download:    float   # Mbps
    upload:      float   # Mbps
    ping:        float   # ms
    server_name: str
    server_cc:   str
    isp:         str
    share_url:   Optional[str] = None


# ---------------------------------------------------------------------------
# Blocking helpers (always run in executor)
# ---------------------------------------------------------------------------

def _build_st(server_id: Optional[int] = None) -> "st_lib.Speedtest":
    """Initialise a Speedtest object and pick the server."""
    s = st_lib.Speedtest(secure=True)
    s.get_config()
    if server_id is not None:
        s.get_servers([server_id])
        s.get_best_server()
    else:
        s.get_best_server()
    return s


def _run_full_test(server_id: Optional[int] = None, threads: int = 4) -> SpeedResult:
    """Execute download + upload test. Blocking — call via executor."""
    s = _build_st(server_id)
    s.download(threads=threads)
    s.upload(threads=threads, pre_allocate=False)

    r = s.results
    svr = r.server

    return SpeedResult(
        ts=time.time(),
        download=r.download / 1_000_000,          # bps → Mbps
        upload=r.upload   / 1_000_000,
        ping=r.ping,
        server_name=f"{svr['sponsor']} ({svr['name']}, {svr['country']})",
        server_cc=svr.get("cc", ""),
        isp=r.client.get("isp", "Unknown"),
        share_url=None,  # skip share() to avoid extra round-trip
    )


def _list_servers(limit: int = 10) -> List[dict]:
    """Return <limit> closest servers. Blocking."""
    s = st_lib.Speedtest(secure=True)
    s.get_config()
    servers = s.get_closest_servers(limit)
    flat = []
    for grp in servers.values():
        flat.extend(grp)
    flat.sort(key=lambda x: x.get("d", 0))
    return flat[:limit]


def _ping_only(server_id: Optional[int] = None) -> dict:
    """Measure latency + jitter without a full throughput test. Blocking."""
    s = _build_st(server_id)
    svr = s.best
    # Run 5 latency samples
    latencies = []
    for _ in range(5):
        start = time.perf_counter()
        s.get_best_server()
        latencies.append((time.perf_counter() - start) * 1000)
    avg  = sum(latencies) / len(latencies)
    jit  = max(latencies) - min(latencies)
    return {
        "server": f"{svr['sponsor']} ({svr['name']}, {svr['country']})",
        "ping":   svr.get("latency", avg),
        "avg_ms": avg,
        "jitter": jit,
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "isp":    s.results.client.get("isp", "Unknown"),
    }


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Bandwidth(commands.Cog):
    """Internet bandwidth testing via Speedtest.net."""

    MAX_HISTORY = 50

    def __init__(self, bot):
        self.bot = bot
        self._history: List[SpeedResult] = []
        self._running = False   # prevent simultaneous tests

    # ── Guard ──────────────────────────────────────────────────────────────

    async def _check(self, ctx) -> bool:
        if not _ST_AVAILABLE:
            await ctx.send(embed=build_embed(
                "❌ speedtest-cli Not Installed",
                "```pip install speedtest-cli```",
                color=Config.COLOR_ERROR,
            ))
            return False
        if self._running:
            await ctx.send(embed=build_embed(
                "⏳ Test In Progress",
                "A bandwidth test is already running. Please wait.",
                color=Config.COLOR_WARNING,
            ))
            return False
        return True

    # ── Commands ───────────────────────────────────────────────────────────

    @commands.command(name="speedtest2", aliases=["bwtest"])
    async def speed_test(self, ctx, threads: int = 4):
        """
        Run a full download + upload speed test.
        !speedtest [threads=4]
        Large thread counts (>8) rarely improve results.
        """
        if not await self._check(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "🌐 Speed Test Running…",
            "⏳ Selecting best server and measuring throughput…\n"
            "*(this takes ~30–60 seconds)*",
            color=Config.COLOR_MONITOR,
        ))

        self._running = True
        try:
            result: SpeedResult = await run_in_executor(_run_full_test, None, max(1, min(threads, 16)))
        except Exception as exc:
            logger.error(f"Speedtest failed: {exc}")
            await msg.edit(embed=build_embed(
                "❌ Speed Test Failed",
                f"```{exc}```",
                color=Config.COLOR_ERROR,
            ))
            return
        finally:
            self._running = False

        # Store history
        self._history.append(result)
        if len(self._history) > self.MAX_HISTORY:
            self._history.pop(0)

        # Build embed
        dl_bar = "█" * min(int(result.download / 10), 20)
        ul_bar = "█" * min(int(result.upload   / 10), 20)

        embed = build_embed(
            "🌐 Speed Test Complete",
            f"Tested against **{result.server_name}**",
            color=Config.COLOR_SUCCESS,
            fields=[
                ("📥 Download",
                 f"`{dl_bar:<20}` **{result.download:.1f} Mbps**", False),
                ("📤 Upload",
                 f"`{ul_bar:<20}` **{result.upload:.1f} Mbps**", False),
                ("🏓 Ping",    f"**{result.ping:.1f} ms**", True),
                ("🏢 ISP",     result.isp, True),
                ("🕐 Tested",  datetime.fromtimestamp(result.ts).strftime("%H:%M:%S"), True),
            ],
        )
        await msg.edit(embed=embed)

    @commands.command(name="pingtest", aliases=["jitter"])
    async def ping_test(self, ctx):
        """Measure latency and jitter to the closest Speedtest server."""
        if not await self._check(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "🏓 Latency Test Running…",
            "⏳ Measuring ping and jitter…",
            color=Config.COLOR_MONITOR,
        ))
        self._running = True
        try:
            data = await run_in_executor(_ping_only)
        except Exception as exc:
            await msg.edit(embed=build_embed("❌ Ping Test Failed", f"```{exc}```", color=Config.COLOR_ERROR))
            return
        finally:
            self._running = False

        await msg.edit(embed=build_embed(
            "🏓 Latency & Jitter",
            f"Server: **{data['server']}**\nISP: {data['isp']}",
            color=Config.COLOR_INFO,
            fields=[
                ("Avg Ping",  f"{data['avg_ms']:.2f} ms", True),
                ("Jitter",    f"{data['jitter']:.2f} ms", True),
                ("Min / Max", f"{data['min_ms']:.2f} / {data['max_ms']:.2f} ms", True),
            ],
        ))

    @commands.command(name="speedservers", aliases=["listservers", "stservers"])
    async def list_servers(self, ctx, limit: int = 8):
        """List the closest Speedtest.net servers. !speedservers [limit=8]"""
        if not await self._check(ctx):
            return
        if limit < 1:
            limit = 1
        if limit > 20:
            limit = 20

        msg = await ctx.send(embed=build_embed(
            "🌐 Fetching Servers…", "⏳ Querying Speedtest.net…", color=Config.COLOR_MONITOR
        ))
        self._running = True
        try:
            servers = await run_in_executor(_list_servers, limit)
        except Exception as exc:
            await msg.edit(embed=build_embed("❌ Failed", f"```{exc}```", color=Config.COLOR_ERROR))
            return
        finally:
            self._running = False

        lines = []
        for sv in servers:
            dist = sv.get("d", 0)
            lines.append(
                f"**ID {sv['id']}** — {sv['sponsor']} ({sv['name']}, {sv['country']})  "
                f"~{dist:.0f} km  {sv.get('latency', '?')} ms"
            )
        await msg.edit(embed=build_embed(
            f"🌐 {len(servers)} Closest Speedtest Servers",
            truncate("\n".join(lines), 1800),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="speedhistory", aliases=["bwhistory", "sthist"])
    async def speed_history(self, ctx):
        """Show past speed-test results."""
        if not self._history:
            await ctx.send(embed=build_embed(
                "📋 Speed History", "No tests run yet. Use `!speedtest`.", color=Config.COLOR_WARNING
            ))
            return

        lines = []
        for r in self._history[-10:]:
            ts = datetime.fromtimestamp(r.ts).strftime("%m-%d %H:%M")
            lines.append(
                f"`{ts}` ↓ **{r.download:.1f}** ↑ **{r.upload:.1f}** Mbps  🏓 {r.ping:.0f}ms"
            )
        await ctx.send(embed=build_embed(
            f"📋 Speed History (last {min(10, len(self._history))} tests)",
            "\n".join(lines),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="speedchart", aliases=["bwchart"])
    async def speed_chart(self, ctx):
        """Plot download/upload history as a chart."""
        if len(self._history) < 2:
            await ctx.send(embed=build_embed(
                "📊 Speed Chart",
                "Not enough data. Run `!speedtest` at least twice.",
                color=Config.COLOR_WARNING,
            ))
            return

        def _plot():
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            xs = [datetime.fromtimestamp(r.ts)  for r in self._history]
            dl = [r.download for r in self._history]
            ul = [r.upload   for r in self._history]

            fig, ax = plt.subplots(figsize=(10, 4), dpi=90)
            ax.plot(xs, dl, color="#3498db", linewidth=2, marker="o", label="Download")
            ax.plot(xs, ul, color="#e74c3c", linewidth=2, marker="s", label="Upload")
            ax.fill_between(xs, dl, alpha=0.15, color="#3498db")
            ax.fill_between(xs, ul, alpha=0.15, color="#e74c3c")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
            ax.set_ylabel("Speed (Mbps)")
            ax.set_title("Internet Speed History")
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig.autofmt_xdate()
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            return buf

        async with ctx.typing():
            buf = await run_in_executor(_plot)

        await ctx.send(
            embed=build_embed("📊 Speed History Chart", color=Config.COLOR_MONITOR),
            file=discord.File(buf, "speed_chart.png"),
        )

    @commands.command(name="speedtestid", aliases=["bwtestid"])
    async def speed_test_by_id(self, ctx, server_id: int, threads: int = 4):
        """
        Run a speed test against a specific server ID (from !speedservers).
        !speedtestid <server_id> [threads=4]
        """
        if not await self._check(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "🌐 Speed Test Running…",
            f"⏳ Using server ID **{server_id}** — measuring throughput…",
            color=Config.COLOR_MONITOR,
        ))
        self._running = True
        try:
            result = await run_in_executor(_run_full_test, server_id, max(1, min(threads, 16)))
        except Exception as exc:
            await msg.edit(embed=build_embed("❌ Failed", f"```{exc}```", color=Config.COLOR_ERROR))
            return
        finally:
            self._running = False

        self._history.append(result)
        if len(self._history) > self.MAX_HISTORY:
            self._history.pop(0)

        await msg.edit(embed=build_embed(
            "🌐 Speed Test Complete",
            f"Server: **{result.server_name}**\nISP: {result.isp}",
            color=Config.COLOR_SUCCESS,
            fields=[
                ("📥 Download", f"**{result.download:.1f} Mbps**", True),
                ("📤 Upload",   f"**{result.upload:.1f} Mbps**",   True),
                ("🏓 Ping",     f"**{result.ping:.1f} ms**",        True),
            ],
        ))


async def setup(bot):
    await bot.add_cog(Bandwidth(bot))
