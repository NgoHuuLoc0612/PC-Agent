"""
Clipboard commands — get, set, clear, history.
"""

from collections import deque
from typing import Deque

from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.clipboard")

MAX_HISTORY = 50


class Clipboard(commands.Cog):
    """System clipboard management."""

    def __init__(self, bot):
        self.bot = bot
        self._history: Deque[str] = deque(maxlen=MAX_HISTORY)

    def _get_clip(self) -> str:
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            raise RuntimeError("Install pyperclip: `pip install pyperclip`")

    def _set_clip(self, text: str):
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            raise RuntimeError("Install pyperclip: `pip install pyperclip`")

    @commands.command(name="getclipboard", aliases=["clip", "paste", "clipboard"])
    async def get_clipboard(self, ctx):
        """Get current clipboard content."""
        try:
            content = self._get_clip()
            if not content:
                await ctx.send(embed=build_embed("Clipboard", "Clipboard is empty.", color=Config.COLOR_WARNING))
                return
            self._history.appendleft(content)
            await ctx.send(embed=build_embed(
                "📋 Clipboard",
                f"```\n{truncate(content, 1900)}\n```",
                color=Config.COLOR_INFO,
                fields=[("Length", f"{len(content)} chars", True)],
            ))
        except Exception as e:
            await ctx.send(embed=build_embed("Clipboard", str(e), color=Config.COLOR_ERROR))

    @commands.command(name="setclipboard", aliases=["copyclip", "copytoclip"])
    @admin_only()
    async def set_clipboard(self, ctx, *, text: str):
        """Set clipboard content."""
        try:
            self._set_clip(text)
            self._history.appendleft(text)
            await ctx.send(embed=build_embed(
                "📋 Clipboard Set",
                f"```\n{truncate(text, 400)}\n```",
                color=Config.COLOR_SUCCESS,
            ))
        except Exception as e:
            await ctx.send(embed=build_embed("Clipboard", str(e), color=Config.COLOR_ERROR))

    @commands.command(name="clearclipboard", aliases=["clipclear"])
    @admin_only()
    async def clear_clipboard(self, ctx):
        """Clear the clipboard."""
        try:
            self._set_clip("")
            await ctx.send(embed=build_embed("Clipboard", "🗑️ Clipboard cleared.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Clipboard", str(e), color=Config.COLOR_ERROR))

    @commands.command(name="cliphistory", aliases=["cliplog"])
    async def clipboard_history(self, ctx, limit: int = 10):
        """Show clipboard history for this session."""
        if not self._history:
            await ctx.send(embed=build_embed("Clipboard History", "No history yet.", color=Config.COLOR_WARNING))
            return
        limit = min(limit, len(self._history))
        entries = list(self._history)[:limit]
        rows = [f"`{i+1}.` {truncate(e, 80).replace(chr(10), '↵')}" for i, e in enumerate(entries)]
        await ctx.send(embed=build_embed(
            f"📋 Clipboard History (last {limit})",
            "\n".join(rows),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="cliprestore", aliases=["restorecopy"])
    @admin_only()
    async def restore_clipboard(self, ctx, index: int = 1):
        """Restore a clipboard entry from history by index."""
        if not self._history:
            await ctx.send(embed=build_embed("Clipboard", "No history.", color=Config.COLOR_WARNING))
            return
        entries = list(self._history)
        if index < 1 or index > len(entries):
            await ctx.send(embed=build_embed("Clipboard", f"Index out of range (1–{len(entries)}).", color=Config.COLOR_ERROR))
            return
        text = entries[index - 1]
        try:
            self._set_clip(text)
            await ctx.send(embed=build_embed(
                "📋 Clipboard Restored",
                f"```\n{truncate(text, 400)}\n```",
                color=Config.COLOR_SUCCESS,
            ))
        except Exception as e:
            await ctx.send(embed=build_embed("Clipboard", str(e), color=Config.COLOR_ERROR))

    @commands.command(name="clipsearch", aliases=["searchclip"])
    async def search_clipboard_history(self, ctx, *, query: str):
        """Search clipboard history for a substring."""
        matches = [(i + 1, e) for i, e in enumerate(self._history) if query.lower() in e.lower()]
        if not matches:
            await ctx.send(embed=build_embed("Clipboard Search", f"No matches for `{query}`.", color=Config.COLOR_WARNING))
            return
        rows = [f"`{i}.` {truncate(e, 80)}" for i, e in matches[:10]]
        await ctx.send(embed=build_embed(
            f"🔍 Clipboard Search: {query}",
            "\n".join(rows),
            color=Config.COLOR_INFO,
        ))


async def setup(bot):
    await bot.add_cog(Clipboard(bot))
