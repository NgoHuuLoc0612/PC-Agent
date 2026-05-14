"""
Task scheduler cog — schedule commands to run at specific times or intervals.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.scheduler")


class Scheduler(commands.Cog):
    """Schedule commands to run at specific times or intervals."""

    def __init__(self, bot):
        self.bot = bot
        self._task_runner.start()

    def cog_unload(self):
        self._task_runner.cancel()

    @tasks.loop(seconds=30)
    async def _task_runner(self):
        """Check and execute due scheduled tasks every 30 seconds."""
        try:
            pending = db.get_pending_tasks()
            for task in pending:
                asyncio.create_task(self._execute_task(task))
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

    async def _execute_task(self, task):
        """Execute a scheduled task."""
        try:
            channel = self.bot.get_channel(int(task["channel_id"]))
            if not channel:
                logger.warning(f"Channel {task['channel_id']} not found for task {task['id']}")
                db.remove_task(task["id"])
                return

            command_str = task["command"]
            args_str = task["args"] or ""
            full_cmd = f"{Config.PREFIX}{command_str} {args_str}".strip()

            embed = build_embed(
                "⏰ Scheduled Task Executing",
                f"Task ID: `{task['id']}`\nCommand: `{full_cmd}`",
                color=Config.COLOR_INFO,
            )
            await channel.send(embed=embed)
            await channel.send(full_cmd)

            # Handle repeating tasks
            repeat_secs = task["repeat_secs"] or 0
            if repeat_secs > 0:
                next_run = datetime.utcnow() + timedelta(seconds=repeat_secs)
                db.reschedule_task(task["id"], next_run)
            else:
                db.remove_task(task["id"])

            logger.info(f"Executed scheduled task {task['id']}: {full_cmd}")
        except Exception as e:
            logger.error(f"Task execution error {task['id']}: {e}")

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """Parse time string to datetime. Formats: HH:MM, +Nm (in N minutes), +Nh (in N hours)."""
        import re
        time_str = time_str.strip()

        # Relative: +10m, +2h, +30s
        m = re.match(r"^\+(\d+)([smh])$", time_str)
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            delta = {"s": timedelta(seconds=val), "m": timedelta(minutes=val), "h": timedelta(hours=val)}[unit]
            return datetime.utcnow() + delta

        # Absolute: HH:MM or HH:MM:SS
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                t = datetime.strptime(time_str, fmt)
                now = datetime.utcnow()
                run_at = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
                if run_at <= now:
                    run_at += timedelta(days=1)
                return run_at
            except ValueError:
                continue

        # ISO datetime
        try:
            return datetime.fromisoformat(time_str)
        except ValueError:
            return None

    # ─── Commands ─────────────────────────────────────────────────────────────

    @commands.command(name="schedule", aliases=["at", "cron"])
    @admin_only()
    async def schedule_task(self, ctx, time_str: str, command: str, *args):
        """
        Schedule a command. Time formats: +10m, +2h, +30s, 14:30, 2024-01-15T14:30
        Examples:
          !schedule +5m screenshot
          !schedule +1h cpu
          !schedule 14:30 sysinfo
        """
        run_at = self._parse_time(time_str)
        if not run_at:
            await ctx.send(embed=build_embed(
                "Schedule",
                "Invalid time format.\nExamples: `+5m`, `+2h`, `+30s`, `14:30`, `2024-01-15T14:30`",
                color=Config.COLOR_ERROR,
            ))
            return

        # Validate command exists
        cmd = self.bot.get_command(command)
        if not cmd:
            await ctx.send(embed=build_embed("Schedule", f"Unknown command: `{command}`", color=Config.COLOR_ERROR))
            return

        args_str = " ".join(args)
        task_id = db.add_task(run_at, command, ctx.channel.id, args_str)

        embed = build_embed(
            "✅ Task Scheduled",
            f"**Task ID:** `{task_id}`\n"
            f"**Command:** `{Config.PREFIX}{command} {args_str}`\n"
            f"**Run At:** `{run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
            f"**Channel:** <#{ctx.channel.id}>",
            color=Config.COLOR_SUCCESS,
        )
        await ctx.send(embed=embed)

    @commands.command(name="schedulerepeat", aliases=["crontab", "repeat"])
    @admin_only()
    async def schedule_repeat(self, ctx, interval: str, command: str, *args):
        """
        Schedule a repeating command.
        Interval formats: 30s, 5m, 1h
        Example: !schedulerepeat 5m cpu
        """
        import re
        m = re.match(r"^(\d+)([smh])$", interval)
        if not m:
            await ctx.send(embed=build_embed("Repeat", "Invalid interval. Examples: `30s`, `5m`, `2h`", color=Config.COLOR_ERROR))
            return

        val = int(m.group(1))
        unit = m.group(2)
        secs = {"s": val, "m": val * 60, "h": val * 3600}[unit]

        if secs < 60:
            await ctx.send(embed=build_embed("Repeat", "Minimum interval is 60 seconds.", color=Config.COLOR_ERROR))
            return

        cmd = self.bot.get_command(command)
        if not cmd:
            await ctx.send(embed=build_embed("Repeat", f"Unknown command: `{command}`", color=Config.COLOR_ERROR))
            return

        run_at = datetime.utcnow() + timedelta(seconds=secs)
        args_str = " ".join(args)
        task_id = db.add_task(run_at, command, ctx.channel.id, args_str, repeat_secs=secs)

        await ctx.send(embed=build_embed(
            "✅ Repeating Task Scheduled",
            f"**Task ID:** `{task_id}`\n"
            f"**Command:** `{Config.PREFIX}{command} {args_str}`\n"
            f"**Interval:** every `{interval}`\n"
            f"**First Run:** `{run_at.strftime('%H:%M:%S UTC')}`",
            color=Config.COLOR_SUCCESS,
        ))

    @commands.command(name="tasks", aliases=["scheduledtasks", "listtasks"])
    async def list_tasks(self, ctx):
        """List all scheduled tasks."""
        all_tasks = db.get_all_tasks()
        if not all_tasks:
            await ctx.send(embed=build_embed("Tasks", "No scheduled tasks.", color=Config.COLOR_INFO))
            return

        rows = []
        for t in all_tasks:
            repeat = f"every {t['repeat_secs']}s" if t["repeat_secs"] else "once"
            args = f" {t['args']}" if t["args"] else ""
            rows.append(
                f"`[{t['id']}]` `{t['run_at']}` — "
                f"`{Config.PREFIX}{t['command']}{args}` ({repeat})"
            )

        await ctx.send(embed=build_embed(
            f"Scheduled Tasks ({len(all_tasks)})",
            truncate("\n".join(rows), 4000),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="canceltask", aliases=["removetask", "deltask"])
    @admin_only()
    async def cancel_task(self, ctx, task_id: int):
        """Cancel a scheduled task by ID."""
        db.remove_task(task_id)
        await ctx.send(embed=build_embed("Cancel Task", f"✅ Task `{task_id}` cancelled.", color=Config.COLOR_SUCCESS))

    @commands.command(name="cancelall", aliases=["cleartasks"])
    @admin_only()
    async def cancel_all_tasks(self, ctx):
        """Cancel all scheduled tasks."""
        tasks_list = db.get_all_tasks()
        for t in tasks_list:
            db.remove_task(t["id"])
        await ctx.send(embed=build_embed("Cancel All", f"✅ Cancelled {len(tasks_list)} tasks.", color=Config.COLOR_SUCCESS))

    @commands.command(name="remindme", aliases=["remind"])
    async def remind_me(self, ctx, time_str: str, *, message: str):
        """Set a personal reminder. !remindme +10m Do the backups."""
        run_at = self._parse_time(time_str)
        if not run_at:
            await ctx.send(embed=build_embed("Remind Me", "Invalid time format.", color=Config.COLOR_ERROR))
            return

        # Schedule a custom reminder via a bot-internal mechanism
        delay = (run_at - datetime.utcnow()).total_seconds()

        async def _remind():
            await asyncio.sleep(delay)
            embed = build_embed(
                "⏰ Reminder",
                f"{ctx.author.mention}\n\n{message}",
                color=Config.COLOR_INFO,
            )
            await ctx.send(embed=embed)

        asyncio.create_task(_remind())
        await ctx.send(embed=build_embed(
            "✅ Reminder Set",
            f"I'll remind you at `{run_at.strftime('%H:%M:%S UTC')}`\n**Message:** {message}",
            color=Config.COLOR_SUCCESS,
        ))


async def setup(bot):
    await bot.add_cog(Scheduler(bot))
