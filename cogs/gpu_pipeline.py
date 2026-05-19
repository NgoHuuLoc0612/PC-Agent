"""
gpu_pipeline.py — GPU Frame Pipeline Usage cog.

Shells out to native/gpu_pipeline.exe, which uses Windows PDH
"GPU Engine" counters to report per-engine GPU utilisation.

Requires Windows 10 version 1709+ with up-to-date GPU drivers.

Commands:
  !gpupipeline             — Full GPU pipeline usage (all engines)
  !gpupipelinelive         — Live updating pipeline dashboard
  !gpupipelinepid  <PID>  — Pipeline usage for a specific process
  !gpupipelinechart        — Bar chart of current engine usage
  !gpupipelineadapters     — List DXGI adapters with LUID
  !gpupipelinebuild        — Compile gpu_pipeline.exe
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
from typing import Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.gpu_pipeline")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_NATIVE_DIR = Path(__file__).parent.parent / "native"
_EXE_PATH   = _NATIVE_DIR / "gpu_pipeline.exe"
_SRC_PATH   = _NATIVE_DIR / "gpu_pipeline.cpp"


# ---------------------------------------------------------------------------
# Binary invocation
# ---------------------------------------------------------------------------

def _call(args: list[str], timeout: int = 20) -> dict | list:
    if not _EXE_PATH.exists():
        raise RuntimeError(
            f"`gpu_pipeline.exe` not found at `{_EXE_PATH}`.\n"
            "Run `!gpupipelinebuild` to compile it."
        )
    cmd = [str(_EXE_PATH)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    stdout = r.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"gpu_pipeline.exe produced no output (exit {r.returncode}).\n"
            f"stderr: {r.stderr[:500]}"
        )
    return json.loads(stdout)


def _get_pipeline(pid: Optional[int] = None) -> dict:
    args = ["--json", "--count", "1"]
    if pid is not None:
        args += ["--pid", str(pid)]
    return _call(args)


def _get_adapters() -> dict:
    return _call(["--listadapters"])


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

# Canonical engine display names + emoji
_ENGINE_META: dict[str, tuple[str, str]] = {
    "3d":            ("🎮 3D / Graphics", "3d"),
    "compute":       ("⚙️ Async Compute",  "compute"),
    "copy":          ("📋 Copy / DMA",     "copy"),
    "video_decode":  ("📺 Video Decode",   "video_decode"),
    "video_encode":  ("🎥 Video Encode",   "video_encode"),
    "video_process": ("🎞️ Video Process",  "video_process"),
    "overlay":       ("🖼️ Overlay",        "overlay"),
    "total_running": ("🏃 Total Running",  "total_running"),
}


def _bar(pct: float, width: int = 16) -> str:
    if pct < 0:
        return "░" * width + " N/A"
    filled = min(int(pct / 100.0 * width), width)
    return "█" * filled + "░" * (width - filled)


def _build_pipeline_embed(data: dict, pid: Optional[int] = None) -> discord.Embed:
    engines: dict = data.get("engines", {})
    ts = data.get("timestamp", time.time() * 1000)
    ts_str = time.strftime("%H:%M:%S", time.localtime(ts / 1000))

    title = f"🖥️ GPU Pipeline — {'All Processes' if pid is None else f'PID {pid}'}"

    fields = []
    for key, (label, _) in _ENGINE_META.items():
        info = engines.get(key)
        pct  = info if isinstance(info, (int, float)) else -1.0
        bar  = _bar(pct)
        val  = f"`{bar}` **{pct:.1f}%**" if pct >= 0 else "`N/A`"
        fields.append((label, val, False))

    # Dominant engine
    valid = {k: v for k, v in engines.items()
             if isinstance(v, (int, float)) and v >= 0 and k != "total_running"}
    if valid:
        dominant = max(valid, key=lambda k: valid[k])
        dom_label = _ENGINE_META.get(dominant, (dominant, dominant))[0]
        fields.append(("🔝 Most Active Engine",
                        f"{dom_label}: **{valid[dominant]:.1f}%**", True))

    return build_embed(
        title,
        f"Sampled at `{ts_str}` via PDH GPU Engine counters",
        color=Config.COLOR_MONITOR,
        fields=fields,
    )


def _pipeline_chart(engines: dict) -> io.BytesIO:
    """Render a horizontal bar chart of engine utilisation."""
    import matplotlib.pyplot as plt
    import numpy as np

    labels, values, colors = [], [], []
    cmap = [
        "#3498db", "#e74c3c", "#2ecc71", "#f39c12",
        "#9b59b6", "#1abc9c", "#e67e22", "#95a5a6",
    ]
    for i, (key, (label, _)) in enumerate(_ENGINE_META.items()):
        info = engines.get(key)
        pct  = info if isinstance(info, (int, float)) and info >= 0 else 0.0
        labels.append(label.replace("️", ""))  # strip emoji for matplotlib
        values.append(pct)
        colors.append(cmap[i % len(cmap)])

    fig, ax = plt.subplots(figsize=(10, 4), dpi=90)
    y = np.arange(len(labels))
    bars = ax.barh(y, values, color=colors, height=0.6)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Utilisation (%)")
    ax.set_title("GPU Pipeline Engine Usage")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=9)
    ax.grid(axis="x", alpha=0.3)
    ax.invert_yaxis()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Auto-build
# ---------------------------------------------------------------------------

def _build_binary() -> tuple[bool, str]:
    src = str(_SRC_PATH)
    out = str(_EXE_PATH)

    for compiler, args in [
        ("cl",  ["cl", "/EHsc", "/O2", "/W4", src,
                  "/link", "pdh.lib", "dxgi.lib", "kernel32.lib",
                  f"/out:{out}"]),
        ("g++", ["g++", "-std=c++17", "-O2", src,
                  "-lpdh", "-ldxgi", "-o", out]),
    ]:
        try:
            r = subprocess.run(args, capture_output=True, text=True,
                               timeout=90, cwd=str(_NATIVE_DIR))
            if r.returncode == 0 and _EXE_PATH.exists():
                return True, f"✅ Built with **{compiler}**."
            err = r.stderr[-300:]
        except FileNotFoundError:
            err = f"{compiler} not found"

    return False, f"Build failed. Install MSVC or MinGW.\nLast error: {err}"


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class GPUPipeline(commands.Cog):
    """GPU Frame Pipeline engine usage via Windows PDH GPU Engine counters."""

    def __init__(self, bot):
        self.bot = bot
        self._last_data: Optional[dict] = None

    @staticmethod
    def _is_windows() -> bool:
        return platform.system() == "Windows"

    async def _guard(self, ctx) -> bool:
        if not self._is_windows():
            await ctx.send(embed=build_embed(
                "❌ Windows Only",
                "GPU Engine PDH counters are Windows-only.",
                color=Config.COLOR_ERROR,
            ))
            return False
        return True

    # ── Commands ───────────────────────────────────────────────────────────

    @commands.command(name="gpupipeline", aliases=["gpuengines", "gpuengine"])
    async def pipeline_snapshot(self, ctx):
        """Show GPU pipeline engine utilisation (all processes)."""
        if not await self._guard(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "⏳ Sampling GPU Pipeline…",
            "Querying PDH GPU Engine counters (~1 s)…",
            color=Config.COLOR_MONITOR,
        ))
        try:
            data = await run_in_executor(_get_pipeline)
            if "error" in data:
                await msg.edit(embed=build_embed(
                    "❌ PDH Error", data["error"], color=Config.COLOR_ERROR
                ))
                return
            self._last_data = data
            await msg.edit(embed=_build_pipeline_embed(data))
        except Exception as exc:
            logger.error(f"gpupipeline error: {exc}")
            await msg.edit(embed=build_embed(
                "❌ Error", truncate(str(exc), 1500), color=Config.COLOR_ERROR
            ))

    @commands.command(name="gpupipelinelive", aliases=["gpuenginelive"])
    async def pipeline_live(self, ctx, duration: int = 60, interval: int = 4):
        """
        Live GPU pipeline dashboard.
        !gpupipelinelive [duration=60] [interval=4]
        """
        if not await self._guard(ctx):
            return
        if interval < 3: interval = 3
        if duration > 300: duration = 300

        msg = await ctx.send(embed=build_embed(
            "🖥️ GPU Pipeline Live", "Starting…", color=Config.COLOR_MONITOR
        ))
        steps = duration // interval
        for _ in range(steps):
            try:
                data = await run_in_executor(_get_pipeline)
                if "error" in data:
                    await msg.edit(embed=build_embed(
                        "❌ PDH Error", data["error"], color=Config.COLOR_ERROR
                    ))
                    break
                self._last_data = data
                await msg.edit(embed=_build_pipeline_embed(data))
                await asyncio.sleep(interval)
            except discord.HTTPException:
                break
            except Exception as exc:
                logger.error(f"gpupipelinelive error: {exc}")
                break

        await msg.edit(embed=build_embed(
            "🖥️ GPU Pipeline Live", "✅ Session ended.", color=Config.COLOR_INFO
        ))

    @commands.command(name="gpupipelinepid", aliases=["gpuenginepid"])
    async def pipeline_pid(self, ctx, pid: int):
        """
        Show GPU pipeline usage for a specific process.
        !gpupipelinepid <PID>
        """
        if not await self._guard(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "⏳ Sampling…",
            f"Filtering GPU Engine counters for PID **{pid}**…",
            color=Config.COLOR_MONITOR,
        ))
        try:
            data = await run_in_executor(_get_pipeline, pid)
            if "error" in data:
                await msg.edit(embed=build_embed(
                    "❌ PDH Error", data["error"], color=Config.COLOR_ERROR
                ))
                return
            await msg.edit(embed=_build_pipeline_embed(data, pid=pid))
        except Exception as exc:
            await msg.edit(embed=build_embed(
                "❌ Error", truncate(str(exc), 1500), color=Config.COLOR_ERROR
            ))

    @commands.command(name="gpupipelinechart", aliases=["gpuenginechart"])
    async def pipeline_chart(self, ctx):
        """
        Render a bar chart of current GPU pipeline engine usage.
        Runs a fresh sample if no data cached.
        """
        if not await self._guard(ctx):
            return

        async with ctx.typing():
            if self._last_data is None:
                try:
                    self._last_data = await run_in_executor(_get_pipeline)
                except Exception as exc:
                    await ctx.send(embed=build_embed(
                        "❌ Error", truncate(str(exc), 1500), color=Config.COLOR_ERROR
                    ))
                    return

            if "error" in self._last_data:
                await ctx.send(embed=build_embed(
                    "❌ PDH Error", self._last_data["error"], color=Config.COLOR_ERROR
                ))
                return

            engines = self._last_data.get("engines", {})
            buf = await run_in_executor(_pipeline_chart, engines)

        await ctx.send(
            embed=build_embed("🖥️ GPU Pipeline Chart", color=Config.COLOR_MONITOR),
            file=discord.File(buf, "gpu_pipeline.png"),
        )

    @commands.command(name="gpupipelineadapters", aliases=["gpuenginelist"])
    async def pipeline_adapters(self, ctx):
        """List DXGI adapters with LUID (for PDH counter path reference)."""
        if not await self._guard(ctx):
            return
        async with ctx.typing():
            try:
                data = await run_in_executor(_get_adapters)
            except Exception as exc:
                await ctx.send(embed=build_embed(
                    "❌ Error", truncate(str(exc), 1500), color=Config.COLOR_ERROR
                ))
                return

        adapters = data.get("adapters", [])
        if not adapters:
            await ctx.send(embed=build_embed(
                "🖥️ DXGI Adapters", "No adapters found.", color=Config.COLOR_WARNING
            ))
            return

        fields = [
            (
                f"GPU {a['index']}: {a['name']}",
                f"VRAM: **{a['vram_mb']} MiB**\n"
                f"Vendor: `{a['vendor']}`\n"
                f"LUID: `{a['luid']}`",
                False,
            )
            for a in adapters
        ]
        await ctx.send(embed=build_embed(
            f"🖥️ DXGI Adapters ({len(adapters)})",
            "LUIDs appear in PDH GPU Engine counter instance names.",
            color=Config.COLOR_INFO,
            fields=fields,
        ))

    @commands.command(name="gpupipelinebuild", aliases=["buildgpupipeline"])
    @admin_only()
    async def pipeline_build(self, ctx):
        """Compile native/gpu_pipeline.cpp → native/gpu_pipeline.exe."""
        if not self._is_windows():
            await ctx.send("❌ Windows only.")
            return

        msg = await ctx.send(embed=build_embed(
            "🔨 Compiling gpu_pipeline.cpp…", "⏳ Trying MSVC then g++…",
            color=Config.COLOR_MONITOR,
        ))
        async with ctx.typing():
            ok, output = await run_in_executor(_build_binary)

        color = Config.COLOR_SUCCESS if ok else Config.COLOR_ERROR
        title = "✅ Build Successful" if ok else "❌ Build Failed"
        await msg.edit(embed=build_embed(title, truncate(output, 1800), color=color))


async def setup(bot):
    await bot.add_cog(GPUPipeline(bot))
