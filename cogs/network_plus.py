"""
Network Plus cog — enhanced network: speed test, firewall rules,
WiFi management, bandwidth monitor, DNS, hosts file, port scanner.
Windows-focused. Requires: speedtest-cli (pip install speedtest-cli)
"""

import asyncio
import ipaddress
import socket
from typing import Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.network_plus")


async def _ps(script: str, timeout: int = 20) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
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


class NetworkPlus(commands.Cog, name="NetworkPlus"):
    """Enhanced network tools — speed, firewall, WiFi, DNS, ports."""

    def __init__(self, bot):
        self.bot = bot

    # ── Speed Test ───────────────────────────────────────────

    @commands.command(name="netspeed", aliases=["ispeedtest", "internetspeed"])
    async def net_speed(self, ctx):
        """Test internet download/upload speed."""
        msg = await ctx.send(embed=build_embed(
            "🌐 Speed Test",
            "Testing... this may take 15-30 seconds.",
            color=Config.COLOR_INFO
        ))
        async with ctx.typing():
            loop = asyncio.get_event_loop()
            try:
                import speedtest
                def run_test():
                    st = speedtest.Speedtest(secure=True)
                    st.get_best_server()
                    st.download()
                    st.upload()
                    return st.results.dict()

                results = await loop.run_in_executor(None, run_test)
                dl = results["download"] / 1_000_000
                ul = results["upload"] / 1_000_000
                ping = results["ping"]
                server = results["server"]

                embed = build_embed(
                    "🌐 Speed Test Results",
                    "",
                    color=Config.COLOR_SUCCESS,
                    fields=[
                        ("⬇️ Download", f"**{dl:.1f} Mbps**", True),
                        ("⬆️ Upload", f"**{ul:.1f} Mbps**", True),
                        ("📶 Ping", f"**{ping:.0f} ms**", True),
                        ("🌍 Server", f"{server['name']}, {server['country']}", True),
                        ("🏢 ISP", results.get("client", {}).get("isp", "N/A"), True),
                    ]
                )
                await msg.edit(embed=embed)

            except ImportError:
                await msg.edit(embed=build_embed(
                    "❌ speedtest-cli not installed",
                    "Run: `pip install speedtest-cli`",
                    color=Config.COLOR_ERROR
                ))
            except Exception as e:
                await msg.edit(embed=build_embed("❌ Speed Test Failed", str(e), color=Config.COLOR_ERROR))

    # ── Firewall ─────────────────────────────────────────────

    @commands.command(name="fwrules2", aliases=["firewallrules", "fwlist"])
    @admin_only()
    async def fw_rules(self, ctx, direction: str = "in", limit: int = 15):
        """List Windows Firewall rules. Usage: !fwrules [in|out] [limit]"""
        direction = "Inbound" if direction.lower() == "in" else "Outbound"
        async with ctx.typing():
            script = f"""
Get-NetFirewallRule -Direction {direction} -Enabled True |
  Select-Object -First {min(limit, 30)} |
  ForEach-Object {{
    $prog = ($_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program
    "$($_.DisplayName) | $($_.Action) | $($_.Protocol) | $($prog -replace '.*\\\\','')"
  }}
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            f"🔥 Firewall Rules ({direction}, top {limit})",
            f"```\n{truncate(result, 1500)}\n```",
            color=Config.COLOR_INFO
        ))

    @commands.command(name="fwadd", aliases=["addfwrule", "firewallblock"])
    @admin_only()
    async def fw_add(self, ctx, name: str, direction: str, action: str, *, program_or_port: str):
        """Add a firewall rule. Usage: !fwadd RuleName in block notepad.exe
        Or: !fwadd RuleName in block port:8080"""
        direction = "Inbound" if direction.lower() == "in" else "Outbound"
        action = "Block" if action.lower() == "block" else "Allow"

        async with ctx.typing():
            if program_or_port.startswith("port:"):
                port = program_or_port.split(":")[1]
                script = f"""
New-NetFirewallRule -DisplayName "{name}" -Direction {direction} -Action {action} -Protocol TCP -LocalPort {port} -ErrorAction Stop
"Rule created: {name}"
"""
            else:
                script = f"""
