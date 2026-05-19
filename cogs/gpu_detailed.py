"""
gpu_detailed.py — Deep NVIDIA GPU monitoring via nvidia-ml-py3 (pynvml).

Features:
  - Per-GPU full telemetry: clocks, memory, power, thermals, PCIe bandwidth
  - Multi-GPU support
  - Live polling loop (updates an embed in-place)
  - ECC error tracking
  - GPU process table (which PID owns how much VRAM)
  - Historical chart (integrates with monitoring cog history)
  - nvlink / p2p topology (multi-GPU rigs)

Requires:
    pip install nvidia-ml-py3
"""

from __future__ import annotations

import asyncio
import io
import time
from typing import List, Optional

import discord
from discord.ext import commands, tasks

from utils.config import Config
from utils.helpers import build_embed, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.gpu_detailed")

# ---------------------------------------------------------------------------
# Optional pynvml import — init deferred to first use so driver has time to load
# ---------------------------------------------------------------------------
try:
    import pynvml  # nvidia-ml-py3
    _NVML_IMPORTED = True
except ImportError:
    _NVML_IMPORTED = False

_NVML_AVAILABLE = False
_GPU_COUNT = 0

_NVML_INIT_ATTEMPTED = False


def _ensure_nvml() -> bool:
    global _NVML_AVAILABLE, _GPU_COUNT, _NVML_INIT_ATTEMPTED
    if _NVML_AVAILABLE:
        return True
    if not _NVML_IMPORTED:
        return False
    # Only attempt (and log) once to avoid repeated warnings on every command call
    if _NVML_INIT_ATTEMPTED:
        return False
    _NVML_INIT_ATTEMPTED = True
    try:
        pynvml.nvmlInit()
        _NVML_AVAILABLE = True
        _GPU_COUNT = pynvml.nvmlDeviceGetCount()
        logger.info(f"pynvml initialised — {_GPU_COUNT} GPU(s) detected")
        return True
    except pynvml.NVMLError_DriverNotLoaded:
        logger.debug(
            "pynvml init skipped: NVIDIA driver not loaded "
            "(no NVIDIA GPU present or driver not installed — this is normal on CPU-only machines)"
        )
        return False
    except Exception as e:
        err = str(e)
        if "NVML Shared Library Not Found" in err or "libnvidia-ml" in err.lower():
            logger.debug(
                "pynvml init skipped: NVML shared library not found "
                "(no NVIDIA GPU / driver — this is normal on CPU-only machines)"
            )
        else:
            logger.debug(f"pynvml init skipped: {e}")
        return False


# ---------------------------------------------------------------------------
# Pure-Python helper functions (run in executor so they never block the loop)
# ---------------------------------------------------------------------------

def _bytes_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MiB"

def _khz_mhz(khz: int) -> str:
    return f"{khz / 1000:.0f} MHz"


def _get_gpu_handle(index: int):
    return pynvml.nvmlDeviceGetHandleByIndex(index)


