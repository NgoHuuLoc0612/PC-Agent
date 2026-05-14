"""
Remote Control cog — mouse, keyboard, window management, screen streaming.
Requires: pyautogui, Pillow (pip install pyautogui pillow)
"""

import asyncio
import io
import time
from typing import Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.remote_control")


def _get_pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # move mouse to corner to abort
        pyautogui.PAUSE = 0.05
        return pyautogui
    except ImportError:
        return None


def _get_pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        return None


class RemoteControl(commands.Cog, name="RemoteControl"):
    """Advanced remote control — mouse, keyboard, windows, streaming."""

    def __init__(self, bot):
        self.bot = bot
        self._streaming = False

    # ── Mouse ────────────────────────────────────────────────

    @commands.command(name="remoteclick", aliases=["mclick", "rmclick"])
    @admin_only()
    async def remote_click(self, ctx, x: int, y: int, button: str = "left"):
        """Click at screen coordinates. Usage: !remoteclick 960 540 [left|right|middle]"""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed. Run: `pip install pyautogui`")
            return
        if button not in ("left", "right", "middle"):
            button = "left"
        try:
            pag.click(x, y, button=button)
            await ctx.send(embed=build_embed(
                "🖱️ Mouse Click",
                f"Clicked **{button}** at `({x}, {y})`",
                color=Config.COLOR_SUCCESS
            ))
            logger.info(f"{ctx.author} clicked {button} at ({x},{y})")
        except Exception as e:
            await ctx.send(f"❌ Click failed: `{e}`")

    @commands.command(name="remotedoubleclick", aliases=["rdblclick"])
    @admin_only()
    async def remote_double_click(self, ctx, x: int, y: int):
        """Double-click at coordinates. Usage: !remotedoubleclick 960 540"""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        try:
            pag.doubleClick(x, y)
            await ctx.send(embed=build_embed(
                "🖱️ Double Click", f"Double-clicked at `({x}, {y})`", color=Config.COLOR_SUCCESS
            ))
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    @commands.command(name="remotemove", aliases=["rmovemouse", "rmousemove"])
    @admin_only()
    async def remote_move(self, ctx, x: int, y: int):
        """Move mouse to coordinates. Usage: !remotemove 960 540"""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        try:
            pag.moveTo(x, y, duration=0.3)
            await ctx.send(embed=build_embed(
                "🖱️ Mouse Moved", f"Mouse moved to `({x}, {y})`", color=Config.COLOR_SUCCESS
            ))
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    @commands.command(name="remotescroll", aliases=["rscroll", "rmousescroll"])
    @admin_only()
    async def remote_scroll(self, ctx, direction: str = "down", amount: int = 3):
        """Scroll mouse wheel. Usage: !remotescroll [up|down] [amount]"""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        clicks = amount if direction.lower() == "up" else -amount
        try:
            pag.scroll(clicks)
            await ctx.send(embed=build_embed(
                "🖱️ Scrolled",
                f"Scrolled **{direction}** {amount} clicks",
                color=Config.COLOR_SUCCESS
            ))
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    @commands.command(name="rmousepos", aliases=["rcursorpos", "mouseposition"])
    @admin_only()
    async def mouse_pos(self, ctx):
        """Get current mouse cursor position."""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        x, y = pag.position()
        size = pag.size()
        await ctx.send(embed=build_embed(
            "🖱️ Mouse Position",
            f"Position: `({x}, {y})`\nScreen size: `{size.width}x{size.height}`",
            color=Config.COLOR_INFO
        ))

    @commands.command(name="remotedrag", aliases=["drag", "mousedrag"])
    @admin_only()
    async def remote_drag(self, ctx, x1: int, y1: int, x2: int, y2: int):
        """Drag from (x1,y1) to (x2,y2). Usage: !remotedrag 100 100 500 500"""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        try:
            pag.moveTo(x1, y1, duration=0.2)
            pag.dragTo(x2, y2, duration=0.5, button="left")
            await ctx.send(embed=build_embed(
                "🖱️ Drag Complete",
                f"Dragged from `({x1}, {y1})` to `({x2}, {y2})`",
                color=Config.COLOR_SUCCESS
            ))
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    # ── Keyboard ─────────────────────────────────────────────

    @commands.command(name="remotetype", aliases=["rtypeit", "rtypetext"])
    @admin_only()
    async def remote_type(self, ctx, *, text: str):
        """Type text on the PC. Usage: !remotetype Hello World"""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        try:
            pag.write(text, interval=0.03)
            await ctx.send(embed=build_embed(
                "⌨️ Typed",
                f"Typed: `{truncate(text, 200)}`",
                color=Config.COLOR_SUCCESS
            ))
            logger.info(f"{ctx.author} typed text on PC")
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    @commands.command(name="remotekey", aliases=["rpresskey", "rhotkey"])
    @admin_only()
    async def remote_key(self, ctx, *, keys: str):
        """Press a key or hotkey combo. Usage: !remotekey ctrl+c | !remotekey enter
        Supported: ctrl, alt, shift, win, f1-f12, enter, esc, tab, space, delete, etc."""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        try:
            parts = [k.strip().lower() for k in keys.replace("+", " ").split()]
            if len(parts) == 1:
                pag.press(parts[0])
            else:
                pag.hotkey(*parts)
            await ctx.send(embed=build_embed(
                "⌨️ Key Pressed",
                f"Keys: `{keys}`",
                color=Config.COLOR_SUCCESS
            ))
            logger.info(f"{ctx.author} pressed keys: {keys}")
        except Exception as e:
            await ctx.send(f"❌ Error: `{e}`")

    @commands.command(name="remoteenter", aliases=["pressenter"])
    @admin_only()
    async def remote_enter(self, ctx):
        """Press Enter key."""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        pag.press("enter")
        await ctx.send("✅ Pressed Enter")

    @commands.command(name="remotepaste", aliases=["pasteclipboard"])
    @admin_only()
    async def remote_paste(self, ctx):
        """Paste clipboard content (Ctrl+V)."""
        pag = _get_pyautogui()
        if not pag:
            await ctx.send("❌ pyautogui not installed.")
            return
        pag.hotkey("ctrl", "v")
        await ctx.send("✅ Pasted clipboard (Ctrl+V)")

    # ── Window Management ────────────────────────────────────

    @commands.command(name="windowlist", aliases=["listwindows", "windows"])
    @admin_only()
    async def window_list(self, ctx):
        """List all open windows."""
        async with ctx.typing():
            try:
                import ctypes
                import ctypes.wintypes

                EnumWindows = ctypes.windll.user32.EnumWindows
                GetWindowText = ctypes.windll.user32.GetWindowTextW
                GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
                IsWindowVisible = ctypes.windll.user32.IsWindowVisible

                windows = []

                def callback(hwnd, _):
                    if IsWindowVisible(hwnd):
                        length = GetWindowTextLength(hwnd)
                        if length > 0:
                            buff = ctypes.create_unicode_buffer(length + 1)
                            GetWindowText(hwnd, buff, length + 1)
                            title = buff.value.strip()
                            if title:
                                windows.append((hwnd, title))
                    return True

                EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(callback), 0)

                if windows:
                    lines = [f"`{hwnd}` — {title}" for hwnd, title in windows[:30]]
                    desc = "\n".join(lines)
                    if len(windows) > 30:
                        desc += f"\n_...and {len(windows)-30} more_"
                else:
                    desc = "No visible windows found."

            except Exception as e:
                desc = f"Error: {e}"

        await ctx.send(embed=build_embed(
            f"🪟 Open Windows ({len(windows) if 'windows' in dir() else 0})",
            truncate(desc, 1500),
            color=Config.COLOR_INFO
        ))

    @commands.command(name="windowfocus", aliases=["focuswindow", "bringtofront"])
    @admin_only()
    async def window_focus(self, ctx, *, title: str):
        """Focus a window by partial title. Usage: !windowfocus Chrome"""
        async with ctx.typing():
            script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinFocus {{
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@
$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -match '{title}' }} | Select-Object -First 1
if ($proc) {{
    [WinFocus]::ShowWindow($proc.MainWindowHandle, 9)
    [WinFocus]::SetForegroundWindow($proc.MainWindowHandle)
    "Focused: $($proc.MainWindowTitle)"
}} else {{ "No window found matching: {title}" }}
"""
            result = await _run_ps(script)
        await ctx.send(embed=build_embed(
            "🪟 Window Focus",
            f"```{result}```",
            color=Config.COLOR_SUCCESS if "Focused" in result else Config.COLOR_ERROR
        ))

    @commands.command(name="windowclose", aliases=["closewindow"])
    @admin_only()
    async def window_close(self, ctx, *, title: str):
        """Close a window by partial title. Usage: !windowclose Notepad"""
        async with ctx.typing():
            script = f"""
