"""
perf_counters.py — Windows Performance Counters cog.

Shells out to the compiled C++ binary  native/perf_counters.exe
which reads raw PDH counters directly from the Windows kernel.

Build the binary first:
    cd native
    cl /EHsc /O2 /W4 perf_counters.cpp /link pdh.lib /out:perf_counters.exe
  or (MinGW):
    g++ -std=c++17 -O2 -o perf_counters.exe perf_counters.cpp -lpdh

The cog also provides:
  - Auto-compile helper command (!perfbuild) — calls MSVC or g++ automatically
  - Live polling dashboard (updates embed in-place)
  - JSON export of a snapshot
  - Threshold alerting for any counter
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.perf_counters")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_NATIVE_DIR  = Path(__file__).parent.parent / "native"
_EXE_PATH    = _NATIVE_DIR / "perf_counters.exe"
_SRC_PATH    = _NATIVE_DIR / "perf_counters.cpp"


# ---------------------------------------------------------------------------
# Binary invocation helpers
# ---------------------------------------------------------------------------

def _invoke_binary(json_mode: bool = True, count: int = 1, interval_ms: int = 800) -> str:
    """
    Call the C++ binary and return its stdout.
    Raises RuntimeError if the binary is missing or exits non-zero.
    """
    if not _EXE_PATH.exists():
        raise RuntimeError(
            f"Binary not found: {_EXE_PATH}\n"
            "Run `!perfbuild` to compile it, or build manually:\n"
            "```\ncd native\n"
            "cl /EHsc /O2 perf_counters.cpp /link pdh.lib /out:perf_counters.exe\n```"
        )

    cmd = [str(_EXE_PATH)]
    if json_mode:
        cmd.append("--json")
    cmd += ["--count", str(count), "--interval", str(interval_ms)]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in (0,):
        raise RuntimeError(
            f"perf_counters.exe exited {result.returncode}\n"
            f"stderr: {result.stderr[:400]}"
        )
    return result.stdout.strip()


def _parse_snapshot(raw: str) -> Dict[str, float]:
    """Parse JSON output from perf_counters.exe into {label: value}."""
    data = json.loads(raw)
    counters = data.get("counters", {})
    # Replace None (null) with -1.0 for consistency
    return {k: (v if v is not None else -1.0) for k, v in counters.items()}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(v: float, suffix: str = "/s") -> str:
    if v < 0:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024:
            return f"{v:.1f} {unit}{suffix}"
        v /= 1024
    return f"{v:.1f} PB{suffix}"


def _fmt_pct(v: float) -> str:
    return "N/A" if v < 0 else f"{v:.1f}%"


def _fmt_count(v: float) -> str:
    return "N/A" if v < 0 else f"{v:,.0f}"


def _build_dashboard_embed(snap: Dict[str, float]) -> discord.Embed:
    """Turn a snapshot dict into a nicely formatted embed."""

    cpu   = _fmt_pct(snap.get("cpu_total_pct", -1))
    cpuu  = _fmt_pct(snap.get("cpu_user_pct", -1))
    cpup  = _fmt_pct(snap.get("cpu_privileged_pct", -1))
    irq   = _fmt_pct(snap.get("cpu_interrupt_pct", -1))
    dpc   = _fmt_pct(snap.get("cpu_dpc_pct", -1))
    ints  = _fmt_count(snap.get("interrupts_per_sec", -1))
    ctxsw = _fmt_count(snap.get("context_switches_sec", -1))

    avail  = _fmt_bytes(snap.get("mem_available_bytes",    -1), "")
    commit = _fmt_bytes(snap.get("mem_committed_bytes",    -1), "")
    limit  = _fmt_bytes(snap.get("mem_commit_limit",       -1), "")
    pfaults= _fmt_count(snap.get("mem_page_faults_sec",   -1))
    pgsin  = _fmt_count(snap.get("mem_pages_input_sec",   -1))
    pgsout = _fmt_count(snap.get("mem_pages_output_sec",  -1))
    pool_p = _fmt_bytes(snap.get("mem_pool_paged_bytes",  -1), "")
    pool_np= _fmt_bytes(snap.get("mem_pool_nonpaged_bytes",-1),"")

    dr  = _fmt_bytes(snap.get("disk_read_bytes_sec",  -1))
    dw  = _fmt_bytes(snap.get("disk_write_bytes_sec", -1))
    dt  = _fmt_pct(snap.get("disk_time_pct",          -1))
    dql = snap.get("disk_queue_length", -1)
    dql_s = "N/A" if dql < 0 else f"{dql:.2f}"

    nr = _fmt_bytes(snap.get("net_bytes_recv_sec", -1))
    ns = _fmt_bytes(snap.get("net_bytes_sent_sec", -1))

    procs   = _fmt_count(snap.get("system_processes",    -1))
    threads = _fmt_count(snap.get("system_threads",      -1))
    handles = _fmt_count(snap.get("system_handle_count", -1))
    syscalls= _fmt_count(snap.get("system_calls_sec",    -1))

    fields = [
        ("🖥️ CPU",
         f"Total: **{cpu}** | User: {cpuu} | Kernel: {cpup}\n"
         f"IRQ: {irq} | DPC: {dpc} | Interrupts/s: {ints} | Ctx-Switch/s: {ctxsw}",
         False),

        ("🧠 Memory",
         f"Available: **{avail}** | Committed: {commit} / {limit}\n"
         f"Page Faults/s: {pfaults} | Pages In: {pgsin} | Pages Out: {pgsout}\n"
         f"Pool Paged: {pool_p} | Pool NonPaged: {pool_np}",
         False),

        ("💾 Disk (Total)",
         f"Read: **{dr}** | Write: **{dw}**\n"
         f"Disk Time: {dt} | Queue Length: {dql_s}",
         False),

        ("🌐 Network",
         f"Recv: **{nr}** | Sent: **{ns}**",
         True),

        ("⚙️ System",
         f"Processes: {procs} | Threads: {threads}\n"
         f"Handles: {handles} | SysCalls/s: {syscalls}",
         True),
    ]

    return build_embed(
        "📊 Windows Performance Counters (Raw PDH)",
        f"Sampled at `{time.strftime('%H:%M:%S')}`  |  via `perf_counters.exe`",
        color=Config.COLOR_MONITOR,
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Auto-build helper
# ---------------------------------------------------------------------------

def _try_compile() -> Tuple[bool, str]:
    """
    Attempt to compile perf_counters.cpp.
    Tries MSVC (cl.exe) first, then MinGW (g++).
    Returns (success, message).
    """
    src = str(_SRC_PATH)
    out = str(_EXE_PATH)

    # Try MSVC
    for compiler in ("cl", "cl.exe"):
        try:
            r = subprocess.run(
                [compiler, "/EHsc", "/O2", "/W4", src,
                 "/link", "pdh.lib", f"/out:{out}"],
                capture_output=True, text=True, timeout=60,
                cwd=str(_NATIVE_DIR),
            )
            if r.returncode == 0 and _EXE_PATH.exists():
                return True, f"Compiled with MSVC.\n```{r.stdout[-400:]}```"
            else:
                msvc_err = r.stderr[-400:]
        except FileNotFoundError:
            msvc_err = "cl.exe not found"

    # Try MinGW g++
    try:
        r = subprocess.run(
            ["g++", "-std=c++17", "-O2", "-o", out, src, "-lpdh"],
            capture_output=True, text=True, timeout=60,
            cwd=str(_NATIVE_DIR),
        )
        if r.returncode == 0 and _EXE_PATH.exists():
            return True, f"Compiled with g++.\n```{r.stdout[-400:]}```"
        return False, f"g++ failed:\n```{r.stderr[-600:]}```"
    except FileNotFoundError:
        pass

    return False, (
        "Neither `cl.exe` (MSVC) nor `g++` (MinGW) found.\n\n"
        "Install one of:\n"
        "• **Visual Studio Build Tools** (includes cl.exe + pdh.lib)\n"
        "• **MinGW-w64** (includes g++ + -lpdh)\n\n"
        f"MSVC error: `{msvc_err}`"
    )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class PerfCounters(commands.Cog):
    """Raw Windows Performance Counters via native PDH C++ binary."""

    def __init__(self, bot):
        self.bot = bot
        self._alert_thresholds: Dict[str, float] = {}  # counter_label -> threshold
        self._alert_channel_id: Optional[int] = None
        self._last_snapshot: Dict[str, float] = {}

    # ── Guard ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_windows() -> bool:
        return platform.system() == "Windows"

    async def _guard(self, ctx) -> bool:
        if not self._is_windows():
            await ctx.send(embed=build_embed(
                "❌ Windows Only",
                "Windows Performance Counters (PDH) are only available on Windows.",
                color=Config.COLOR_ERROR,
            ))
            return False
        return True

    # ── Commands ───────────────────────────────────────────────────────────

    @commands.command(name="perf", aliases=["pdh", "winperf", "perfcounters"])
    async def perf_snapshot(self, ctx):
        """Show a full Windows Performance Counter dashboard (raw PDH)."""
        if not await self._guard(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "📊 Sampling Counters…",
            "⏳ Querying PDH (takes ~1 s for rate counters)…",
            color=Config.COLOR_MONITOR,
        ))
        try:
            raw = await run_in_executor(_invoke_binary, True, 1, 900)
            snap = _parse_snapshot(raw)
            self._last_snapshot = snap
            await msg.edit(embed=_build_dashboard_embed(snap))
        except Exception as exc:
            logger.error(f"perf snapshot failed: {exc}")
            await msg.edit(embed=build_embed(
                "❌ PDH Error", truncate(str(exc), 1500), color=Config.COLOR_ERROR
            ))

    @commands.command(name="perflive", aliases=["pdhlive", "winperflive"])
    async def perf_live(self, ctx, duration: int = 60, interval: int = 5):
        """
        Live-updating PDH dashboard for <duration> seconds.
        !perflive [duration=60] [interval=5]
        """
        if not await self._guard(ctx):
            return
        if interval < 3:
            interval = 3
        if duration > 300:
            duration = 300

        msg = await ctx.send(embed=build_embed(
            "📊 PDH Live Dashboard", "Starting…", color=Config.COLOR_MONITOR
        ))

        steps = duration // interval
        for i in range(steps):
            try:
                raw = await run_in_executor(_invoke_binary, True, 1, max(interval * 800, 900))
                snap = _parse_snapshot(raw)
                self._last_snapshot = snap
                await self._check_alert_thresholds(snap)
                await msg.edit(embed=_build_dashboard_embed(snap))
                await asyncio.sleep(interval)
            except discord.HTTPException:
                break
            except Exception as exc:
                logger.error(f"perflive step error: {exc}")
                break

        await msg.edit(embed=build_embed(
            "📊 PDH Live Dashboard", "✅ Session ended.", color=Config.COLOR_INFO
        ))

    @commands.command(name="perfcounter", aliases=["pdhcounter", "rawcounter"])
    async def perf_one_counter(self, ctx, *, label: str):
        """
        Query a single counter by label name.
        !perfcounter cpu_total_pct
        Use !perflist to see all available labels.
        """
        if not await self._guard(ctx):
            return
        try:
            raw = await run_in_executor(_invoke_binary, True, 1, 900)
            snap = _parse_snapshot(raw)
        except Exception as exc:
            await ctx.send(embed=build_embed("❌ PDH Error", str(exc), color=Config.COLOR_ERROR))
            return

        label = label.strip().lower()
        if label not in snap:
            # Fuzzy match
            matches = [k for k in snap if label in k]
            if not matches:
                await ctx.send(embed=build_embed(
                    "❌ Counter Not Found",
                    f"`{label}` not found. Use `!perflist` to see all labels.",
                    color=Config.COLOR_ERROR,
                ))
                return
            label = matches[0]

        value = snap[label]
        val_str = "N/A" if value < 0 else f"{value:.4f}"
        await ctx.send(embed=build_embed(
            "📊 Raw PDH Counter",
            f"**Label:** `{label}`\n**Value:** `{val_str}`",
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="perflist", aliases=["pdhlist", "counterlist"])
    async def perf_list(self, ctx):
        """List all available PDH counter labels."""
        try:
            raw = await run_in_executor(_invoke_binary, True, 1, 900)
            snap = _parse_snapshot(raw)
        except Exception as exc:
            await ctx.send(embed=build_embed("❌ PDH Error", str(exc), color=Config.COLOR_ERROR))
            return

        lines = [f"`{k}` = `{v:.4f}`" if v >= 0 else f"`{k}` = N/A" for k, v in sorted(snap.items())]
        chunks = [lines[i:i+20] for i in range(0, len(lines), 20)]
        for idx, chunk in enumerate(chunks[:3]):
            title = "📋 PDH Counter Labels" if idx == 0 else f"📋 PDH Counters (cont. {idx+1})"
            await ctx.send(embed=build_embed(title, "\n".join(chunk), color=Config.COLOR_INFO))

    @commands.command(name="perfexport", aliases=["pdhexport"])
    async def perf_export(self, ctx, count: int = 5, interval: int = 2):
        """
        Collect <count> PDH snapshots, <interval> seconds apart, export as JSON.
        !perfexport [count=5] [interval=2]
        """
        if not await self._guard(ctx):
            return
        if count < 1:  count = 1
        if count > 30: count = 30
        if interval < 1: interval = 1

        msg = await ctx.send(embed=build_embed(
            "📦 PDH Export",
            f"⏳ Collecting {count} samples × {interval}s…",
            color=Config.COLOR_MONITOR,
        ))
        async with ctx.typing():
            samples = []
            for i in range(count):
                try:
                    raw = await run_in_executor(_invoke_binary, True, 1, max(interval * 800, 900))
                    snap = _parse_snapshot(raw)
                    snap["_timestamp"] = time.time()
                    samples.append(snap)
                except Exception as exc:
                    samples.append({"_error": str(exc), "_timestamp": time.time()})
                if i + 1 < count:
                    await asyncio.sleep(interval)

            buf = io.BytesIO(json.dumps(samples, indent=2).encode())

        await msg.edit(embed=build_embed(
            "📦 PDH Export Complete",
            f"✅ {len(samples)} samples captured.",
            color=Config.COLOR_SUCCESS,
        ))
        await ctx.send(file=discord.File(buf, "pdh_export.json"))

    @commands.command(name="perfalert", aliases=["pdhalert"])
    @admin_only()
    async def perf_set_alert(self, ctx, label: str, threshold: float):
        """
        Set an alert when a PDH counter exceeds a threshold.
        !perfalert cpu_total_pct 85
        Alerts will fire in the channel where !perflive is running.
        """
        self._alert_thresholds[label.lower()] = threshold
        self._alert_channel_id = ctx.channel.id
        await ctx.send(embed=build_embed(
            "🔔 Alert Set",
            f"Will alert when `{label}` exceeds **{threshold}**.",
            color=Config.COLOR_SUCCESS,
        ))

    @commands.command(name="perfalertclear", aliases=["pdhclearalert"])
    @admin_only()
    async def perf_clear_alerts(self, ctx):
        """Clear all PDH counter alerts."""
        self._alert_thresholds.clear()
        await ctx.send(embed=build_embed(
            "🔔 Alerts Cleared", "All PDH alerts removed.", color=Config.COLOR_INFO
        ))

    @commands.command(name="perfbuild", aliases=["buildperf", "compileperfcounters"])
    @admin_only()
    async def perf_build(self, ctx):
        """
        Auto-compile native/perf_counters.cpp → native/perf_counters.exe.
        Requires MSVC (cl.exe) or MinGW (g++).
        """
        if not self._is_windows():
            await ctx.send("❌ Can only compile on Windows.")
            return

        msg = await ctx.send(embed=build_embed(
            "🔨 Compiling perf_counters.cpp…",
            "⏳ Trying MSVC then g++…",
            color=Config.COLOR_MONITOR,
        ))
        async with ctx.typing():
            ok, output = await run_in_executor(_try_compile)

        color = Config.COLOR_SUCCESS if ok else Config.COLOR_ERROR
        title = "✅ Compiled Successfully" if ok else "❌ Compilation Failed"
        await msg.edit(embed=build_embed(title, truncate(output, 1800), color=color))

    # ── Internal alert check ───────────────────────────────────────────────

    async def _check_alert_thresholds(self, snap: Dict[str, float]):
        if not self._alert_thresholds or not self._alert_channel_id:
            return
        channel = self.bot.get_channel(self._alert_channel_id)
        if not channel:
            return
        for label, threshold in self._alert_thresholds.items():
            val = snap.get(label, -1)
            if val >= 0 and val > threshold:
                try:
                    await channel.send(embed=build_embed(
                        "⚠️ PDH Counter Alert",
                        f"`{label}` = **{val:.4f}** (threshold: {threshold})",
                        color=Config.COLOR_ERROR,
                    ))
                except Exception:
                    pass


async def setup(bot):
    await bot.add_cog(PerfCounters(bot))
