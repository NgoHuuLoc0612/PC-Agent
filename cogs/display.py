"""
Display commands — screenshot, screen recording info, brightness, resolution, wallpaper.
"""

import io
import platform
import subprocess
from typing import Optional

import discord
from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, run_in_executor
from utils.logger import setup_logger

logger = setup_logger("cog.display")


class Display(commands.Cog):
    """Screen and display management."""

    def __init__(self, bot):
        self.bot = bot

    def _take_screenshot(self) -> io.BytesIO:
        try:
            import mss
            import mss.tools
            with mss.mss() as sct:
                monitor = sct.monitors[0]  # All monitors combined
                img = sct.grab(monitor)
                buf = io.BytesIO(mss.tools.to_png(img.rgb, img.size))
                buf.seek(0)
                return buf
        except ImportError:
            # Fallback to PIL
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True, quality=Config.SCREENSHOT_QUALITY)
            buf.seek(0)
            return buf

    def _take_monitor_screenshot(self, monitor_idx: int) -> io.BytesIO:
        import mss
        import mss.tools
        with mss.mss() as sct:
            monitors = sct.monitors
            idx = min(monitor_idx, len(monitors) - 1)
            img = sct.grab(monitors[idx])
            buf = io.BytesIO(mss.tools.to_png(img.rgb, img.size))
            buf.seek(0)
            return buf

    @commands.command(name="screenshot", aliases=["ss", "snap", "capture"])
    async def screenshot(self, ctx, monitor: int = 0):
        """Take a screenshot. monitor=0 for all, 1+ for specific monitor."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "screenshot")
        async with ctx.typing():
            try:
                if monitor > 0:
                    buf = await run_in_executor(self._take_monitor_screenshot, monitor)
                else:
                    buf = await run_in_executor(self._take_screenshot)

                embed = build_embed(
                    f"Screenshot (Monitor {monitor})" if monitor > 0 else "Screenshot (All Monitors)",
                    color=Config.COLOR_INFO,
                )
                await ctx.send(embed=embed, file=discord.File(buf, "screenshot.png"))
            except Exception as e:
                await ctx.send(embed=build_embed("Screenshot", f"Failed: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="monitors", aliases=["displays", "screens"])
    async def list_monitors(self, ctx):
        """List all connected monitors."""
        async with ctx.typing():
            try:
                import mss
                with mss.mss() as sct:
                    monitors = sct.monitors
                    fields = []
                    for i, m in enumerate(monitors):
                        label = "All Combined" if i == 0 else f"Monitor {i}"
                        fields.append((label, f"{m['width']}×{m['height']} @ ({m['left']},{m['top']})", True))
                    await ctx.send(embed=build_embed("Monitors", color=Config.COLOR_INFO, fields=fields))
            except ImportError:
                await ctx.send(embed=build_embed("Monitors", "Install mss: `pip install mss`", color=Config.COLOR_WARNING))
            except Exception as e:
                await ctx.send(embed=build_embed("Monitors", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="resolution", aliases=["screenres"])
    async def get_resolution(self, ctx):
        """Get current screen resolution."""
        try:
            import mss
            with mss.mss() as sct:
                primary = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                res = f"{primary['width']}×{primary['height']}"
                await ctx.send(embed=build_embed("Resolution", f"Primary display: **{res}**", color=Config.COLOR_INFO))
        except Exception as e:
            await ctx.send(embed=build_embed("Resolution", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="brightness")
    async def set_brightness(self, ctx, level: int):
        """Set screen brightness (0–100). Linux/Windows only."""
        level = max(0, min(100, level))
        try:
            if platform.system() == "Linux":
                import subprocess
                # Try xrandr first
                try:
                    ratio = level / 100
                    subprocess.run(["xrandr", "--output", "LVDS-1", "--brightness", str(ratio)], check=True)
                except Exception:
                    subprocess.run(["brightnessctl", "set", f"{level}%"], check=True)
            elif platform.system() == "Windows":
                import wmi
                c = wmi.WMI(namespace="wmi")
                methods = c.WmiMonitorBrightnessMethods()[0]
                methods.WmiSetBrightness(level, 0)
            else:
                await ctx.send(embed=build_embed("Brightness", "Not supported on this platform.", color=Config.COLOR_WARNING))
                return
            await ctx.send(embed=build_embed("Brightness", f"✅ Set to **{level}%**", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Brightness", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="wallpaper", aliases=["setbg", "background"])
    @admin_only()
    async def set_wallpaper(self, ctx, path: str = None):
        """Set desktop wallpaper. Provide a path or attach an image."""
        async with ctx.typing():
            try:
                img_path = None

                if ctx.message.attachments:
                    attachment = ctx.message.attachments[0]
                    tmp_path = f"/tmp/wallpaper_{ctx.author.id}.png"
                    await attachment.save(tmp_path)
                    img_path = tmp_path
                elif path:
                    img_path = path
                else:
                    await ctx.send(embed=build_embed("Wallpaper", "Provide a path or attach an image.", color=Config.COLOR_ERROR))
                    return

                if platform.system() == "Windows":
                    import ctypes
                    ctypes.windll.user32.SystemParametersInfoW(20, 0, img_path, 3)
                elif platform.system() == "Linux":
                    subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri",
                                    f"file://{img_path}"])
                elif platform.system() == "Darwin":
                    subprocess.run(["osascript", "-e",
                                    f'tell app "Finder" to set desktop picture to POSIX file "{img_path}"'])

                await ctx.send(embed=build_embed("Wallpaper", f"✅ Wallpaper set.", color=Config.COLOR_SUCCESS))
            except Exception as e:
                await ctx.send(embed=build_embed("Wallpaper", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="locksecreen", aliases=["lockscreen"])
    @admin_only()
    async def lock_screen(self, ctx):
        """Lock the screen."""
        try:
            if platform.system() == "Windows":
                import ctypes
                ctypes.windll.user32.LockWorkStation()
            elif platform.system() == "Linux":
                subprocess.run(["gnome-screensaver-command", "--lock"])
            elif platform.system() == "Darwin":
                subprocess.run(["pmset", "displaysleepnow"])
            await ctx.send(embed=build_embed("Lock Screen", "🔒 Screen locked.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Lock Screen", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="screensaver", aliases=["blank"])
    @admin_only()
    async def screensaver(self, ctx):
        """Turn display off / activate screensaver."""
        try:
            if platform.system() == "Linux":
                subprocess.run(["xset", "dpms", "force", "off"])
            elif platform.system() == "Darwin":
                subprocess.run(["pmset", "displaysleepnow"])
            elif platform.system() == "Windows":
                import ctypes
                ctypes.windll.user32.SendMessageW(65535, 0x0112, 0xF170, 2)
            await ctx.send(embed=build_embed("Display", "🖥️ Display turned off.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Display", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="annotate")
    async def annotate_screenshot(self, ctx, *, text: str = ""):
        """Take a screenshot and annotate it with text."""
        async with ctx.typing():
            try:
                from PIL import Image, ImageDraw, ImageFont
                import mss, mss.tools

                with mss.mss() as sct:
                    img_data = sct.grab(sct.monitors[0])
                    img = Image.frombytes("RGB", img_data.size, img_data.rgb)

                draw = ImageDraw.Draw(img)
                # Add timestamp and optional text
                import datetime
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                annotation = f"{ts} | {text}" if text else ts
                draw.rectangle([(0, 0), (len(annotation) * 7 + 10, 25)], fill=(0, 0, 0, 180))
                draw.text((5, 5), annotation, fill=(255, 255, 0))

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                await ctx.send(file=discord.File(buf, "annotated.png"))
            except Exception as e:
                await ctx.send(embed=build_embed("Annotate", f"Error: {e}", color=Config.COLOR_ERROR))


async def setup(bot):
    await bot.add_cog(Display(bot))