def _full_snapshot(index: int) -> dict:
    """Collect every available metric for one GPU. Called in executor."""
    h = _get_gpu_handle(index)

    def _safe(fn, *args, default="N/A"):
        try:
            return fn(*args)
        except pynvml.NVMLError:
            return default

    name        = _safe(pynvml.nvmlDeviceGetName, h)
    uuid        = _safe(pynvml.nvmlDeviceGetUUID, h)
    serial      = _safe(pynvml.nvmlDeviceGetSerial, h)
    pci         = _safe(pynvml.nvmlDeviceGetPciInfo, h)
    driver      = _safe(pynvml.nvmlSystemGetDriverVersion)
    nvml_ver    = _safe(pynvml.nvmlSystemGetNVMLVersion)

    # Utilisation
    util = _safe(pynvml.nvmlDeviceGetUtilizationRates, h)
    gpu_util   = util.gpu  if util != "N/A" else "N/A"
    mem_util   = util.memory if util != "N/A" else "N/A"

    # Encoder / decoder
    enc = _safe(pynvml.nvmlDeviceGetEncoderUtilization, h)
    dec = _safe(pynvml.nvmlDeviceGetDecoderUtilization, h)
    enc_util = enc[0] if enc != "N/A" else "N/A"
    dec_util = dec[0] if dec != "N/A" else "N/A"

    # Memory
    mem = _safe(pynvml.nvmlDeviceGetMemoryInfo, h)
    mem_total = mem.total if mem != "N/A" else 0
    mem_used  = mem.used  if mem != "N/A" else 0
    mem_free  = mem.free  if mem != "N/A" else 0

    # Temperature
    _TEMP_GPU = getattr(pynvml, "NVML_TEMPERATURE_GPU", 0)
    _CLK_GFX  = getattr(pynvml, "NVML_CLOCK_GRAPHICS", 0)
    _CLK_SM   = getattr(pynvml, "NVML_CLOCK_SM", 1)
    _CLK_MEM  = getattr(pynvml, "NVML_CLOCK_MEM", 2)
    _CLK_VID  = getattr(pynvml, "NVML_CLOCK_VIDEO", 3)
    _PCIE_TX  = getattr(pynvml, "NVML_PCIE_UTIL_TX_BYTES", 0)
    _PCIE_RX  = getattr(pynvml, "NVML_PCIE_UTIL_RX_BYTES", 1)
    _ECC_CORR = getattr(pynvml, "NVML_MEMORY_ERROR_TYPE_CORRECTED", 0)
    _ECC_UNCR = getattr(pynvml, "NVML_MEMORY_ERROR_TYPE_UNCORRECTED", 1)
    _ECC_AGG  = getattr(pynvml, "NVML_AGGREGATE_ECC", 1)

    temp_gpu  = _safe(pynvml.nvmlDeviceGetTemperature, h, _TEMP_GPU)
    temp_mem  = _safe(pynvml.nvmlDeviceGetTemperature, h, 1)  # memory junction (Ampere+)

    # Clocks
    clk_gr   = _safe(pynvml.nvmlDeviceGetClockInfo, h, _CLK_GFX)
    clk_sm   = _safe(pynvml.nvmlDeviceGetClockInfo, h, _CLK_SM)
    clk_mem  = _safe(pynvml.nvmlDeviceGetClockInfo, h, _CLK_MEM)
    clk_vid  = _safe(pynvml.nvmlDeviceGetClockInfo, h, _CLK_VID)
    max_gr   = _safe(pynvml.nvmlDeviceGetMaxClockInfo, h, _CLK_GFX)
    max_mem  = _safe(pynvml.nvmlDeviceGetMaxClockInfo, h, _CLK_MEM)

    # Power
    pwr_usage = _safe(pynvml.nvmlDeviceGetPowerUsage, h)           # mW
    pwr_limit = _safe(pynvml.nvmlDeviceGetEnforcedPowerLimit, h)   # mW
    pwr_mgmt  = _safe(pynvml.nvmlDeviceGetPowerManagementLimit, h) # mW
    pwr_state = _safe(pynvml.nvmlDeviceGetPowerState, h)

    # Fan
    fans: list = []
    if hasattr(pynvml, "nvmlDeviceGetNumFans"):
        fan_count = _safe(pynvml.nvmlDeviceGetNumFans, h, default=0)
        if fan_count and fan_count != "N/A":
            for fi in range(fan_count):
                _fn = getattr(pynvml, "nvmlDeviceGetFanSpeed_v2", None)
                spd = _safe(_fn, h, fi) if _fn else "N/A"
                fans.append(spd)
    if not fans:
        fans = [_safe(pynvml.nvmlDeviceGetFanSpeed, h)]

    # PCIe bandwidth
    pcie_tx = _safe(pynvml.nvmlDeviceGetPcieThroughput, h, _PCIE_TX)
    pcie_rx = _safe(pynvml.nvmlDeviceGetPcieThroughput, h, _PCIE_RX)
    pcie_gen = _safe(pynvml.nvmlDeviceGetCurrPcieLinkGeneration, h)
    pcie_wid = _safe(pynvml.nvmlDeviceGetCurrPcieLinkWidth, h)

    # ECC
    ecc_mode = _safe(pynvml.nvmlDeviceGetEccMode, h)
    ecc_sbe  = _safe(pynvml.nvmlDeviceGetTotalEccErrors, h,
                     _ECC_CORR,
                     _ECC_AGG)
    ecc_dbe  = _safe(pynvml.nvmlDeviceGetTotalEccErrors, h,
                     _ECC_UNCR,
                     _ECC_AGG)

    # Performance state
    perf_state = _safe(pynvml.nvmlDeviceGetPerformanceState, h)

    # Throttle reasons
    throttle = _safe(pynvml.nvmlDeviceGetCurrentClocksThrottleReasons, h)

    # Running processes
    procs = []
    try:
        raw_procs = pynvml.nvmlDeviceGetComputeRunningProcesses(h)
        raw_procs += pynvml.nvmlDeviceGetGraphicsRunningProcesses(h)
        seen_pids: set = set()
        for p in raw_procs:
            if p.pid not in seen_pids:
                seen_pids.add(p.pid)
                try:
                    pname = pynvml.nvmlSystemGetProcessName(p.pid).decode()
                except Exception:
                    pname = f"PID {p.pid}"
                procs.append({
                    "pid":  p.pid,
                    "name": pname,
                    "vram": p.usedGpuMemory,
                })
    except pynvml.NVMLError:
        pass

    return {
        "index":     index,
        "name":      name.decode() if isinstance(name, bytes) else str(name),
        "uuid":      uuid.decode() if isinstance(uuid, bytes) else str(uuid),
        "serial":    serial.decode() if isinstance(serial, bytes) else str(serial),
        "driver":    driver.decode() if isinstance(driver, bytes) else str(driver),
        "nvml_ver":  nvml_ver.decode() if isinstance(nvml_ver, bytes) else str(nvml_ver),
        "gpu_util":  gpu_util,
        "mem_util":  mem_util,
        "enc_util":  enc_util,
        "dec_util":  dec_util,
        "mem_total": mem_total,
        "mem_used":  mem_used,
        "mem_free":  mem_free,
        "temp_gpu":  temp_gpu,
        "temp_mem":  temp_mem,
        "clk_gr":    clk_gr,
        "clk_sm":    clk_sm,
        "clk_mem":   clk_mem,
        "clk_vid":   clk_vid,
        "max_gr":    max_gr,
        "max_mem":   max_mem,
        "pwr_usage": pwr_usage,
        "pwr_limit": pwr_limit,
        "pwr_mgmt":  pwr_mgmt,
        "pwr_state": pwr_state,
        "fans":      fans,
        "pcie_tx":   pcie_tx,
        "pcie_rx":   pcie_rx,
        "pcie_gen":  pcie_gen,
        "pcie_wid":  pcie_wid,
        "ecc_mode":  ecc_mode,
        "ecc_sbe":   ecc_sbe,
        "ecc_dbe":   ecc_dbe,
        "perf_state": perf_state,
        "throttle":  throttle,
        "procs":     procs,
    }