New-NetFirewallRule -DisplayName "{name}" -Direction {direction} -Action {action} -Program "{program_or_port}" -ErrorAction Stop
"Rule created: {name}"
"""
            result = await _ps(script)

        color = Config.COLOR_SUCCESS if "created" in result else Config.COLOR_ERROR
        await ctx.send(embed=build_embed(f"🔥 Firewall Rule: {action}", f"```{result}```", color=color))

    @commands.command(name="fwremove", aliases=["removefwrule", "deletefwrule"])
    @admin_only()
    async def fw_remove(self, ctx, *, name: str):
        """Remove a firewall rule by name. Usage: !fwremove RuleName"""
        async with ctx.typing():
            result = await _ps(f'Remove-NetFirewallRule -DisplayName "{name}" -ErrorAction Stop; "Removed: {name}"')
        color = Config.COLOR_SUCCESS if "Removed" in result else Config.COLOR_ERROR
        await ctx.send(embed=build_embed("🔥 Firewall Rule Removed", f"```{result}```", color=color))

    # ── Hosts File ───────────────────────────────────────────

    @commands.command(name="netblock", aliases=["blockdomain", "hostsblock"])
    @admin_only()
    async def net_block(self, ctx, domain: str):
        """Block a domain via hosts file. Usage: !netblock example.com"""
        domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
        async with ctx.typing():
            script = f"""
$hosts = "C:\\Windows\\System32\\drivers\\etc\\hosts"
$entry = "0.0.0.0 {domain}"
$www   = "0.0.0.0 www.{domain}"
$content = Get-Content $hosts -Raw
if ($content -notmatch [regex]::Escape("{domain}")) {{
    Add-Content $hosts "`n$entry"
    Add-Content $hosts "`n$www"
    "Blocked: {domain}"
}} else {{
    "Already blocked: {domain}"
}}
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "🚫 Domain Blocked",
            f"`{domain}`\n```{result}```",
            color=Config.COLOR_SUCCESS if "Blocked" in result else Config.COLOR_WARNING
        ))

    @commands.command(name="netunblock", aliases=["unblockdomain"])
    @admin_only()
    async def net_unblock(self, ctx, domain: str):
        """Unblock a domain from hosts file. Usage: !netunblock example.com"""
        domain = domain.strip().lower()
        async with ctx.typing():
            script = f"""
$hosts = "C:\\Windows\\System32\\drivers\\etc\\hosts"
$lines = Get-Content $hosts | Where-Object {{ $_ -notmatch [regex]::Escape("{domain}") }}
$lines | Set-Content $hosts
"Unblocked: {domain}"
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed("✅ Domain Unblocked", f"`{domain}`\n```{result}```", color=Config.COLOR_SUCCESS))

    @commands.command(name="blocklist", aliases=["hostsblocklist"])
    @admin_only()
    async def block_list(self, ctx):
        """Show all blocked domains in hosts file."""
        async with ctx.typing():
            script = """
Get-Content "C:\\Windows\\System32\\drivers\\etc\\hosts" |
  Where-Object { $_ -match '^0\\.0\\.0\\.0' -and $_ -notmatch '^#' } |
  ForEach-Object { $_.Trim() }
