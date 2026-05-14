"""
Security cog — firewall, startup programs, open ports, AV status, audit log.
"""

import platform
import subprocess
from typing import Optional

from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.security")


class Security(commands.Cog):
    """Security and system hardening tools."""

    def __init__(self, bot):
        self.bot = bot

    async def _run(self, *cmd, timeout: int = 15) -> str:
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode(errors="replace") or stderr.decode(errors="replace")
        except Exception as e:
            return f"Error: {e}"

    @commands.command(name="firewall", aliases=["fwrules", "firewallstatus"])
    @admin_only()
    async def firewall_status(self, ctx):
        """List firewall rules/status."""
        async with ctx.typing():
            if platform.system() == "Windows":
                output = await self._run("netsh", "advfirewall", "show", "allprofiles")
            elif platform.system() == "Linux":
                output = await self._run("sudo", "ufw", "status", "verbose")
                if "Error" in output:
                    output = await self._run("sudo", "iptables", "-L", "-n", "--line-numbers")
            elif platform.system() == "Darwin":
                output = await self._run("sudo", "pfctl", "-sr")
            else:
                output = "Unsupported platform"

            await ctx.send(embed=build_embed(
                "🔥 Firewall Status",
                f"```\n{truncate(output, 1900)}\n```",
                color=Config.COLOR_INFO,
            ))

    @commands.command(name="startup", aliases=["startupreg", "startupprograms"])
    async def list_startup(self, ctx):
        """List startup programs."""
        async with ctx.typing():
            if platform.system() == "Windows":
                output = await self._run(
                    "reg", "query",
                    r"HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
                )
                output2 = await self._run(
                    "reg", "query",
                    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
                )
                full = f"[HKCU]\n{output}\n[HKLM]\n{output2}"
            elif platform.system() == "Linux":
                import os
                autostart = os.path.expanduser("~/.config/autostart")
                full = "\n".join(os.listdir(autostart)) if os.path.exists(autostart) else "No autostart directory found"
            elif platform.system() == "Darwin":
                full = await self._run("launchctl", "list")
            else:
                full = "Unsupported platform"

            await ctx.send(embed=build_embed(
                "🚀 Startup Programs",
                f"```\n{truncate(full, 1900)}\n```",
                color=Config.COLOR_INFO,
            ))

    @commands.command(name="antivirus", aliases=["avstatus", "checksecurity"])
    async def antivirus_status(self, ctx):
        """Check antivirus/security software status."""
        async with ctx.typing():
            if platform.system() == "Windows":
                output = await self._run(
                    "powershell", "-command",
                    "Get-MpComputerStatus | Select-Object -Property "
                    "AMServiceEnabled, AntispywareEnabled, AntivirusEnabled, "
                    "RealTimeProtectionEnabled, OnAccessProtectionEnabled | Format-List"
                )
            elif platform.system() == "Linux":
                output = await self._run("clamscan", "--version")
                if "Error" in output:
                    output = "ClamAV not found. Other AV tools may be installed."
            else:
                output = "AV check not available on this platform."

            await ctx.send(embed=build_embed(
                "🛡️ Security Status",
                f"```\n{truncate(output, 1900)}\n```",
                color=Config.COLOR_INFO,
            ))

    @commands.command(name="auditlog", aliases=["audit", "cmdlog"])
    @admin_only()
    async def audit_log(self, ctx, limit: int = 20):
        """Show recent command audit log."""
        rows_data = db.get_audit_log(limit)
        if not rows_data:
            await ctx.send(embed=build_embed("Audit Log", "No logs found.", color=Config.COLOR_INFO))
            return

        rows = []
        for r in rows_data:
            status = "✅" if r["success"] else "❌"
            args = f" `{r['args']}`" if r["args"] else ""
            rows.append(f"{status} `{r['ts']}` **{r['username']}** → `{r['command']}`{args}")

        await ctx.send(embed=build_embed(
            f"📋 Audit Log (last {limit})",
            truncate("\n".join(rows), 4000),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="commandstats", aliases=["cmdstats"])
    async def command_stats(self, ctx, limit: int = 15):
        """Show most used commands."""
        stats = db.get_command_stats(limit)
        if not stats:
            await ctx.send(embed=build_embed("Stats", "No stats yet.", color=Config.COLOR_INFO))
            return

        rows = [f"`{i+1:>2}.` `{r['command']:<25}` × {r['invocations']}" for i, r in enumerate(stats)]
        await ctx.send(embed=build_embed(
            f"📊 Top {limit} Commands",
            "\n".join(rows),
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="whoami")
    async def whoami(self, ctx):
        """Show current OS user and privileges."""
        import os, getpass
        try:
            username = getpass.getuser()
            uid = os.getuid() if hasattr(os, "getuid") else "N/A"
            gid = os.getgid() if hasattr(os, "getgid") else "N/A"
            is_admin = (uid == 0) if uid != "N/A" else False

            if platform.system() == "Windows":
                output = await self._run("whoami", "/all")
                is_admin = "administrators" in output.lower()
            else:
                output = None

            fields = [
                ("Username", username, True),
                ("UID", str(uid), True),
                ("GID", str(gid), True),
                ("Admin/Root", "✅ Yes" if is_admin else "❌ No", True),
            ]
            await ctx.send(embed=build_embed("👤 Current User", color=Config.COLOR_INFO, fields=fields))
        except Exception as e:
            await ctx.send(embed=build_embed("Whoami", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="netfirewall", aliases=["blockip"])
    @admin_only()
    async def block_ip(self, ctx, ip: str, action: str = "block"):
        """Block or unblock an IP address via firewall. action: block|unblock"""
        async with ctx.typing():
            try:
                if platform.system() == "Windows":
                    if action == "block":
                        cmd = ["netsh", "advfirewall", "firewall", "add", "rule",
                               f"name=BLOCK_{ip}", "dir=in", "action=block", f"remoteip={ip}"]
                    else:
                        cmd = ["netsh", "advfirewall", "firewall", "delete", "rule", f"name=BLOCK_{ip}"]
                elif platform.system() == "Linux":
                    if action == "block":
                        cmd = ["sudo", "iptables", "-I", "INPUT", "-s", ip, "-j", "DROP"]
                    else:
                        cmd = ["sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"]
                else:
                    await ctx.send(embed=build_embed("Firewall", "Not supported on this platform.", color=Config.COLOR_WARNING))
                    return

                output = await self._run(*cmd)
                color = Config.COLOR_SUCCESS if action == "block" else Config.COLOR_WARNING
                await ctx.send(embed=build_embed(
                    "Firewall",
                    f"{'🚫 Blocked' if action == 'block' else '✅ Unblocked'} `{ip}`\n```\n{truncate(output, 300)}\n```",
                    color=color,
                ))
            except Exception as e:
                await ctx.send(embed=build_embed("Firewall", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="sshkeys", aliases=["listsshkeys"])
    async def list_ssh_keys(self, ctx):
        """List SSH authorized keys."""
        import os
        key_file = os.path.expanduser("~/.ssh/authorized_keys")
        if not os.path.exists(key_file):
            await ctx.send(embed=build_embed("SSH Keys", "No authorized_keys file found.", color=Config.COLOR_WARNING))
            return
        content = open(key_file).read()
        lines = [l for l in content.splitlines() if l.strip() and not l.startswith("#")]
        rows = [f"`{i+1}.` `{truncate(l, 60)}`" for i, l in enumerate(lines)]
        await ctx.send(embed=build_embed(
            f"🔑 SSH Keys ({len(lines)})",
            "\n".join(rows) or "No keys found.",
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="checkfile", aliases=["viruscheck", "hashcheck"])
    async def check_file_hash(self, ctx, path: str):
        """Compute MD5/SHA256 of a file for integrity verification."""
        import hashlib, os
        try:
            p = os.path.expanduser(path)
            if not os.path.isfile(p):
                await ctx.send(embed=build_embed("Check File", f"File not found: `{path}`", color=Config.COLOR_ERROR))
                return

            def _hash():
                md5 = hashlib.md5()
                sha256 = hashlib.sha256()
                sha1 = hashlib.sha1()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        md5.update(chunk)
                        sha256.update(chunk)
                        sha1.update(chunk)
                return md5.hexdigest(), sha256.hexdigest(), sha1.hexdigest()

            from utils.helpers import run_in_executor
            md5, sha256, sha1 = await run_in_executor(_hash)

            fields = [
                ("MD5", f"`{md5}`", False),
                ("SHA1", f"`{sha1}`", False),
                ("SHA256", f"`{sha256}`", False),
            ]
            await ctx.send(embed=build_embed(f"File Hashes: {os.path.basename(p)}", color=Config.COLOR_INFO, fields=fields))
        except Exception as e:
            await ctx.send(embed=build_embed("Check File", f"Error: {e}", color=Config.COLOR_ERROR))


async def setup(bot):
    await bot.add_cog(Security(bot))
