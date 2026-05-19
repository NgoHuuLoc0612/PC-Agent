"""
fps_counter.py — Discord cog for DXGI-based FPS monitoring.

Shells out to native/fps_counter.exe which reads FPS data from
shared memory populated by fps_hook.dll (injected into the game).

Commands:
  !fpsinject  <PID>           — Inject fps_hook.dll into a process
  !fps        <PID>           — Show current FPS from shared memory
  !fpslive    <PID>           — Live FPS embed (30s)
  !frametimes <PID>           — Frame-time percentile stats
  !fpsgpulist                 — List DXGI adapters / displays
  !fpsbuild                   — Compile fps_counter.exe + fps_hook.dll
  !fpsprocs                   — List running DirectX processes (for PID lookup)
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.fps_counter")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_NATIVE_DIR    = Path(__file__).parent.parent / "native"
_FPS_EXE       = _NATIVE_DIR / "fps_counter.exe"
_FPS_HOOK_DLL  = _NATIVE_DIR / "fps_hook.dll"
_FPS_CPP       = _NATIVE_DIR / "fps_counter.cpp"
_HOOK_CPP      = _NATIVE_DIR / "fps_hook.cpp"


# ---------------------------------------------------------------------------
# Binary invocation
# ---------------------------------------------------------------------------

def _call(args: list[str], timeout: int = 15) -> dict:
    """Run fps_counter.exe with given args, parse JSON stdout."""
    if not _FPS_EXE.exists():
        raise RuntimeError(
            f"`fps_counter.exe` not found at `{_FPS_EXE}`.\n"
            "Run `!fpsbuild` to compile it."
        )
    cmd = [str(_FPS_EXE)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    stdout = r.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"fps_counter.exe produced no output (exit {r.returncode}).\n"
            f"stderr: {r.stderr[:400]}"
        )
    return json.loads(stdout)


def _query_fps(pid: int) -> dict:
    return _call(["--query", str(pid)])


def _query_frametimes(pid: int, samples: int = 256) -> dict:
    return _call(["--frametimes", str(pid), "--samples", str(samples)])


def _inject(pid: int) -> dict:
    return _call(["--inject", str(pid)], timeout=20)


def _listgpu() -> dict:
    return _call(["--listgpu"], timeout=15)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fps_color(fps: float) -> int:
    if fps >= 120: return 0x2ECC71   # green
    if fps >= 60:  return 0xF39C12   # orange
    if fps >= 30:  return 0xE67E22   # dark orange
    return 0xE74C3C                  # red


def _fps_bar(fps: float, cap: float = 240.0) -> str:
    filled = min(int(fps / cap * 20), 20)
    return "█" * filled + "░" * (20 - filled)


def _ms_to_fps(ms: float) -> str:
    return f"{1000/ms:.1f}" if ms > 0 else "N/A"


def _build_fps_embed(data: dict) -> discord.Embed:
    fps_1s = data.get("fps_1s", 0)
    fps_5s = data.get("fps_5s_avg", 0.0)
    elapsed = data.get("elapsed_since_present_ms", -1.0)
    pid = data.get("pid", "?")

    stale = elapsed > 3000 if elapsed >= 0 else False
    status = "⚠️ Stale (no frame for >3s)" if stale else "✅ Live"

    return build_embed(
        f"🎮 FPS Monitor — PID {pid}",
        f"Status: {status}",
        color=_fps_color(fps_1s),
        fields=[
            ("📊 FPS (1s)",      f"`{_fps_bar(fps_1s)}`  **{fps_1s} FPS**", False),
            ("📈 FPS (5s avg)",  f"**{fps_5s:.1f} FPS**", True),
            ("⏱️ Frame Age",    f"{elapsed:.0f} ms" if elapsed >= 0 else "N/A", True),
        ],
    )


def _build_frametimes_embed(data: dict) -> discord.Embed:
    pid     = data.get("pid", "?")
    n       = data.get("samples", 0)
    avg_ms  = data.get("avg_ms",  0.0)
    p50     = data.get("p50",     0.0)
    p95     = data.get("p95",     0.0)
    p99     = data.get("p99",     0.0)
    low1    = data.get("low1pct_ms",  0.0)
    low01   = data.get("low01pct_ms", 0.0)
    lo1_fps = data.get("low1pct_fps",  0.0)
    lo01fps = data.get("low01pct_fps", 0.0)
    min_ms  = data.get("min_ms",  0.0)
    max_ms  = data.get("max_ms",  0.0)
    avg_fps = data.get("avg_fps", 0.0)

    return build_embed(
        f"📉 Frame-Time Analysis — PID {pid}",
        f"{n} samples from shared memory ring buffer",
        color=_fps_color(avg_fps),
        fields=[
            ("⚡ Avg FPS",         f"**{avg_fps:.1f} FPS**  ({avg_ms:.2f} ms)", True),
            ("⏱️ Best / Worst",   f"{min_ms:.2f} ms / {max_ms:.2f} ms", True),
            ("📊 Percentiles (ms)",
             f"p50: **{p50:.2f}**  p95: **{p95:.2f}**  p99: **{p99:.2f}**", False),
            ("🔻 1% Low",
             f"{low1:.2f} ms  →  **{lo1_fps:.1f} FPS**", True),
            ("🔻 0.1% Low",
             f"{low01:.2f} ms  →  **{lo01fps:.1f} FPS**", True),
        ],
    )


# ---------------------------------------------------------------------------
# Process discovery helper (finds DirectX-linked processes)
# ---------------------------------------------------------------------------

def _find_dx_procs() -> list[dict]:
    """
    Use tasklist to find processes that have dxgi.dll or d3d11.dll loaded.
    Returns list of {pid, name} dicts.
    """
    try:
        r = subprocess.run(
            ["tasklist", "/M", "dxgi.dll", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=20
        )
        procs = []
        for line in r.stdout.strip().splitlines():
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                try:
                    procs.append({"name": parts[0], "pid": int(parts[1])})
                except ValueError:
                    pass
        return procs
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Auto-build
# ---------------------------------------------------------------------------

def _build_binaries() -> tuple[bool, str]:
    """Compile fps_counter.exe and fps_hook.dll."""
    output_lines = []

    # Try MSVC
    for exe_args, dll_args, compiler in [
        (
            ["cl", "/EHsc", "/O2", "/W4", str(_FPS_CPP),
             "/link", "dxgi.lib", "user32.lib", "kernel32.lib",
             f"/out:{_FPS_EXE}"],
            ["cl", "/EHsc", "/O2", "/W4", "/LD", str(_HOOK_CPP),
             "/link", "dxgi.lib", "d3d11.lib", "user32.lib", "kernel32.lib",
             f"/out:{_FPS_HOOK_DLL}"],
            "MSVC",
        ),
        (
            ["g++", "-std=c++17", "-O2", str(_FPS_CPP),
             "-ldxgi", "-luser32", "-lkernel32",
             "-o", str(_FPS_EXE)],
            ["g++", "-std=c++17", "-O2", "-shared", str(_HOOK_CPP),
             "-ldxgi", "-ld3d11", "-luser32", "-lkernel32",
             "-o", str(_FPS_HOOK_DLL)],
            "MinGW g++",
        ),
    ]:
        try:
            r1 = subprocess.run(exe_args, capture_output=True, text=True,
                                timeout=90, cwd=str(_NATIVE_DIR))
            r2 = subprocess.run(dll_args, capture_output=True, text=True,
                                timeout=90, cwd=str(_NATIVE_DIR))
            if r1.returncode == 0 and r2.returncode == 0:
                return True, f"✅ Built with **{compiler}**."
            output_lines.append(f"{compiler} error:\n{r1.stderr[-200:]}\n{r2.stderr[-200:]}")
        except FileNotFoundError:
            output_lines.append(f"{compiler} not found.")

    return False, "Failed:\n" + "\n".join(output_lines)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class FPSCounter(commands.Cog):
    """DXGI-based FPS counter via native C++ hook DLL."""

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _is_windows() -> bool:
        return platform.system() == "Windows"

    async def _guard(self, ctx) -> bool:
        if not self._is_windows():
            await ctx.send(embed=build_embed(
                "❌ Windows Only",
                "FPS counter requires Windows (DXGI API).",
                color=Config.COLOR_ERROR,
            ))
            return False
        return True

    # ── Commands ───────────────────────────────────────────────────────────

    @commands.command(name="fpsinject", aliases=["injectfps", "hookg ame"])
    @admin_only()
    async def fps_inject(self, ctx, pid: int):
        """
        Inject fps_hook.dll into a process to enable FPS tracking.
        !fpsinject <PID>
        Requires Administrator.  Use !fpsprocs to find the PID.
        ⚠️ Do NOT use on online games with anti-cheat.
        """
        if not await self._guard(ctx):
            return

        msg = await ctx.send(embed=build_embed(
            "💉 Injecting fps_hook.dll…",
            f"⏳ Targeting PID **{pid}**…",
            color=Config.COLOR_MONITOR,
        ))
        try:
            data = await run_in_executor(_inject, pid)
        except Exception as exc:
            await msg.edit(embed=build_embed("❌ Inject Failed",
                                              truncate(str(exc), 1500),
                                              color=Config.COLOR_ERROR))
            return

        if "error" in data:
            await msg.edit(embed=build_embed(
                "❌ Inject Failed",
                data["error"] + f"\n\nHint: {data.get('hint', '')}",
                color=Config.COLOR_ERROR,
            ))
        else:
            await msg.edit(embed=build_embed(
                "✅ Injected Successfully",
                f"**PID:** {data.get('pid')}\n"
                f"**DLL:** `{data.get('dll')}`\n"
                f"**Module base:** `{data.get('module_base')}`\n\n"
                "Now use `!fps <PID>` or `!fpslive <PID>` to monitor.",
                color=Config.COLOR_SUCCESS,
            ))

    @commands.command(name="fps", aliases=["getfps", "fpscheck"])
    async def fps_query(self, ctx, pid: int):
        """Show current FPS for an injected process. !fps <PID>"""
        if not await self._guard(ctx):
            return
        try:
            data = await run_in_executor(_query_fps, pid)
        except Exception as exc:
            await ctx.send(embed=build_embed("❌ FPS Query Failed",
                                              truncate(str(exc), 1500),
                                              color=Config.COLOR_ERROR))
            return

        if "error" in data:
            await ctx.send(embed=build_embed(
                "❌ FPS Data Unavailable",
                data["error"] + "\n\nMake sure you ran `!fpsinject` first.",
                color=Config.COLOR_ERROR,
            ))
        else:
            await ctx.send(embed=_build_fps_embed(data))

    @commands.command(name="fpslive", aliases=["fpsmoni", "watchfps"])
    async def fps_live(self, ctx, pid: int, duration: int = 60, interval: int = 2):
        """
        Live FPS embed, refreshing every <interval> seconds for <duration> s.
        !fpslive <PID> [duration=60] [interval=2]
        """
        if not await self._guard(ctx):
            return
        if interval < 1: interval = 1
        if duration > 300: duration = 300

        msg = await ctx.send(embed=build_embed(
            "🎮 FPS Live", "Starting…", color=Config.COLOR_MONITOR
        ))
        steps = duration // interval
        for _ in range(steps):
            try:
                data = await run_in_executor(_query_fps, pid)
                if "error" in data:
                    await msg.edit(embed=build_embed(
                        "❌ Error", data["error"], color=Config.COLOR_ERROR
                    ))
                    break
                await msg.edit(embed=_build_fps_embed(data))
                await asyncio.sleep(interval)
            except discord.HTTPException:
                break
            except Exception as exc:
                logger.error(f"fpslive error: {exc}")
                break

        await msg.edit(embed=build_embed(
            "🎮 FPS Live", "✅ Session ended.", color=Config.COLOR_INFO
        ))

    @commands.command(name="frametimes", aliases=["frametime", "fpspercentiles"])
    async def fps_frametimes(self, ctx, pid: int, samples: int = 256):
        """
        Frame-time percentile analysis from ring buffer.
        !frametimes <PID> [samples=256]
        """
        if not await self._guard(ctx):
            return
        try:
            data = await run_in_executor(_query_frametimes, pid, samples)
        except Exception as exc:
            await ctx.send(embed=build_embed("❌ Frame-Time Error",
                                              truncate(str(exc), 1500),
                                              color=Config.COLOR_ERROR))
            return

        if "error" in data:
            await ctx.send(embed=build_embed(
                "❌ No Frame-Time Data", data["error"], color=Config.COLOR_ERROR
            ))
        else:
            await ctx.send(embed=_build_frametimes_embed(data))

    @commands.command(name="fpsgpulist", aliases=["dxgiadapters", "listadapters"])
    async def fps_gpu_list(self, ctx):
        """List DXGI adapters (GPUs) and their connected displays."""
        if not await self._guard(ctx):
            return
        async with ctx.typing():
            try:
                data = await run_in_executor(_listgpu)
            except Exception as exc:
                await ctx.send(embed=build_embed("❌ DXGI Error",
                                                  truncate(str(exc), 1500),
                                                  color=Config.COLOR_ERROR))
                return

        adapters = data.get("adapters", [])
        if not adapters:
            await ctx.send(embed=build_embed(
                "🖥️ DXGI Adapters", "No adapters found.", color=Config.COLOR_WARNING
            ))
            return

        fields = []
        for a in adapters:
            outputs = a.get("outputs", [])
            out_str = "\n".join(
                f"  └ `{o['name']}` {o['max_resolution']} @ {o['max_refresh']}Hz"
                for o in outputs
            ) or "  └ No outputs"
            fields.append((
                f"GPU {a['index']}: {a['name']}",
                f"VRAM: **{a['vram_mb']} MiB**  Shared: {a['shared_mb']} MiB\n"
                f"VendorID: `{a['vendor_id']}`  DeviceID: `{a['device_id']}`\n"
                + out_str,
                False,
            ))

        await ctx.send(embed=build_embed(
            f"🖥️ DXGI Adapters ({len(adapters)} found)",
            "",
            color=Config.COLOR_INFO,
            fields=fields,
        ))

    @commands.command(name="fpsprocs", aliases=["dxprocs", "gamelist"])
    async def fps_procs(self, ctx):
        """List running processes that have loaded dxgi.dll (DirectX games/apps)."""
        if not await self._guard(ctx):
            return
        async with ctx.typing():
            procs = await run_in_executor(_find_dx_procs)

        if not procs:
            await ctx.send(embed=build_embed(
                "🎮 DirectX Processes",
                "No processes with dxgi.dll found.",
                color=Config.COLOR_WARNING,
            ))
            return

        lines = [f"`{p['pid']:>6}` **{p['name']}**" for p in procs[:30]]
        await ctx.send(embed=build_embed(
            f"🎮 DirectX Processes ({len(procs)} found)",
            truncate("\n".join(lines), 1800),
            color=Config.COLOR_INFO,
            fields=[("Tip", "Use `!fpsinject <PID>` to hook a process.", False)],
        ))

    @commands.command(name="fpsbuild", aliases=["buildfps", "compilefps"])
    @admin_only()
    async def fps_build(self, ctx):
        """
        Compile native/fps_counter.exe and native/fps_hook.dll.
        Requires MSVC or MinGW on PATH.
        """
        if not self._is_windows():
            await ctx.send("❌ Windows only.")
            return

        msg = await ctx.send(embed=build_embed(
            "🔨 Building FPS Binaries…",
            "⏳ Compiling `fps_counter.exe` and `fps_hook.dll`…",
            color=Config.COLOR_MONITOR,
        ))
        async with ctx.typing():
            ok, output = await run_in_executor(_build_binaries)

        color = Config.COLOR_SUCCESS if ok else Config.COLOR_ERROR
        title = "✅ Build Successful" if ok else "❌ Build Failed"
        await msg.edit(embed=build_embed(title, truncate(output, 1800), color=color))


async def setup(bot):
    await bot.add_cog(FPSCounter(bot))