"""
            result = await _ps(script)
        lines = [l for l in result.splitlines() if l.strip()]
        desc = "\n".join(f"• `{l}`" for l in lines) if lines else "No domains blocked."
        await ctx.send(embed=build_embed(f"🚫 Blocked Domains ({len(lines)})", truncate(desc, 1500), color=Config.COLOR_INFO))

    # ── WiFi ─────────────────────────────────────────────────

    @commands.command(name="wifilist", aliases=["wifiscan", "networks"])
    async def wifi_list(self, ctx):
        """List available WiFi networks."""
        async with ctx.typing():
            result = await _run("netsh", "wlan", "show", "networks", "mode=bssid", timeout=15)
        lines = result.splitlines()
        # Parse network blocks
        networks = []
        current = {}
        for line in lines:
            line = line.strip()
            if line.startswith("SSID") and "BSSID" not in line:
                if current:
                    networks.append(current)
                current = {"ssid": line.split(":", 1)[-1].strip()}
            elif "Signal" in line and current:
                current["signal"] = line.split(":", 1)[-1].strip()
            elif "Authentication" in line and current:
                current["auth"] = line.split(":", 1)[-1].strip()
        if current:
            networks.append(current)

        if networks:
            lines_out = [f"📶 `{n.get('ssid','?')}` — {n.get('signal','?')} ({n.get('auth','?')})"
                         for n in networks[:20]]
            desc = "\n".join(lines_out)
        else:
            desc = f"```\n{truncate(result, 1000)}\n```"

        await ctx.send(embed=build_embed(
            f"📡 WiFi Networks ({len(networks)} found)",
            truncate(desc, 1500),
            color=Config.COLOR_INFO
        ))

    @commands.command(name="wifistatus", aliases=["wificurrent", "currentwifi"])
    async def wifi_status(self, ctx):
        """Show current WiFi connection status."""
        async with ctx.typing():
            result = await _run("netsh", "wlan", "show", "interfaces", timeout=10)
        await ctx.send(embed=build_embed(
            "📡 WiFi Status",
            f"```\n{truncate(result, 1200)}\n```",
            color=Config.COLOR_INFO
        ))

    @commands.command(name="wificonnect", aliases=["connectwifi"])
    @admin_only()
    async def wifi_connect(self, ctx, *, ssid: str):
        """Connect to a saved WiFi network. Usage: !wificonnect MyNetwork"""
        async with ctx.typing():
            result = await _run("netsh", "wlan", "connect", f"name={ssid}", timeout=15)
        await ctx.send(embed=build_embed(
            "📡 WiFi Connect",
            f"Connecting to `{ssid}`...\n```{result}```",
            color=Config.COLOR_INFO
        ))

    @commands.command(name="wifipassword", aliases=["wifipass", "wifikey"])
    @admin_only()
    async def wifi_password(self, ctx, *, profile: str = None):
        """Show saved WiFi password(s). Usage: !wifipassword [network_name]"""
        async with ctx.typing():
            if profile:
                result = await _run("netsh", "wlan", "show", "profile",
                                     f"name={profile}", "key=clear", timeout=10)
                # Extract password
                for line in result.splitlines():
                    if "Key Content" in line:
                        password = line.split(":", 1)[-1].strip()
                        await ctx.send(embed=build_embed(
                            f"🔑 WiFi Password: {profile}",
                            f"Password: ||`{password}`|| _(click to reveal)_",
                            color=Config.COLOR_SUCCESS
                        ))
                        return
                await ctx.send(embed=build_embed("🔑 Password", f"```{truncate(result, 800)}```", color=Config.COLOR_INFO))
            else:
                # List all profiles
                result = await _run("netsh", "wlan", "show", "profiles", timeout=10)
                await ctx.send(embed=build_embed(
                    "📡 Saved WiFi Profiles",
                    f"```\n{truncate(result, 1000)}\n```\nUse `!wifipassword <name>` to see password.",
                    color=Config.COLOR_INFO
                ))

    # ── Port Scanner ─────────────────────────────────────────

    @commands.command(name="portscan2", aliases=["rportscan", "portcheck"])
    @admin_only()
    async def port_scan(self, ctx, host: str = "localhost", ports: str = "80,443,22,21,3389,8080"):
        """Scan ports on a host. Usage: !portscan [host] [ports]
        Ports: comma-separated or range like 80-100"""
        async with ctx.typing():
            # Parse ports
            port_list = []
            for part in ports.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-")
                    port_list.extend(range(int(start), int(end)+1))
                else:
                    port_list.append(int(part))

            port_list = port_list[:50]  # max 50 ports

            async def check_port(port):
                try:
                    conn = asyncio.open_connection(host, port)
                    _, writer = await asyncio.wait_for(conn, timeout=1.5)
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                    return port, True
                except Exception:
                    return port, False

            results = await asyncio.gather(*[check_port(p) for p in port_list])
            open_ports = [(p, s) for p, s in results if s]
            closed_ports = [(p, s) for p, s in results if not s]

        lines = []
        for port, _ in open_ports:
            service = {80: "HTTP", 443: "HTTPS", 22: "SSH", 21: "FTP",
                       3389: "RDP", 8080: "HTTP-Alt", 3306: "MySQL",
                       5432: "PostgreSQL", 27017: "MongoDB"}.get(port, "unknown")
            lines.append(f"🟢 `{port}` — {service}")
        for port, _ in closed_ports:
            lines.append(f"🔴 `{port}`")

        await ctx.send(embed=build_embed(
            f"🔍 Port Scan: {host}",
            f"**Open: {len(open_ports)} | Closed: {len(closed_ports)}**\n\n" + "\n".join(lines),
            color=Config.COLOR_INFO
        ))

    # ── DNS ──────────────────────────────────────────────────

    @commands.command(name="dnslookup2", aliases=["rdns", "rnslookup"])
    async def dns_lookup(self, ctx, hostname: str):
        """DNS lookup for a hostname. Usage: !dnslookup google.com"""
        async with ctx.typing():
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(None, socket.gethostbyname_ex, hostname)
                host, aliases, addresses = result
                fields = [
                    ("Hostname", host, False),
                    ("IP Addresses", "\n".join(f"`{a}`" for a in addresses), False),
                ]
                if aliases:
                    fields.append(("Aliases", "\n".join(aliases), False))
                await ctx.send(embed=build_embed(
                    f"🔍 DNS: {hostname}", "", color=Config.COLOR_INFO, fields=fields
                ))
            except Exception as e:
                await ctx.send(embed=build_embed("❌ DNS Lookup Failed", str(e), color=Config.COLOR_ERROR))

    @commands.command(name="dnsflush", aliases=["flushdns"])
    @admin_only()
    async def dns_flush(self, ctx):
        """Flush DNS cache."""
        async with ctx.typing():
            result = await _run("ipconfig", "/flushdns", timeout=10)
        await ctx.send(embed=build_embed("✅ DNS Cache Flushed", f"```{result}```", color=Config.COLOR_SUCCESS))

    @commands.command(name="dnsservers", aliases=["getdns"])
    async def dns_servers(self, ctx):
        """Show current DNS servers."""
        async with ctx.typing():
            result = await _ps("""