$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -match '{title}' }} | Select-Object -First 1
if ($proc) {{
    $proc.CloseMainWindow() | Out-Null
    "Closed: $($proc.MainWindowTitle)"
}} else {{ "No window found matching: {title}" }}
"""
            result = await _run_ps(script)
        await ctx.send(embed=build_embed(
            "🪟 Window Closed",
            f"```{result}```",
            color=Config.COLOR_SUCCESS if "Closed" in result else Config.COLOR_ERROR
        ))

    @commands.command(name="windowmin", aliases=["minimizewindow", "minimize"])
    @admin_only()
    async def window_minimize(self, ctx, *, title: str):
        """Minimize a window. Usage: !windowmin Chrome"""
        async with ctx.typing():
            script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinMin {{
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@
$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -match '{title}' }} | Select-Object -First 1
if ($proc) {{
    [WinMin]::ShowWindow($proc.MainWindowHandle, 6)
    "Minimized: $($proc.MainWindowTitle)"
}} else {{ "No window found: {title}" }}
"""
            result = await _run_ps(script)
        await ctx.send(embed=build_embed("🪟 Minimized", f"```{result}```", color=Config.COLOR_SUCCESS))

    @commands.command(name="windowmax", aliases=["maximizewindow", "maximize"])
    @admin_only()
    async def window_maximize(self, ctx, *, title: str):
        """Maximize a window. Usage: !windowmax Chrome"""
        async with ctx.typing():
            script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinMax {{
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@
$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -match '{title}' }} | Select-Object -First 1
if ($proc) {{
    [WinMax]::ShowWindow($proc.MainWindowHandle, 3)
    "Maximized: $($proc.MainWindowTitle)"
}} else {{ "No window found: {title}" }}
"""
            result = await _run_ps(script)
        await ctx.send(embed=build_embed("🪟 Maximized", f"```{result}```", color=Config.COLOR_SUCCESS))

    # ── Screen Streaming ─────────────────────────────────────

    @commands.command(name="remotestream", aliases=["rscreenstream", "rlivestream"])
    @admin_only()
    async def remote_stream(self, ctx, frames: int = 5, interval: float = 2.0):
        """Stream screenshots to Discord. Usage: !remotestream [frames] [interval_sec]
        Max 10 frames, min 1.5s interval."""
        frames = min(frames, 10)
        interval = max(interval, 1.5)

        if self._streaming:
            await ctx.send("⚠️ Already streaming. Wait for current stream to finish.")
            return

        try:
            import pyautogui
            from PIL import Image
        except ImportError:
            await ctx.send("❌ Missing: `pip install pyautogui pillow`")
            return

        self._streaming = True
        await ctx.send(embed=build_embed(
            "📡 Stream Started",
            f"Sending {frames} frames every {interval}s\nMove mouse to top-left corner to abort.",
            color=Config.COLOR_INFO
        ))

        try:
            for i in range(frames):
                loop = asyncio.get_event_loop()
                img = await loop.run_in_executor(None, pyautogui.screenshot)

                # Resize to reduce size
                w, h = img.size
                scale = min(1280/w, 720/h, 1.0)
                img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                buf.seek(0)

                file = discord.File(buf, filename=f"stream_{i+1}.jpg")
                await ctx.send(
                    content=f"`Frame {i+1}/{frames}`",
                    file=file
                )

                if i < frames - 1:
                    await asyncio.sleep(interval)
        except Exception as e:
            await ctx.send(f"❌ Stream error: `{e}`")
        finally:
            self._streaming = False
            await ctx.send("📡 Stream ended.")

    @commands.command(name="rstreamstop", aliases=["rstopstreaming"])
    @admin_only()
    async def stream_stop(self, ctx):
        """Stop active screen stream."""
        self._streaming = False
        await ctx.send("📡 Stream stopped.")

    # ── Screen info ──────────────────────────────────────────

    @commands.command(name="screeninfo", aliases=["rdisplayinfo", "rresolution"])
    async def screen_info(self, ctx):
        """Show screen resolution and display info."""
        async with ctx.typing():
            pag = _get_pyautogui()
            script = """
Get-WmiObject Win32_VideoController | ForEach-Object {
    "Display: $($_.Name)"
    "Resolution: $($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)"
    "Refresh: $($_.CurrentRefreshRate) Hz"
    "Bits/Pixel: $($_.CurrentBitsPerPixel)"
    "---"
}
"""
            result = await _run_ps(script)
            size_info = ""
            if pag:
                s = pag.size()
                size_info = f"\n**PyAutoGUI Screen:** `{s.width}x{s.height}`"

        await ctx.send(embed=build_embed(
            "🖥️ Display Info",
            f"```\n{truncate(result, 900)}\n```{size_info}",
            color=Config.COLOR_INFO
        ))


async def _run_ps(script: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        return stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip()
    except Exception as e:
        return f"Error: {e}"


async def _run(*cmd, timeout=15) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace").strip()
    except Exception as e:
        return f"Error: {e}"


async def setup(bot):
    await bot.add_cog(RemoteControl(bot))