def _throttle_reason_str(mask) -> str:
    """Decode NVML clock throttle bitmask into human-readable reasons."""
    if mask == "N/A" or mask == 0:
        return "None"
    reasons = []
    _map = {
        0x0000000000000001: "GPU Idle",
        0x0000000000000002: "App Clock Setting",
        0x0000000000000004: "SW Power Cap",
        0x0000000000000008: "HW Slowdown",
        0x0000000000000010: "Sync Boost",
        0x0000000000000020: "SW Thermal",
        0x0000000000000040: "HW Thermal",
        0x0000000000000080: "HW Power Brake",
        0x0000000000000200: "Display Clk Setting",
    }
    for bit, label in _map.items():
        if mask & bit:
            reasons.append(label)
    return ", ".join(reasons) if reasons else f"0x{mask:016x}"


def _fmt_mw(mw) -> str:
    if isinstance(mw, (int, float)):
        return f"{mw/1000:.1f} W"
    return str(mw)


def _fmt_mhz(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v} MHz"
    return str(v)


def _snapshot_to_embed(s: dict) -> discord.Embed:
    """Convert a GPU snapshot dict → rich Discord embed."""
    fan_str = ", ".join(
        f"{f}%" if isinstance(f, (int, float)) else str(f)
        for f in s["fans"]
    ) or "N/A"

    mem_pct = (s["mem_used"] / s["mem_total"] * 100) if s["mem_total"] else 0
    mem_bar = "█" * int(mem_pct / 10) + "░" * (10 - int(mem_pct / 10))

    ecc_str = "N/A"
    if s["ecc_mode"] not in ("N/A",):
        curr, _ = s["ecc_mode"]
        ecc_str = f"{'Enabled' if curr else 'Disabled'} | SBE: {s['ecc_sbe']} | DBE: {s['ecc_dbe']}"

    pcie_str = (
        f"Gen {s['pcie_gen']} x{s['pcie_wid']}  "
        f"TX: {s['pcie_tx']//1024} MiB/s  RX: {s['pcie_rx']//1024} MiB/s"
        if isinstance(s["pcie_gen"], int) else "N/A"
    )

    fields = [
        ("🎮 GPU", s["name"], False),
        ("📊 Utilisation",
         f"GPU: **{s['gpu_util']}%** | MEM: {s['mem_util']}% | "
         f"ENC: {s['enc_util']}% | DEC: {s['dec_util']}%", False),
        ("💾 VRAM",
         f"`{mem_bar}` {mem_pct:.1f}%\n"
         f"Used: {_bytes_mb(s['mem_used'])} / Total: {_bytes_mb(s['mem_total'])} "
         f"(Free: {_bytes_mb(s['mem_free'])})", False),
        ("🌡️ Temperature",
         f"GPU Core: **{s['temp_gpu']}°C** | Memory: {s['temp_mem']}°C", True),
        ("🌀 Fan(s)", fan_str, True),
        ("⚡ Power",
         f"{_fmt_mw(s['pwr_usage'])} / {_fmt_mw(s['pwr_limit'])}  (P{s['pwr_state']})", True),
        ("⏱️ Clocks",
         f"Core: {_fmt_mhz(s['clk_gr'])} (max {_fmt_mhz(s['max_gr'])})  "
         f"SM: {_fmt_mhz(s['clk_sm'])}  "
         f"MEM: {_fmt_mhz(s['clk_mem'])} (max {_fmt_mhz(s['max_mem'])})  "
         f"VID: {_fmt_mhz(s['clk_vid'])}", False),
        ("🚦 Perf State", f"P{s['perf_state']}", True),
        ("🛑 Throttle", _throttle_reason_str(s["throttle"]), True),
        ("🔗 PCIe", pcie_str, False),
        ("🛡️ ECC", ecc_str, False),
    ]

    embed = build_embed(
        f"🎮 GPU {s['index']} — Detailed Monitor",
        f"UUID: `{s['uuid']}`\nDriver: {s['driver']}  |  NVML: {s['nvml_ver']}",
        color=Config.COLOR_MONITOR,
        fields=fields,
    )
    return embed


