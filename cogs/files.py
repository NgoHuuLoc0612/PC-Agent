"""
File system management — list, read, copy, move, delete, rename, find, zip, info.
"""

import hashlib
import mimetypes
import os
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, bytes_to_human, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.files")


def _resolve_safe(path: str) -> Path:
    """Resolve path and check it's within allowed paths or common safe dirs."""
    p = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
    return p


class Files(commands.Cog):
    """File system operations."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ls", aliases=["dir", "listdir"])
    async def list_dir(self, ctx, path: str = "."):
        """List directory contents."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "ls", path)
        try:
            p = _resolve_safe(path)
            if not p.exists():
                await ctx.send(embed=build_embed("List Dir", f"Path not found: `{path}`", color=Config.COLOR_ERROR))
                return
            if not p.is_dir():
                await ctx.send(embed=build_embed("List Dir", f"`{path}` is not a directory.", color=Config.COLOR_ERROR))
                return

            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            rows = []
            for entry in entries[:50]:
                try:
                    stat_info = entry.stat()
                    size = bytes_to_human(stat_info.st_size) if entry.is_file() else ""
                    icon = "📁" if entry.is_dir() else "📄"
                    mtime = datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M")
                    rows.append(f"{icon} `{entry.name:<40}` {size:<10} `{mtime}`")
                except PermissionError:
                    rows.append(f"🔒 `{entry.name}` (access denied)")

            embed = build_embed(
                f"📂 {p}",
                truncate("\n".join(rows), 4000) or "Empty directory",
                color=Config.COLOR_INFO,
            )
            embed.set_footer(text=f"Showing up to 50 of {len(list(p.iterdir()))} entries")
            await ctx.send(embed=embed)
        except PermissionError:
            await ctx.send(embed=build_embed("List Dir", "Access denied.", color=Config.COLOR_ERROR))
        except Exception as e:
            await ctx.send(embed=build_embed("List Dir", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="readfile", aliases=["cat", "view"])
    async def read_file(self, ctx, path: str, lines: int = 50):
        """Read a text file (first N lines)."""
        async with ctx.typing():
            try:
                p = _resolve_safe(path)
                if not p.is_file():
                    await ctx.send(embed=build_embed("Read File", f"File not found: `{path}`", color=Config.COLOR_ERROR))
                    return

                size = p.stat().st_size
                if size > 5 * 1024 * 1024:
                    await ctx.send(embed=build_embed("Read File", "File too large (>5MB). Use download instead.", color=Config.COLOR_WARNING))
                    return

                content = p.read_text(encoding="utf-8", errors="replace")
                file_lines = content.splitlines()[:lines]
                preview = "\n".join(file_lines)

                embed = build_embed(
                    f"📄 {p.name}",
                    f"```\n{truncate(preview, 1900)}\n```",
                    color=Config.COLOR_INFO,
                )
                embed.add_field(name="Size", value=bytes_to_human(size), inline=True)
                embed.add_field(name="Lines (shown)", value=str(len(file_lines)), inline=True)
                await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(embed=build_embed("Read File", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="download", aliases=["getfile"])
    async def download_file(self, ctx, path: str):
        """Send a file to Discord."""
        async with ctx.typing():
            try:
                p = _resolve_safe(path)
                if not p.is_file():
                    await ctx.send(embed=build_embed("Download", f"File not found: `{path}`", color=Config.COLOR_ERROR))
                    return
                size = p.stat().st_size
                if size > Config.MAX_FILE_SIZE:
                    await ctx.send(embed=build_embed("Download", f"File too large: {bytes_to_human(size)} > {bytes_to_human(Config.MAX_FILE_SIZE)}", color=Config.COLOR_ERROR))
                    return
                await ctx.send(file=discord.File(str(p), filename=p.name))
            except PermissionError:
                await ctx.send(embed=build_embed("Download", "Access denied.", color=Config.COLOR_ERROR))
            except Exception as e:
                await ctx.send(embed=build_embed("Download", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="upload", aliases=["putfile"])
    @admin_only()
    async def upload_file(self, ctx, destination: str):
        """Upload an attached file to the PC."""
        if not ctx.message.attachments:
            await ctx.send(embed=build_embed("Upload", "No attachment found. Attach a file.", color=Config.COLOR_ERROR))
            return
        async with ctx.typing():
            attachment = ctx.message.attachments[0]
            dest = _resolve_safe(destination)
            if dest.is_dir():
                dest = dest / attachment.filename
            try:
                await attachment.save(str(dest))
                await ctx.send(embed=build_embed("Upload", f"✅ Saved to `{dest}`", color=Config.COLOR_SUCCESS))
            except Exception as e:
                await ctx.send(embed=build_embed("Upload", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="deletefile", aliases=["rm", "del"])
    @admin_only()
    async def delete_file(self, ctx, path: str):
        """Delete a file or empty directory."""
        try:
            p = _resolve_safe(path)
            if not p.exists():
                await ctx.send(embed=build_embed("Delete", f"Not found: `{path}`", color=Config.COLOR_ERROR))
                return

            # Confirm via reaction
            confirm_embed = build_embed("Delete Confirmation",
                                        f"⚠️ Delete `{p}`?\nReact ✅ to confirm, ❌ to cancel.",
                                        color=Config.COLOR_WARNING)
            msg = await ctx.send(embed=confirm_embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id

            try:
                reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            except Exception:
                await ctx.send(embed=build_embed("Delete", "Timed out — cancelled.", color=Config.COLOR_WARNING))
                return

            if str(reaction.emoji) == "✅":
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(str(p))
                await ctx.send(embed=build_embed("Delete", f"✅ Deleted `{p}`", color=Config.COLOR_SUCCESS))
            else:
                await ctx.send(embed=build_embed("Delete", "Cancelled.", color=Config.COLOR_INFO))
        except PermissionError:
            await ctx.send(embed=build_embed("Delete", "Access denied.", color=Config.COLOR_ERROR))
        except Exception as e:
            await ctx.send(embed=build_embed("Delete", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="rename", aliases=["mv", "move"])
    @admin_only()
    async def rename_file(self, ctx, src: str, dest: str):
        """Rename or move a file/directory."""
        try:
            s = _resolve_safe(src)
            d = _resolve_safe(dest)
            if not s.exists():
                await ctx.send(embed=build_embed("Rename/Move", f"Source not found: `{src}`", color=Config.COLOR_ERROR))
                return
            shutil.move(str(s), str(d))
            await ctx.send(embed=build_embed("Rename/Move", f"✅ `{s}` → `{d}`", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Rename/Move", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="copy", aliases=["cp"])
    @admin_only()
    async def copy_file(self, ctx, src: str, dest: str):
        """Copy a file or directory."""
        try:
            s = _resolve_safe(src)
            d = _resolve_safe(dest)
            if not s.exists():
                await ctx.send(embed=build_embed("Copy", f"Source not found: `{src}`", color=Config.COLOR_ERROR))
                return
            if s.is_file():
                shutil.copy2(str(s), str(d))
            else:
                shutil.copytree(str(s), str(d))
            await ctx.send(embed=build_embed("Copy", f"✅ Copied `{s}` → `{d}`", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Copy", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="find", aliases=["search", "locate"])
    async def find_files(self, ctx, root: str, *, pattern: str):
        """Find files matching a pattern under a root directory."""
        async with ctx.typing():
            try:
                root_p = _resolve_safe(root)
                matches = list(root_p.rglob(pattern))[:30]
                if not matches:
                    await ctx.send(embed=build_embed("Find", f"No matches for `{pattern}` in `{root}`", color=Config.COLOR_WARNING))
                    return
                rows = [f"{'📁' if m.is_dir() else '📄'} `{m}`" for m in matches]
                await ctx.send(embed=build_embed(
                    f"Find: {pattern}",
                    truncate("\n".join(rows), 4000),
                    color=Config.COLOR_INFO,
                    fields=[("Matches", str(len(matches)), True)],
                ))
            except Exception as e:
                await ctx.send(embed=build_embed("Find", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="fileinfo", aliases=["stat", "finfo"])
    async def file_info(self, ctx, path: str):
        """Detailed file/directory information."""
        try:
            p = _resolve_safe(path)
            if not p.exists():
                await ctx.send(embed=build_embed("File Info", f"Not found: `{path}`", color=Config.COLOR_ERROR))
                return

            st = p.stat()
            mime = mimetypes.guess_type(str(p))[0] or "N/A"

            # MD5 for files < 50MB
            md5 = "N/A"
            if p.is_file() and st.st_size < 50 * 1024 * 1024:
                def _hash():
                    h = hashlib.md5()
                    with open(p, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            h.update(chunk)
                    return h.hexdigest()
                md5 = await run_in_executor(_hash)

            fields = [
                ("Type", "Directory" if p.is_dir() else "File", True),
                ("Size", bytes_to_human(st.st_size), True),
                ("MIME", mime, True),
                ("Created", datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S"), True),
                ("Modified", datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"), True),
                ("Accessed", datetime.fromtimestamp(st.st_atime).strftime("%Y-%m-%d %H:%M:%S"), True),
                ("Permissions", oct(stat.S_IMODE(st.st_mode)), True),
                ("MD5", md5, False),
            ]
            await ctx.send(embed=build_embed(f"Info: {p.name}", color=Config.COLOR_INFO, fields=fields))
        except Exception as e:
            await ctx.send(embed=build_embed("File Info", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="zip")
    @admin_only()
    async def zip_files(self, ctx, output: str, *paths: str):
        """Zip files/directories into an archive."""
        async with ctx.typing():
            try:
                out = _resolve_safe(output)
                with zipfile.ZipFile(str(out), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for path in paths:
                        p = _resolve_safe(path)
                        if p.is_file():
                            zf.write(str(p), p.name)
                        elif p.is_dir():
                            for f in p.rglob("*"):
                                if f.is_file():
                                    zf.write(str(f), str(f.relative_to(p.parent)))
                size = out.stat().st_size
                await ctx.send(embed=build_embed("Zip", f"✅ Created `{out}` ({bytes_to_human(size)})", color=Config.COLOR_SUCCESS))
            except Exception as e:
                await ctx.send(embed=build_embed("Zip", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="unzip", aliases=["extract"])
    @admin_only()
    async def unzip_files(self, ctx, archive: str, destination: str = "."):
        """Extract a zip archive."""
        async with ctx.typing():
            try:
                src = _resolve_safe(archive)
                dest = _resolve_safe(destination)
                dest.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(str(src), "r") as zf:
                    zf.extractall(str(dest))
                    names = zf.namelist()
                await ctx.send(embed=build_embed(
                    "Unzip",
                    f"✅ Extracted {len(names)} files to `{dest}`",
                    color=Config.COLOR_SUCCESS,
                ))
            except Exception as e:
                await ctx.send(embed=build_embed("Unzip", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="mkdir")
    @admin_only()
    async def make_dir(self, ctx, path: str):
        """Create a directory (including parents)."""
        try:
            p = _resolve_safe(path)
            p.mkdir(parents=True, exist_ok=True)
            await ctx.send(embed=build_embed("Mkdir", f"✅ Created `{p}`", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Mkdir", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="diskusage", aliases=["du"])
    async def disk_usage_dir(self, ctx, path: str = "."):
        """Calculate total disk usage of a directory."""
        async with ctx.typing():
            try:
                p = _resolve_safe(path)
                def _calc():
                    total = 0
                    count = 0
                    for f in p.rglob("*"):
                        try:
                            if f.is_file():
                                total += f.stat().st_size
                                count += 1
                        except Exception:
                            pass
                    return total, count
                total, count = await run_in_executor(_calc)
                await ctx.send(embed=build_embed(
                    f"Disk Usage: {p.name}",
                    f"**{bytes_to_human(total)}** across **{count:,}** files",
                    color=Config.COLOR_INFO,
                ))
            except Exception as e:
                await ctx.send(embed=build_embed("Disk Usage", f"Error: {e}", color=Config.COLOR_ERROR))


async def setup(bot):
    await bot.add_cog(Files(bot))
