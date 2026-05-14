"""
Custom help system — paginated, categorized, searchable.
"""

import math
from typing import List, Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.help")

COG_ICONS = {
    "System": "🖥️",
    "Processes": "⚙️",
    "Files": "📁",
    "Network": "🌐",
    "Display": "🖼️",
    "Audio": "🔊",
    "Automation": "🤖",
    "Monitoring": "📊",
    "Power": "⚡",
    "Registry": "🗝️",
    "Security": "🛡️",
    "Voice": "🎙️",
    "Visualizations": "📈",
    "Scheduler": "⏰",
    "Clipboard": "📋",
    "Remote": "📡",
    "Help": "❓",
}

ITEMS_PER_PAGE = 8


class HelpPaginator(discord.ui.View):
    def __init__(self, pages: List[discord.Embed], author_id: int):
        super().__init__(timeout=120)
        self.pages = pages
        self.current = 0
        self.author_id = author_id
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current >= len(self.pages) - 1
        self.page_label.label = f"{self.current + 1}/{len(self.pages)}"

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.defer()
            return
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.defer()
            return
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Help(commands.Cog):
    """Paginated help system."""

    def __init__(self, bot):
        self.bot = bot

    def _get_cog_commands(self, cog_name: str) -> List[commands.Command]:
        cog = self.bot.get_cog(cog_name)
        if not cog:
            return []
        return [cmd for cmd in cog.get_commands() if not cmd.hidden]

    def _build_cog_pages(self) -> List[discord.Embed]:
        pages = []

        # Overview page
        overview = discord.Embed(
            title="🤖 PC Agent — Command Reference",
            description=(
                "**PC control via Discord**\n\n"
                "Use the buttons below to browse commands by category.\n"
                f"Prefix: `{Config.PREFIX}` | Natural language: `{Config.PREFIX}ask <anything>`\n\n"
                "**Quick Start:**\n"
                f"• `{Config.PREFIX}sysinfo` — System overview\n"
                f"• `{Config.PREFIX}dashboard` — Full visual dashboard\n"
                f"• `{Config.PREFIX}ask what is my CPU usage` — Natural language\n"
                f"• `{Config.PREFIX}startmonitor` — Enable alerts\n"
                f"• `{Config.PREFIX}startvoice` — Voice commands\n"
            ),
            color=Config.COLOR_SYSTEM,
        )
        overview.set_footer(text="PC Agent | Use !help <command> for details")

        # Category summary
        cats = []
        for cog_name, icon in COG_ICONS.items():
            cog = self.bot.get_cog(cog_name)
            if cog:
                count = len([c for c in cog.get_commands() if not c.hidden])
                cats.append(f"{icon} **{cog_name}** — {count} commands")
        overview.add_field(name="Categories", value="\n".join(cats), inline=False)
        pages.append(overview)

        # Per-cog pages
        for cog_name, icon in COG_ICONS.items():
            cog = self.bot.get_cog(cog_name)
            if not cog:
                continue
            cmds = [c for c in cog.get_commands() if not c.hidden]
            if not cmds:
                continue

            # Paginate if many commands
            chunks = [cmds[i:i+ITEMS_PER_PAGE] for i in range(0, len(cmds), ITEMS_PER_PAGE)]
            for chunk_idx, chunk in enumerate(chunks):
                embed = discord.Embed(
                    title=f"{icon} {cog_name}",
                    description=cog.description or "",
                    color=Config.COLOR_INFO,
                )
                for cmd in chunk:
                    aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                    usage = f"`{Config.PREFIX}{cmd.name}{aliases}`"
                    desc = cmd.help or cmd.brief or "No description."
                    embed.add_field(
                        name=usage,
                        value=truncate(desc.split("\n")[0], 100),
                        inline=False,
                    )
                if len(chunks) > 1:
                    embed.set_footer(text=f"{cog_name} page {chunk_idx+1}/{len(chunks)}")
                pages.append(embed)

        return pages

    @commands.command(name="help", aliases=["h", "commands"])
    async def help_cmd(self, ctx, *, query: str = None):
        """Browse all commands with pagination, or get details for a specific command."""
        if query:
            # Specific command lookup
            cmd = self.bot.get_command(query.lower())
            if cmd:
                aliases = ", ".join(f"`{a}`" for a in cmd.aliases) if cmd.aliases else "None"
                fields = [
                    ("Usage", f"`{Config.PREFIX}{cmd.name} {cmd.signature}`", False),
                    ("Aliases", aliases, True),
                    ("Module", cmd.cog_name or "N/A", True),
                ]
                embed = build_embed(
                    f"Command: {Config.PREFIX}{cmd.name}",
                    cmd.help or "No description.",
                    color=Config.COLOR_INFO,
                    fields=fields,
                )
                await ctx.send(embed=embed)
            else:
                # Search
                matches = [c for c in self.bot.commands
                           if query.lower() in c.name.lower() or
                           any(query.lower() in a for a in c.aliases)]
                if matches:
                    rows = [f"`{Config.PREFIX}{c.name}` — {(c.help or '').split(chr(10))[0][:80]}"
                            for c in matches[:15]]
                    await ctx.send(embed=build_embed(
                        f"Search: {query}",
                        "\n".join(rows),
                        color=Config.COLOR_INFO,
                    ))
                else:
                    await ctx.send(embed=build_embed("Help", f"No command found: `{query}`", color=Config.COLOR_WARNING))
            return

        pages = self._build_cog_pages()
        view = HelpPaginator(pages, ctx.author.id)
        await ctx.send(embed=pages[0], view=view)

    @commands.command(name="cmdcount", aliases=["totalcmds"])
    async def cmd_count(self, ctx):
        """Show total number of registered commands."""
        total = len([c for c in self.bot.commands if not c.hidden])
        by_cog = {}
        for cmd in self.bot.commands:
            if not cmd.hidden:
                cog = cmd.cog_name or "Uncategorized"
                by_cog[cog] = by_cog.get(cog, 0) + 1
        rows = [f"`{cog}`: {count}" for cog, count in sorted(by_cog.items(), key=lambda x: -x[1])]
        await ctx.send(embed=build_embed(
            f"📊 Total Commands: {total}",
            "\n".join(rows),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="version", aliases=["about", "botinfo"])
    async def version_info(self, ctx):
        """Bot version and information."""
        import platform, discord as dpy
        from utils.helpers import seconds_to_human
        import time

        uptime = seconds_to_human(
            (time.time() - self.bot.startup_time.timestamp())
            if self.bot.startup_time else 0
        )

        fields = [
            ("Version", "2.0.0", True),
            ("Discord.py", dpy.__version__, True),
            ("Python", platform.python_version(), True),
            ("Platform", platform.system(), True),
            ("Guilds", str(len(self.bot.guilds)), True),
            ("Uptime", uptime, True),
            ("Commands", str(len(self.bot.commands)), True),
            ("Cogs", str(len(self.bot.cogs)), True),
            ("NLP", "Groq ✅" if Config.GROQ_API_KEY else "⚠️ Not configured", True),
        ]
        embed = build_embed(
            "🤖 PC Agent",
            color=Config.COLOR_SYSTEM,
            fields=fields,
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Help(bot))