class GPUDetailed(commands.Cog):
    """Deep NVIDIA GPU monitoring via pynvml (nvidia-ml-py3)."""

    def __init__(self, bot):
        self.bot = bot
        # History buffer for each GPU  {gpu_idx: [util_pct, ...]}
        self._util_hist: dict[int, list] = {i: [] for i in range(_GPU_COUNT)}
        self._hist_max = 120  # 2 minutes @ 1 s

    # ──────────────────────────────────────────────────────────────────────
    # Guard helper
    # ──────────────────────────────────────────────────────────────────────

    async def _check_nvml(self, ctx) -> bool:
        _ensure_nvml()  # lazy init on first command call
        if not _NVML_AVAILABLE:
            await ctx.send(embed=build_embed(
                "❌ NVIDIA NVML Unavailable",
                "Install `nvidia-ml-py3` **and** NVIDIA drivers, then restart the bot.\n"
                "```pip install nvidia-ml-py3```",
                color=Config.COLOR_ERROR,
            ))
            return False
        if _GPU_COUNT == 0:
            await ctx.send(embed=build_embed(
                "❌ No NVIDIA GPU Detected",
                "No NVIDIA GPU found by NVML.",
                color=Config.COLOR_ERROR,
            ))
            return False
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Commands
    # ──────────────────────────────────────────────────────────────────────

    @commands.command(name="gpudetail", aliases=["gpud", "nvml"])
    async def gpu_detail(self, ctx, index: int = 0):
        """Full NVIDIA GPU telemetry via pynvml. !gpudetail [gpu_index]"""
        if not await self._check_nvml(ctx):
            return
        if index >= _GPU_COUNT:
            await ctx.send(f"❌ GPU index {index} out of range (0–{_GPU_COUNT-1})")
            return
        async with ctx.typing():
            snapshot = await run_in_executor(_full_snapshot, index)
        embed = _snapshot_to_embed(snapshot)
        await ctx.send(embed=embed)

    @commands.command(name="gpuprocs", aliases=["gpuprocesses", "gpupids"])
    async def gpu_procs(self, ctx, index: int = 0):
        """Show processes using VRAM on a GPU. !gpuprocs [gpu_index]"""
        if not await self._check_nvml(ctx):
            return
        async with ctx.typing():
            snapshot = await run_in_executor(_full_snapshot, index)

        procs = snapshot["procs"]
        if not procs:
            desc = "No processes currently using this GPU."
        else:
            lines = [f"`{p['pid']:>6}` **{p['name'][:40]}**  —  {_bytes_mb(p['vram'])}" for p in procs]
            desc = "\n".join(lines)

        await ctx.send(embed=build_embed(
            f"🎮 GPU {index} — VRAM Consumers",
            truncate(desc, 1800),
            color=Config.COLOR_SYSTEM,
        ))

    @commands.command(name="gpulive", aliases=["gpuwatch2", "gpupoll"])
    async def gpu_live(self, ctx, index: int = 0, duration: int = 60, interval: int = 3):
        """
        Live-updating GPU embed for <duration> seconds, refresh every <interval> s.
        !gpulive [gpu_index=0] [duration=60] [interval=3]
        """
        if not await self._check_nvml(ctx):
            return
        if interval < 2:
            interval = 2
        if duration > 300:
            duration = 300  # cap at 5 minutes

        msg = await ctx.send(embed=build_embed(
            "🎮 GPU Live Monitor", "Starting…", color=Config.COLOR_MONITOR
        ))

        steps = duration // interval
        for _ in range(steps):
            try:
                snap = await run_in_executor(_full_snapshot, index)
                # Update history
                util = snap["gpu_util"]
                if isinstance(util, int):
                    self._util_hist[index].append(util)
                    if len(self._util_hist[index]) > self._hist_max:
                        self._util_hist[index].pop(0)
                await msg.edit(embed=_snapshot_to_embed(snap))
                await asyncio.sleep(interval)
            except discord.HTTPException:
                break
            except Exception as exc:
                logger.error(f"gpulive error: {exc}")
                break

        await msg.edit(embed=build_embed(
            "🎮 GPU Live Monitor", "✅ Session ended.", color=Config.COLOR_INFO
        ))

    @commands.command(name="gpucount", aliases=["gpulist", "gpus"])
    async def gpu_count(self, ctx):
        """List all detected NVIDIA GPUs."""
        if not await self._check_nvml(ctx):
            return

        def _list():
            results = []
            for i in range(_GPU_COUNT):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(h)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                results.append((
                    i,
                    name.decode() if isinstance(name, bytes) else name,
                    mem.total,
                ))
            return results

        gpus = await run_in_executor(_list)
        lines = [f"**GPU {i}**: {nm}  ({_bytes_mb(tot)})" for i, nm, tot in gpus]
        await ctx.send(embed=build_embed(
            f"🎮 {_GPU_COUNT} NVIDIA GPU(s) Detected",
            "\n".join(lines),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="gpueccstatus", aliases=["gpuecc"])
    async def gpu_ecc_status(self, ctx, index: int = 0):
        """Show ECC error counts for a GPU. !gpueccstatus [gpu_index]"""
        if not await self._check_nvml(ctx):
            return
        async with ctx.typing():
            snap = await run_in_executor(_full_snapshot, index)

        ecc = snap["ecc_mode"]
        if ecc == "N/A":
            desc = "ECC not supported on this GPU."
        else:
            curr, pend = ecc
            desc = (
                f"**ECC Mode (current):** {'Enabled' if curr else 'Disabled'}\n"
                f"**ECC Mode (pending):** {'Enabled' if pend else 'Disabled'}\n"
                f"**Single-Bit Errors (SBE):** {snap['ecc_sbe']}\n"
                f"**Double-Bit Errors (DBE):** {snap['ecc_dbe']}"
            )
        await ctx.send(embed=build_embed(
            f"🛡️ GPU {index} — ECC Status", desc, color=Config.COLOR_SYSTEM
        ))

    @commands.command(name="gpuhistchart", aliases=["gpuhist", "gpuchart"])
    async def gpu_hist_chart(self, ctx, index: int = 0):
        """Plot GPU utilisation history (requires monitoring via !gpulive). !gpuhistchart [gpu_index]"""
        hist = self._util_hist.get(index, [])
        if len(hist) < 2:
            await ctx.send(embed=build_embed(
                "📊 GPU History",
                "Not enough data yet. Run `!gpulive` first to collect samples.",
                color=Config.COLOR_WARNING,
            ))
            return

        def _plot():
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(10, 3), dpi=90)
            ax.plot(hist, color="#00c896", linewidth=1.5, label="GPU Util %")
            ax.fill_between(range(len(hist)), hist, alpha=0.25, color="#00c896")
            ax.set_ylim(0, 100)
            ax.set_xlabel("Sample")
            ax.set_ylabel("GPU Util (%)")
            ax.set_title(f"GPU {index} Utilisation History")
            ax.legend(loc="upper right")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            return buf

        async with ctx.typing():
            buf = await run_in_executor(_plot)

        await ctx.send(
            embed=build_embed("📊 GPU Utilisation History", color=Config.COLOR_MONITOR),
            file=discord.File(buf, f"gpu{index}_history.png"),
        )


async def setup(bot):
    await bot.add_cog(GPUDetailed(bot))