Get-DnsClientServerAddress | Where-Object { $_.ServerAddresses } |
  ForEach-Object { "$($_.InterfaceAlias): $($_.ServerAddresses -join ', ')" }
""")
        await ctx.send(embed=build_embed("🌐 DNS Servers", f"```\n{truncate(result, 900)}\n```", color=Config.COLOR_INFO))

    # ── Bandwidth Monitor ─────────────────────────────────────

    @commands.command(name="netmon", aliases=["bandwidth", "netbandwidth"])
    async def net_monitor(self, ctx, seconds: int = 5):
        """Monitor network bandwidth for N seconds. Usage: !netmon [seconds]"""
        seconds = max(3, min(seconds, 30))
        async with ctx.typing():
            script1 = """
$stats = Get-NetAdapterStatistics | Where-Object { $_.ReceivedBytes -gt 0 }
$stats | ForEach-Object { "$($_.Name),$($_.ReceivedBytes),$($_.SentBytes)" }
"""
            before = await _ps(script1)
            await asyncio.sleep(seconds)
            after = await _ps(script1)

        def parse(output):
            result = {}
            for line in output.splitlines():
                parts = line.split(",")
                if len(parts) == 3:
                    result[parts[0]] = (int(parts[1]), int(parts[2]))
            return result

        b = parse(before)
        a = parse(after)

        fields = []
        for name in a:
            if name in b:
                rx = (a[name][0] - b[name][0]) / seconds / 1024
                tx = (a[name][1] - b[name][1]) / seconds / 1024
                if abs(rx) > 0 or abs(tx) > 0:
                    fields.append((name, f"⬇️ {rx:.1f} KB/s  ⬆️ {tx:.1f} KB/s", False))

        if not fields:
            fields = [("Status", "No significant traffic detected.", False)]

        await ctx.send(embed=build_embed(
            f"📊 Bandwidth Monitor ({seconds}s average)",
            "",
            color=Config.COLOR_MONITOR,
            fields=fields
        ))

    # ── Connections ──────────────────────────────────────────

    @commands.command(name="netconnections", aliases=["rnetconn", "tcpconn"])
    @admin_only()
    async def net_connections(self, ctx, state: str = "ESTABLISHED"):
        """Show active network connections. Usage: !netconnections [ESTABLISHED|LISTENING|ALL]"""
        async with ctx.typing():
            if state.upper() == "ALL":
                result = await _run("netstat", "-ano", timeout=15)
            else:
                script = f"""
Get-NetTCPConnection -State {state.capitalize()} |
  Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, State, OwningProcess |
  ForEach-Object {{
    $proc = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name
    "$($_.LocalAddress):$($_.LocalPort) -> $($_.RemoteAddress):$($_.RemotePort) [$proc]"
  }} | Select-Object -First 25
"""
                result = await _ps(script)

        await ctx.send(embed=build_embed(
            f"🔌 Network Connections ({state})",
            f"```\n{truncate(result, 1500)}\n```",
            color=Config.COLOR_INFO
        ))


async def setup(bot):
    await bot.add_cog(NetworkPlus(bot))
