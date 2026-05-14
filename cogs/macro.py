"""
Macro cog — record, save, and replay sequences of bot commands.
Macros are stored in SQLite and can be run on demand or scheduled.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.macro")


def _init_macro_db():
    import sqlite3
    from pathlib import Path
    path = Path(Config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macros (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            description TEXT,
            commands    TEXT NOT NULL,   -- JSON list of command strings
            created_by  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            run_count   INTEGER DEFAULT 0,
            last_run    TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


class MacroRecorder:
    """In-memory recorder for a single user session."""
    def __init__(self):
        self.recording = False
        self.macro_name = None
        self.commands = []
        self.user_id = None

    def start(self, name: str, user_id: int):
        self.recording = True
        self.macro_name = name
        self.commands = []
        self.user_id = user_id

    def record(self, cmd: str):
        if self.recording:
            self.commands.append(cmd)

    def stop(self):
        self.recording = False
        return self.commands.copy()


class Macro(commands.Cog):
    """Record and replay sequences of bot commands."""

    def __init__(self, bot):
        self.bot = bot
        self._recorder = MacroRecorder()
        _init_macro_db()
        logger.info("Macro cog loaded.")

    def _save_macro(self, name, description, commands_list, user):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        conn.execute("""
            INSERT INTO macros (name, description, commands, created_by)
            VALUES (?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                commands=excluded.commands,
                created_by=excluded.created_by,
                created_at=CURRENT_TIMESTAMP
        """, (name, description, json.dumps(commands_list), str(user)))
        conn.commit()
        conn.close()

    def _get_macro(self, name):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        row = conn.execute(
            "SELECT id, name, description, commands, created_by, run_count FROM macros WHERE name=?",
            (name,)
        ).fetchone()
        conn.close()
        return row

    def _list_macros(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        rows = conn.execute(
            "SELECT name, description, created_by, run_count, last_run FROM macros ORDER BY name"
        ).fetchall()
        conn.close()
        return rows

    def _delete_macro(self, name):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        affected = conn.execute("DELETE FROM macros WHERE name=?", (name,)).rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def _increment_run(self, name):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        conn.execute(
            "UPDATE macros SET run_count=run_count+1, last_run=CURRENT_TIMESTAMP WHERE name=?",
            (name,)
        )
        conn.commit()
        conn.close()

    # ── Recording ────────────────────────────────────────────

    @commands.command(name="macrorecord", aliases=["mrecord", "recordmacro"])
    @admin_only()
    async def macro_record(self, ctx, name: str):
        """Start recording a macro. Usage: !macrorecord my_macro
        Run commands after this, then !macrostop to save."""
        name = name.lower().strip()
        if not re.match(r'^[a-z0-9_\-]+$', name):
            await ctx.send("❌ Macro name can only contain letters, numbers, `_`, `-`")
            return
        if self._recorder.recording:
            await ctx.send(f"⚠️ Already recording macro `{self._recorder.macro_name}`. Use `!macrostop` first.")
            return

        self._recorder.start(name, ctx.author.id)
        await ctx.send(embed=build_embed(
            f"🔴 Recording: `{name}`",
            "Now run any bot commands. Use `!macrostop [description]` when done.\n"
            "Use `!macrocancel` to discard.",
            color=0xE74C3C
        ))
        logger.info(f"{ctx.author} started recording macro '{name}'")

    @commands.command(name="macrostop", aliases=["mstop", "stopmacro"])
    @admin_only()
    async def macro_stop(self, ctx, *, description: str = ""):
        """Stop recording and save the macro. Usage: !macrostop [optional description]"""
        if not self._recorder.recording:
            await ctx.send("❌ Not currently recording. Use `!macrorecord <name>` to start.")
            return
        if self._recorder.user_id != ctx.author.id:
            await ctx.send("❌ Only the person who started recording can stop it.")
            return

        cmds = self._recorder.stop()
        name = self._recorder.macro_name

        if not cmds:
            await ctx.send("⚠️ No commands were recorded. Macro not saved.")
            return

        self._save_macro(name, description, cmds, ctx.author)
        steps = "\n".join(f"{i+1}. `{c}`" for i, c in enumerate(cmds))
        await ctx.send(embed=build_embed(
            f"✅ Macro Saved: `{name}`",
            f"**{len(cmds)} steps recorded**\n{description}\n\n{steps}",
            color=Config.COLOR_SUCCESS
        ))
        logger.info(f"{ctx.author} saved macro '{name}' with {len(cmds)} steps")

    @commands.command(name="macrocancel", aliases=["mcancel"])
    @admin_only()
    async def macro_cancel(self, ctx):
        """Cancel current recording without saving."""
        if not self._recorder.recording:
            await ctx.send("❌ Not currently recording.")
            return
        name = self._recorder.macro_name
        self._recorder.stop()
        await ctx.send(embed=build_embed(
            "🚫 Recording Cancelled",
            f"Macro `{name}` discarded.",
            color=Config.COLOR_WARNING
        ))

    # ── Track commands while recording ───────────────────────

    @commands.Cog.listener()
    async def on_command(self, ctx):
        """Intercept commands while recording."""
        if not self._recorder.recording:
            return
        if ctx.author.id != self._recorder.user_id:
            return
        # Don't record macro commands themselves
        macro_cmds = {"macrorecord", "macrostop", "macrocancel", "macroplay",
                      "macrolist", "macrodelete", "macroshow", "macrorun"}
        if ctx.command and ctx.command.name in macro_cmds:
            return
        # Record the full command string
        full_cmd = ctx.message.content
        self._recorder.record(full_cmd)
        await ctx.message.add_reaction("🔴")  # visual indicator

    # ── Playback ─────────────────────────────────────────────

    @commands.command(name="macroplay", aliases=["mplay", "runmacro", "macro"])
    @admin_only()
    async def macro_play(self, ctx, name: str, delay: float = 0.5):
        """Play a saved macro. Usage: !macroplay my_macro [delay_between_steps]"""
        name = name.lower()
        row = self._get_macro(name)
        if not row:
            await ctx.send(f"❌ Macro `{name}` not found. Use `!macrolist` to see available macros.")
            return

        _, _, description, commands_json, created_by, run_count = row
        cmds = json.loads(commands_json)
        delay = max(0.1, min(delay, 10.0))

        status_msg = await ctx.send(embed=build_embed(
            f"▶️ Running Macro: `{name}`",
            f"{description}\n**{len(cmds)} steps** with {delay}s delay",
            color=Config.COLOR_INFO
        ))

        self._increment_run(name)

        for i, cmd_text in enumerate(cmds):
            try:
                # Update status
                embed = build_embed(
                    f"▶️ Macro: `{name}`",
                    f"Step {i+1}/{len(cmds)}: `{cmd_text}`",
                    color=Config.COLOR_INFO
                )
                await status_msg.edit(embed=embed)

                # Simulate message from the same user
                fake_msg = ctx.message
                # Create a new context with modified content
                msg_copy = discord.Message.__new__(discord.Message)
                msg_copy.__dict__.update(fake_msg.__dict__)
                msg_copy.content = cmd_text

                new_ctx = await self.bot.get_context(msg_copy)
                if new_ctx.valid:
                    await self.bot.invoke(new_ctx)
                else:
                    await ctx.send(f"⚠️ Step {i+1} unrecognized: `{cmd_text}`")

                await asyncio.sleep(delay)

            except Exception as e:
                await ctx.send(f"❌ Step {i+1} failed: `{e}`\nContinuing...")
                await asyncio.sleep(delay)

        await ctx.send(embed=build_embed(
            f"✅ Macro Complete: `{name}`",
            f"Ran {len(cmds)} steps successfully.",
            color=Config.COLOR_SUCCESS
        ))
        logger.info(f"{ctx.author} played macro '{name}'")

    # ── Management ───────────────────────────────────────────

    @commands.command(name="macrolist", aliases=["mlist", "macros"])
    async def macro_list(self, ctx):
        """List all saved macros."""
        rows = self._list_macros()
        if not rows:
            await ctx.send(embed=build_embed(
                "📋 Macros",
                "No macros saved yet. Use `!macrorecord <name>` to create one.",
                color=Config.COLOR_INFO
            ))
            return

        lines = []
        for name, desc, created_by, run_count, last_run in rows:
            lines.append(f"**`{name}`** — {desc or 'no description'} _(ran {run_count}x)_")

        await ctx.send(embed=build_embed(
            f"📋 Macros ({len(rows)})",
            "\n".join(lines),
            color=Config.COLOR_INFO
        ))

    @commands.command(name="macroshow", aliases=["mshow", "macroview"])
    async def macro_show(self, ctx, name: str):
        """Show steps in a macro. Usage: !macroshow my_macro"""
        row = self._get_macro(name.lower())
        if not row:
            await ctx.send(f"❌ Macro `{name}` not found.")
            return
        _, _, description, commands_json, created_by, run_count = row
        cmds = json.loads(commands_json)
        steps = "\n".join(f"{i+1}. `{c}`" for i, c in enumerate(cmds))
        await ctx.send(embed=build_embed(
            f"📋 Macro: `{name}`",
            f"{description or ''}\n**Created by:** {created_by}\n**Runs:** {run_count}\n\n{steps}",
            color=Config.COLOR_INFO
        ))

    @commands.command(name="macrodelete", aliases=["mdelete", "deletemacro"])
    @admin_only()
    async def macro_delete(self, ctx, name: str):
        """Delete a macro. Usage: !macrodelete my_macro"""
        if self._delete_macro(name.lower()):
            await ctx.send(embed=build_embed(
                "🗑️ Macro Deleted", f"Macro `{name}` removed.", color=Config.COLOR_WARNING
            ))
        else:
            await ctx.send(f"❌ Macro `{name}` not found.")

    @commands.command(name="macrorun", aliases=["mrun"])
    @admin_only()
    async def macro_run_quick(self, ctx, name: str):
        """Quick alias for macroplay. Usage: !macrorun my_macro"""
        await ctx.invoke(self.macro_play, name=name)

    # ── Quick macro (inline) ─────────────────────────────────

    @commands.command(name="macroquick", aliases=["mqquick"])
    @admin_only()
    async def macro_quick(self, ctx, name: str, *, commands_str: str):
        """Create a macro from a semicolon-separated list of commands.
        Usage: !macroquick my_macro !screenshot; !sysinfo; !diskinfo"""
        name = name.lower()
        cmds = [c.strip() for c in commands_str.split(";") if c.strip()]
        if not cmds:
            await ctx.send("❌ No commands provided. Separate with `;`")
            return
        self._save_macro(name, f"Quick macro by {ctx.author}", cmds, ctx.author)
        steps = "\n".join(f"{i+1}. `{c}`" for i, c in enumerate(cmds))
        await ctx.send(embed=build_embed(
            f"✅ Quick Macro Created: `{name}`",
            steps,
            color=Config.COLOR_SUCCESS
        ))


async def setup(bot):
    await bot.add_cog(Macro(bot))
