"""
Network commands — IP, ping, traceroute, DNS, port scan, WiFi, speedtest, netstat.
"""

import asyncio
import platform
import re
import socket
import subprocess
from typing import List, Optional

import discord
import psutil
from discord.ext import commands

from services.database import db
from services.viz_service import network_chart
from utils.config import Config
from utils.helpers import admin_only, build_embed, bytes_to_human, run_in_executor, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.network")


class Network(commands.Cog):
    """Network diagnostics and monitoring."""

    def __init__(self, bot):
        self.bot = bot
        self._net_sent_history: List[float] = []
        self._net_recv_history: List[float] = []
        self._prev_net = psutil.net_io_counters()

    async def _run_cmd(self, *args, timeout: int = 15) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            return "Command timed out."
        except Exception as e:
            return f"Error: {e}"

    @commands.command(name="ip", aliases=["myip", "getip"])
    async def get_ip(self, ctx):
        """Show local and public IP addresses."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "ip")
        async with ctx.typing():
            # Local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            except Exception:
                local_ip = "N/A"
            finally:
                s.close()

            # Hostname
            hostname = socket.gethostname()

            # All interfaces
            ifaces = []
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ifaces.append(f"`{iface}`: `{addr.address}` (mask: `{addr.netmask}`)")

            fields = [
                ("🏠 Hostname", hostname, True),
                ("📍 Primary IP", local_ip, True),
                ("🔌 All Interfaces", truncate("\n".join(ifaces), 1024), False),
            ]
            await ctx.send(embed=build_embed("IP Addresses", color=Config.COLOR_INFO, fields=fields))

    @commands.command(name="ping")
    async def ping(self, ctx, host: str = "8.8.8.8", count: int = 4):
        """Ping a host."""
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "ping", host)
        async with ctx.typing():
            count = min(count, 10)
            if platform.system() == "Windows":
                cmd = ["ping", "-n", str(count), host]
            else:
                cmd = ["ping", "-c", str(count), host]

            output = await self._run_cmd(*cmd, timeout=30)
            embed = build_embed(
                f"Ping → {host}",
                f"```\n{truncate(output, 1900)}\n```",
                color=Config.COLOR_INFO,
            )
            await ctx.send(embed=embed)

    @commands.command(name="traceroute", aliases=["tracert", "trace"])
    async def traceroute(self, ctx, host: str):
        """Traceroute to a host."""
        async with ctx.typing():
            if platform.system() == "Windows":
                cmd = ["tracert", "-h", "20", host]
            else:
                cmd = ["traceroute", "-m", "20", host]
            output = await self._run_cmd(*cmd, timeout=60)
            embed = build_embed(
                f"Traceroute → {host}",
                f"```\n{truncate(output, 1900)}\n```",
                color=Config.COLOR_INFO,
            )
            await ctx.send(embed=embed)

    @commands.command(name="dns", aliases=["dnslookup", "nslookup"])
    async def dns_lookup(self, ctx, host: str):
        """DNS lookup for a hostname."""
        async with ctx.typing():
            try:
                results = socket.getaddrinfo(host, None)
                ips = list({r[4][0] for r in results})
                fqdn = socket.getfqdn(host)
                fields = [
                    ("FQDN", fqdn, False),
                    ("Resolved IPs", "\n".join(f"`{ip}`" for ip in ips), False),
                ]
                await ctx.send(embed=build_embed(f"DNS: {host}", color=Config.COLOR_INFO, fields=fields))
            except socket.gaierror as e:
                await ctx.send(embed=build_embed("DNS", f"Failed: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="netstat", aliases=["connections", "ports"])
    async def netstat(self, ctx, kind: str = "inet"):
        """Show active network connections."""
        async with ctx.typing():
            try:
                connections = psutil.net_connections(kind=kind)
                rows = [f"`{'PROTO':>5}` `{'LOCAL':>22}` `{'REMOTE':>22}` `{'STATUS':>12}` `PID`"]
                for conn in connections[:30]:
                    laddr = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "N/A"
                    raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A"
                    rows.append(
                        f"`{conn.type.name:>5}` `{laddr:>22}` `{raddr:>22}` "
                        f"`{conn.status:>12}` `{conn.pid or 'N/A'}`"
                    )
                embed = build_embed(
                    f"Network Connections ({kind})",
                    truncate("\n".join(rows), 4000),
                    color=Config.COLOR_INFO,
                )
                embed.set_footer(text=f"Showing up to 30 of {len(connections)} connections")
                await ctx.send(embed=embed)
            except psutil.AccessDenied:
                await ctx.send(embed=build_embed("Netstat", "Access denied — run as admin.", color=Config.COLOR_ERROR))

    @commands.command(name="networkstats", aliases=["netstats", "netio"])
    async def network_stats(self, ctx):
        """Network I/O statistics per interface."""
        stats = psutil.net_io_counters(pernic=True)
        rows = []
        for iface, s in stats.items():
            rows.append(
                f"**{iface}**: ↑ {bytes_to_human(s.bytes_sent)} / "
                f"↓ {bytes_to_human(s.bytes_recv)} | "
                f"pkts ↑{s.packets_sent} ↓{s.packets_recv} | "
                f"err ↑{s.errin} ↓{s.errout}"
            )
        total = psutil.net_io_counters()
        fields = [
            ("📊 Per Interface", truncate("\n".join(rows), 1024), False),
            ("📈 Total Sent", bytes_to_human(total.bytes_sent), True),
            ("📉 Total Recv", bytes_to_human(total.bytes_recv), True),
        ]
        await ctx.send(embed=build_embed("Network Statistics", color=Config.COLOR_INFO, fields=fields))

    @commands.command(name="netchart", aliases=["netgraph"])
    async def network_chart_cmd(self, ctx):
        """Network I/O history chart."""
        if not self._net_sent_history:
            await ctx.send(embed=build_embed("Network Chart", "Not enough history. Run `!dashboard` first.", color=Config.COLOR_WARNING))
            return
        async with ctx.typing():
            buf = await run_in_executor(network_chart, self._net_sent_history, self._net_recv_history)
            await ctx.send(
                embed=build_embed("Network I/O History", color=Config.COLOR_INFO),
                file=discord.File(buf, "network.png"),
            )

    @commands.command(name="wifi", aliases=["wifiinfo", "wireless"])
    async def wifi_info(self, ctx):
        """WiFi information."""
        async with ctx.typing():
            if platform.system() == "Windows":
                output = await self._run_cmd("netsh", "wlan", "show", "interfaces")
            elif platform.system() == "Darwin":
                output = await self._run_cmd("/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I")
            else:
                output = await self._run_cmd("iwconfig")

            await ctx.send(embed=build_embed(
                "WiFi Information",
                f"```\n{truncate(output, 1900)}\n```",
                color=Config.COLOR_INFO,
            ))

    @commands.command(name="mac", aliases=["macaddr"])
    async def get_mac(self, ctx):
        """Show MAC addresses for all interfaces."""
        addrs = psutil.net_if_addrs()
        rows = []
        for iface, addr_list in addrs.items():
            for addr in addr_list:
                if addr.family == psutil.AF_LINK:
                    rows.append(f"`{iface}`: `{addr.address}`")
        await ctx.send(embed=build_embed(
            "MAC Addresses",
            "\n".join(rows) or "No MAC addresses found.",
            color=Config.COLOR_INFO,
        ))

    @commands.command(name="scanports", aliases=["portscan"])
    @admin_only()
    async def scan_ports(self, ctx, host: str, start: int = 1, end: int = 1024):
        """Scan open TCP ports on a host. Range max: 1024 ports."""
        end = min(end, start + 1023)  # Safety cap
        async with ctx.typing():
            open_ports = []

            async def check_port(port):
                try:
                    conn = await asyncio.wait_for(
                        asyncio.open_connection(host, port), timeout=0.5
                    )
                    conn[1].close()
                    open_ports.append(port)
                except Exception:
                    pass

            tasks = [check_port(p) for p in range(start, end + 1)]
            await asyncio.gather(*tasks)
            open_ports.sort()

            if not open_ports:
                desc = f"No open ports found in range {start}-{end}."
            else:
                desc = f"Open ports: `{', '.join(map(str, open_ports[:50]))}`"

            await ctx.send(embed=build_embed(
                f"Port Scan: {host} ({start}-{end})",
                desc,
                color=Config.COLOR_INFO if open_ports else Config.COLOR_WARNING,
            ))

    @commands.command(name="speedtest")
    async def speedtest_cmd(self, ctx):
        """Run a network speed test (requires speedtest-cli)."""
        async with ctx.typing():
            try:
                import speedtest as st
                def _run():
                    s = st.Speedtest()
                    s.get_best_server()
                    s.download()
                    s.upload()
                    return s.results.dict()

                results = await run_in_executor(_run)
                dl = results["download"] / 1e6
                ul = results["upload"] / 1e6
                ping = results["ping"]
                server = results["server"]

                fields = [
                    ("⬇️ Download", f"{dl:.2f} Mbps", True),
                    ("⬆️ Upload", f"{ul:.2f} Mbps", True),
                    ("📡 Ping", f"{ping:.2f} ms", True),
                    ("🌐 Server", f"{server['name']}, {server['country']}", False),
                ]
                await ctx.send(embed=build_embed("Speed Test", color=Config.COLOR_SUCCESS, fields=fields))
            except ImportError:
                await ctx.send(embed=build_embed("Speedtest", "Install speedtest-cli: `pip install speedtest-cli`", color=Config.COLOR_WARNING))
            except Exception as e:
                await ctx.send(embed=build_embed("Speedtest", f"Error: {e}", color=Config.COLOR_ERROR))


async def setup(bot):
    await bot.add_cog(Network(bot))
