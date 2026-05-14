"""
Automation commands — run commands, open apps, keyboard/mouse control, typing.
"""

import asyncio
import platform
import subprocess
from typing import Optional

from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.automation")


class Automation(commands.Cog):
    """PC automation — shell, keyboard, mouse, applications."""

    def __init__(self, bot):
        self.bot = bot

    async def _shell(self, command: str, timeout: int = 30, shell: bool = True) -> tuple:
        """Run a shell command and return (stdout, stderr, returncode)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except asyncio.TimeoutError:
            return "", "Command timed out.", -1
        except Exception as e:
            return "", str(e), -1

    @commands.command(name="run", aliases=["exec", "shell", "cmd"])
    @admin_only()
    async def run_command(self, ctx, *, command: str):
        """Execute a shell command on the PC."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "run", command)
        async with ctx.typing():
            stdout, stderr, code = await self._shell(command)
            output = stdout or stderr or "(no output)"
            color = Config.COLOR_SUCCESS if code == 0 else Config.COLOR_ERROR
            embed = build_embed(
                f"$ {truncate(command, 100)}",
                f"```\n{truncate(output, 1900)}\n```",
                color=color,
            )
            embed.add_field(name="Exit Code", value=str(code), inline=True)
            await ctx.send(embed=embed)

    @commands.command(name="open", aliases=["launch", "start"])
    @admin_only()
    async def open_app(self, ctx, *, app: str):
        """Open an application or file."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "open", app)
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["start", "", app], shell=True)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", app])
            else:
                subprocess.Popen([app])
            await ctx.send(embed=build_embed("Open", f"✅ Launched: `{app}`", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Open", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="type", aliases=["typetext", "keyboard"])
    @admin_only()
    async def type_text(self, ctx, *, text: str):
        """Type text using keyboard automation."""
        try:
            import pyautogui
            await run_in_executor(pyautogui.typewrite, text, interval=0.02)
            await ctx.send(embed=build_embed("Type", f"✅ Typed `{truncate(text, 50)}`", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Type", "Install pyautogui: `pip install pyautogui`", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Type", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="hotkey", aliases=["shortcut", "keys"])
    @admin_only()
    async def hotkey(self, ctx, *keys: str):
        """Press a keyboard hotkey (e.g., ctrl c, alt f4, win d)."""
        if not keys:
            await ctx.send(embed=build_embed("Hotkey", "Provide key names (e.g., `!hotkey ctrl c`)", color=Config.COLOR_ERROR))
            return
        try:
            import pyautogui
            await run_in_executor(pyautogui.hotkey, *keys)
            await ctx.send(embed=build_embed("Hotkey", f"✅ Pressed: `{' + '.join(keys)}`", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Hotkey", "Install pyautogui.", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Hotkey", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="keypress", aliases=["press"])
    @admin_only()
    async def key_press(self, ctx, key: str, presses: int = 1):
        """Press a single key N times."""
        try:
            import pyautogui
            await run_in_executor(pyautogui.press, key, presses=presses)
            await ctx.send(embed=build_embed("Key Press", f"✅ Pressed `{key}` × {presses}", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Key Press", "Install pyautogui.", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Key Press", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="mousemove", aliases=["movemouse", "mmove"])
    @admin_only()
    async def move_mouse(self, ctx, x: int, y: int, duration: float = 0.5):
        """Move the mouse to coordinates (x, y)."""
        try:
            import pyautogui
            await run_in_executor(pyautogui.moveTo, x, y, duration=duration)
            await ctx.send(embed=build_embed("Mouse Move", f"✅ Moved to ({x}, {y})", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Mouse Move", "Install pyautogui.", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Mouse Move", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="click", aliases=["mouseclick"])
    @admin_only()
    async def click_mouse(self, ctx, x: int = None, y: int = None, button: str = "left"):
        """Click the mouse. Optional coordinates, button: left/right/middle."""
        try:
            import pyautogui
            if x is not None and y is not None:
                await run_in_executor(pyautogui.click, x, y, button=button)
                await ctx.send(embed=build_embed("Click", f"✅ {button.capitalize()} click at ({x}, {y})", color=Config.COLOR_SUCCESS))
            else:
                await run_in_executor(pyautogui.click, button=button)
                await ctx.send(embed=build_embed("Click", f"✅ {button.capitalize()} click at current position", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Click", "Install pyautogui.", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Click", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="doubleclick", aliases=["dblclick"])
    @admin_only()
    async def double_click(self, ctx, x: int = None, y: int = None):
        """Double-click the mouse."""
        try:
            import pyautogui
            if x and y:
                await run_in_executor(pyautogui.doubleClick, x, y)
            else:
                await run_in_executor(pyautogui.doubleClick)
            await ctx.send(embed=build_embed("Double Click", "✅ Double-clicked.", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Double Click", "Install pyautogui.", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Double Click", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="scroll", aliases=["scrollmouse"])
    @admin_only()
    async def scroll(self, ctx, amount: int = 3, direction: str = "up"):
        """Scroll the mouse wheel."""
        try:
            import pyautogui
            clicks = amount if direction == "up" else -amount
            await run_in_executor(pyautogui.scroll, clicks)
            await ctx.send(embed=build_embed("Scroll", f"✅ Scrolled {direction} by {amount}", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Scroll", "Install pyautogui.", color=Config.COLOR_WARNING))
        except Exception as e:
            await ctx.send(embed=build_embed("Scroll", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="mousepos", aliases=["cursorpos"])
    async def mouse_position(self, ctx):
        """Get current mouse cursor position."""
        try:
            import pyautogui
            x, y = pyautogui.position()
            await ctx.send(embed=build_embed("Mouse Position", f"📍 Cursor at `({x}, {y})`", color=Config.COLOR_INFO))
        except ImportError:
            await ctx.send(embed=build_embed("Mouse Position", "Install pyautogui.", color=Config.COLOR_WARNING))

    @commands.command(name="alert", aliases=["msgbox", "popup"])
    @admin_only()
    async def show_alert(self, ctx, title: str, *, message: str):
        """Show a popup message box on the PC."""
        try:
            if platform.system() == "Windows":
                import ctypes
                await run_in_executor(ctypes.windll.user32.MessageBoxW, 0, message, title, 0x40)
            elif platform.system() == "Darwin":
                await run_in_executor(subprocess.run, [
                    "osascript", "-e", f'display dialog "{message}" with title "{title}"'
                ])
            else:
                await run_in_executor(subprocess.run, ["zenity", "--info", f"--title={title}", f"--text={message}"])
            await ctx.send(embed=build_embed("Alert", "✅ Message displayed on PC.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Alert", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="script", aliases=["runscript"])
    @admin_only()
    async def run_script(self, ctx, interpreter: str = "python"):
        """Execute an uploaded script file. Attach the script as a file."""
        if not ctx.message.attachments:
            await ctx.send(embed=build_embed("Script", "Attach a script file.", color=Config.COLOR_ERROR))
            return
        async with ctx.typing():
            import tempfile, os
            attachment = ctx.message.attachments[0]
            suffix = "." + attachment.filename.split(".")[-1] if "." in attachment.filename else ".txt"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                await attachment.save(tmp.name)
                tmp_path = tmp.name

            try:
                stdout, stderr, code = await self._shell(f"{interpreter} {tmp_path}")
                output = stdout or stderr or "(no output)"
                color = Config.COLOR_SUCCESS if code == 0 else Config.COLOR_ERROR
                await ctx.send(embed=build_embed(
                    f"Script: {attachment.filename}",
                    f"```\n{truncate(output, 1900)}\n```",
                    color=color,
                    fields=[("Exit Code", str(code), True)],
                ))
            finally:
                os.unlink(tmp_path)

    @commands.command(name="env", aliases=["envvars", "environment"])
    async def env_vars(self, ctx, key: str = None):
        """Get environment variables. Optionally query a specific key."""
        import os
        if key:
            value = os.environ.get(key, None)
            if value is None:
                await ctx.send(embed=build_embed("Env", f"Variable `{key}` not found.", color=Config.COLOR_WARNING))
            else:
                await ctx.send(embed=build_embed("Env", f"`{key}` = `{truncate(value, 500)}`", color=Config.COLOR_INFO))
        else:
            envs = [f"`{k}` = `{truncate(v, 60)}`" for k, v in sorted(os.environ.items())]
            await ctx.send(embed=build_embed("Environment Variables",
                                             truncate("\n".join(envs[:50]), 4000), color=Config.COLOR_INFO))


async def setup(bot):
    await bot.add_cog(Automation(bot))
