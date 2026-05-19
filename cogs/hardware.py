"""
Hardware cog — deep hardware info for Windows: CPU temps, GPU, RAM slots,
storage SMART data, motherboard sensors, USB devices, fan speeds.
Requires: psutil, wmi, pywin32 (pip install wmi pywin32)
"""

import asyncio
import platform
import subprocess
from typing import Optional

import discord
from discord.ext import commands

from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate, bytes_to_human
from utils.logger import setup_logger

logger = setup_logger("cog.hardware")


async def _run(*cmd, timeout=15) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace").strip()
    except Exception as e:
        return f"Error: {e}"


async def _ps(script: str) -> str:
    """Run a PowerShell script and return output."""
    return await _run("powershell", "-NoProfile", "-Command", script, timeout=60)


class Hardware(commands.Cog):
    """Full hardware monitoring and info for Windows."""

    def __init__(self, bot):
        self.bot = bot

    # ── CPU ─────────────────────────────────────────────────

    @commands.command(name="cputemp", aliases=["cputherm", "cputhermal"])
    @admin_only()
    async def cpu_temp(self, ctx):
        """Show CPU temperature via WMI / Open Hardware Monitor."""
        async with ctx.typing():
            # Try OpenHardwareMonitor WMI namespace first
            script = """
try {
    $ohm = Get-WmiObject -Namespace "root/OpenHardwareMonitor" -Class Sensor -ErrorAction Stop |
           Where-Object { $_.SensorType -eq 'Temperature' }
    if ($ohm) {
        $ohm | ForEach-Object { "$($_.Name): $($_.Value) C" }
    } else { "OHM_NOT_FOUND" }
} catch { "OHM_NOT_FOUND" }
"""
            result = await _ps(script)

            if "OHM_NOT_FOUND" in result or not result:
                # Fallback: MSAcpi_ThermalZoneTemperature
                script2 = """
$temps = Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace "root/wmi" 2>$null
if ($temps) {
    $temps | ForEach-Object {
        $c = [math]::Round(($_.CurrentTemperature - 2732) / 10, 1)
        "$($_.InstanceName): ${c} C"
    }
} else { "ACPI_FALLBACK_FAILED" }
"""
                result = await _ps(script2)

            if not result or "FAILED" in result or "Error" in result:
                desc = ("⚠️ Could not read CPU temperature.\n\n"
                        "Install **[Open Hardware Monitor](https://openhardwaremonitor.org/)** "
                        "and run it as admin, then try again.")
            else:
                desc = f"```\n{truncate(result, 900)}\n```"

        await ctx.send(embed=build_embed("🌡️ CPU Temperature", desc, color=Config.COLOR_SYSTEM))

    @commands.command(name="cpuclock", aliases=["cpufreq", "cpuspeed"])
    async def cpu_clock(self, ctx):
        """Show per-core CPU clock speeds."""
        async with ctx.typing():
            script = """
$cpu = Get-WmiObject Win32_Processor
$cpu | ForEach-Object {
    "Name: $($_.Name)"
    "Cores: $($_.NumberOfCores) | Threads: $($_.NumberOfLogicalProcessors)"
    "Base: $($_.MaxClockSpeed) MHz"
    "Current: $($_.CurrentClockSpeed) MHz"
    "Load: $($_.LoadPercentage)%"
}
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "⚡ CPU Clock Speed",
            f"```\n{truncate(result, 1000)}\n```",
            color=Config.COLOR_SYSTEM
        ))

    # ── GPU ─────────────────────────────────────────────────

    @commands.command(name="gpuinfo", aliases=["gpustatus"])
    async def gpu_info(self, ctx):
        """Show GPU info — name, VRAM, driver, temperature."""
        async with ctx.typing():
            # Basic info from WMI
            script_basic = """
Get-WmiObject Win32_VideoController | ForEach-Object {
    "Name: $($_.Name)"
    "VRAM: $([math]::Round($_.AdapterRAM / 1GB, 2)) GB"
    "Driver: $($_.DriverVersion)"
    "Resolution: $($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)"
    "Refresh: $($_.CurrentRefreshRate) Hz"
    "Status: $($_.Status)"
    "---"
}
"""
            basic = await _ps(script_basic)

            # NVIDIA specific (nvidia-smi)
            nvidia = await _run("nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,fan.speed",
                "--format=csv,noheader,nounits",
                timeout=10)

            fields = [("Basic Info", f"```\n{truncate(basic, 800)}\n```", False)]
            if nvidia and "Error" not in nvidia and "not found" not in nvidia.lower():
                cols = [x.strip() for x in nvidia.split(",")]
                if len(cols) >= 6:
                    fields.append(("🟢 NVIDIA GPU", (
                        f"**Name:** {cols[0]}\n"
                        f"**Temp:** {cols[1]}°C\n"
                        f"**GPU Usage:** {cols[2]}%\n"
                        f"**VRAM:** {cols[3]} / {cols[4]} MiB\n"
                        f"**Fan:** {cols[5]}%"
                    ), False))

        await ctx.send(embed=build_embed(
            "🎮 GPU Information", "", color=Config.COLOR_SYSTEM, fields=fields
        ))

    @commands.command(name="gpumon", aliases=["gpuwatch"])
    async def gpu_monitor(self, ctx, interval: int = 5):
        """Live GPU stats via nvidia-smi (NVIDIA only). Updates every N seconds for 30s."""
        if interval < 3:
            interval = 3
        async with ctx.typing():
            nvidia = await _run("nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits", timeout=10)
            if "Error" in nvidia or not nvidia:
                await ctx.send("❌ nvidia-smi not found. GPU monitoring requires NVIDIA GPU with drivers installed.")
                return

        msg = await ctx.send(embed=build_embed("🎮 GPU Monitor", "Starting...", color=Config.COLOR_MONITOR))
        for _ in range(30 // interval):
            result = await _run("nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,fan.speed,power.draw",
                "--format=csv,noheader,nounits", timeout=10)
            if result and "Error" not in result:
                cols = [x.strip() for x in result.split(",")]
                embed = build_embed(
                    "🎮 GPU Live Monitor",
                    f"Updates every {interval}s",
                    color=Config.COLOR_MONITOR,
                    fields=[
                        ("GPU", cols[0] if len(cols) > 0 else "N/A", False),
                        ("🌡️ Temp", f"{cols[1]}°C" if len(cols) > 1 else "N/A", True),
                        ("📊 Usage", f"{cols[2]}%" if len(cols) > 2 else "N/A", True),
                        ("💾 VRAM", f"{cols[3]}/{cols[4]} MiB" if len(cols) > 4 else "N/A", True),
                        ("🌀 Fan", f"{cols[5]}%" if len(cols) > 5 else "N/A", True),
                        ("⚡ Power", f"{cols[6]}W" if len(cols) > 6 else "N/A", True),
                    ]
                )
                await msg.edit(embed=embed)
            await asyncio.sleep(interval)

    # ── RAM ─────────────────────────────────────────────────

    @commands.command(name="ramslots", aliases=["ramdetail", "memoryslots"])
    async def ram_slots(self, ctx):
        """Show detailed RAM slot info — speed, size, manufacturer, type."""
        async with ctx.typing():
            script = """
Get-WmiObject Win32_PhysicalMemory | ForEach-Object {
    $type = switch ($_.MemoryType) {
        20 {"DDR"} 21 {"DDR2"} 22 {"DDR2 FB-DIMM"} 24 {"DDR3"} 26 {"DDR4"} 34 {"DDR5"} default {"Unknown"}
    }
    "Slot: $($_.DeviceLocator)"
    "Size: $([math]::Round($_.Capacity / 1GB, 0)) GB"
    "Speed: $($_.Speed) MHz"
    "Type: $type"
    "Manufacturer: $($_.Manufacturer)"
    "Part#: $($_.PartNumber)"
    "---"
}
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "🧠 RAM Slots Detail",
            f"```\n{truncate(result, 1500)}\n```",
            color=Config.COLOR_SYSTEM
        ))

    # ── Storage / SMART ──────────────────────────────────────

    @commands.command(name="smart", aliases=["disksmart", "diskhealth"])
    @admin_only()
    async def smart_info(self, ctx):
        """Show SMART health data for all drives."""
        async with ctx.typing():
            script = """
Get-WmiObject -Namespace root/wmi -Class MSStorageDriver_FailurePredictStatus 2>$null |
ForEach-Object {
    $status = if ($_.PredictFailure) { "⚠️ WARNING" } else { "✅ HEALTHY" }
    "Drive: $($_.InstanceName)"
    "Status: $status"
    "Reason: $($_.Reason)"
    "---"
}
"""
            result = await _ps(script)

            if not result or "Error" in result:
                # Fallback: diskdrive basic
                script2 = """
Get-WmiObject Win32_DiskDrive | ForEach-Object {
    "Drive: $($_.Caption)"
    "Size: $([math]::Round($_.Size / 1GB, 1)) GB"
    "Interface: $($_.InterfaceType)"
    "Status: $($_.Status)"
    "Partitions: $($_.Partitions)"
    "---"
}
"""
                result = await _ps(script2)

        await ctx.send(embed=build_embed(
            "💾 Disk SMART / Health",
            f"```\n{truncate(result, 1500)}\n```",
            color=Config.COLOR_SYSTEM
        ))

    @commands.command(name="disktemp", aliases=["hddtemp", "storagetemp"])
    @admin_only()
    async def disk_temp(self, ctx):
        """Show disk temperature via Open Hardware Monitor or CrystalDiskInfo."""
        async with ctx.typing():
            script = """
try {
    $temps = Get-WmiObject -Namespace "root/OpenHardwareMonitor" -Class Sensor -ErrorAction Stop |
             Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'HDD|SSD|NVMe|Drive' }
    if ($temps) {
        $temps | ForEach-Object { "$($_.Hardware.Name) - $($_.Name): $($_.Value)°C" }
    } else { "No disk temperature sensors found in OHM." }
} catch { "Open Hardware Monitor not running. Install and run as admin." }
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "🌡️ Disk Temperature",
            f"```\n{truncate(result, 900)}\n```",
            color=Config.COLOR_SYSTEM
        ))

    # ── Motherboard / Sensors ────────────────────────────────

    @commands.command(name="sensors", aliases=["hwsensors", "alltemps"])
    @admin_only()
    async def all_sensors(self, ctx):
        """Show all hardware sensors via Open Hardware Monitor."""
        loading = await ctx.send(embed=build_embed(
            "🔬 Hardware Sensors",
            "⏳ Querying Open Hardware Monitor... (may take 10-30s)",
            color=Config.COLOR_SYSTEM
        ))
        async with ctx.typing():
            script = (
                "try {"
                "  $s = Get-WmiObject -Namespace 'root/OpenHardwareMonitor' -Class Sensor -EA Stop;"
                "  $s | Sort SensorType,Name | %{"
                "    $u = if($_.SensorType-eq'Temperature'){'C'}elseif($_.SensorType-eq'Fan'){'RPM'}elseif($_.SensorType-eq'Voltage'){'V'}else{'%'};"
                "    $_.SensorType+' | '+$_.Name+' | '+$_.Value+' '+$u"
                "  }"
                "} catch { 'ERROR: '+$_ }"
            )
            result = await _ps(script)

        await loading.delete()

        if not result or result.startswith("ERROR"):
            await ctx.send(embed=build_embed(
                "❌ Sensors Error",
                f"```{result or 'No data returned'}```\n"
                "Make sure **Open Hardware Monitor** is running as Administrator.",
                color=Config.COLOR_ERROR
            ))
            return

        chunks = [result[i:i+1400] for i in range(0, len(result), 1400)]
        for i, chunk in enumerate(chunks[:3]):
            title = "🔬 All Hardware Sensors" if i == 0 else f"🔬 Sensors (cont. {i+1})"
            await ctx.send(embed=build_embed(title, f"```\n{chunk}\n```", color=Config.COLOR_SYSTEM))

    @commands.command(name="moboinfo", aliases=["motherboard", "mbinfo"])
    async def mobo_info(self, ctx):
        """Show motherboard information."""
        async with ctx.typing():
            script = """
$board = Get-WmiObject Win32_BaseBoard
$bios  = Get-WmiObject Win32_BIOS
"Manufacturer: $($board.Manufacturer)"
"Model: $($board.Product)"
"Version: $($board.Version)"
"Serial: $($board.SerialNumber)"
"---"
"BIOS Vendor: $($bios.Manufacturer)"
"BIOS Version: $($bios.SMBIOSBIOSVersion)"
"BIOS Date: $($bios.ReleaseDate)"
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "🖥️ Motherboard Info",
            f"```\n{truncate(result, 900)}\n```",
            color=Config.COLOR_SYSTEM
        ))

    # ── USB ─────────────────────────────────────────────────

    @commands.command(name="usblist", aliases=["usb", "usbdevices"])
    async def usb_list(self, ctx):
        """List all connected USB devices."""
        async with ctx.typing():
            script = """
Get-WmiObject Win32_USBControllerDevice | ForEach-Object {
    $dep = [wmi]($_.Dependent)
    if ($dep.Name -and $dep.Name -notmatch "USB Root Hub|Host Controller") {
        "$($dep.Name)"
    }
} | Sort-Object -Unique
"""
            result = await _ps(script)
            if not result or "Error" in result:
                script2 = "Get-PnpDevice -Class USB | Select-Object -ExpandProperty FriendlyName | Sort-Object"
                result = await _ps(script2)

        lines = [l for l in result.splitlines() if l.strip()]
        desc = "\n".join(f"• {l}" for l in lines) if lines else "No USB devices found."
        await ctx.send(embed=build_embed(
            f"🔌 USB Devices ({len(lines)} found)",
            truncate(desc, 1500),
            color=Config.COLOR_INFO
        ))

    @commands.command(name="usbeject", aliases=["ejectusb"])
    @admin_only()
    async def usb_eject(self, ctx, drive_letter: str):
        """Safely eject a USB drive. Usage: !usbeject E"""
        drive_letter = drive_letter.strip(":").upper()
        async with ctx.typing():
            script = f"""
$driveEject = New-Object -comObject Shell.Application
$driveEject.Namespace(17).ParseName('{drive_letter}:').InvokeVerb('Eject')
"Ejected {drive_letter}:"
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "🔌 USB Ejected",
            f"Drive `{drive_letter}:` safely ejected.\n```{result}```",
            color=Config.COLOR_SUCCESS
        ))

    # ── Fan ──────────────────────────────────────────────────

    @commands.command(name="fanspeed", aliases=["fans", "fanrpm"])
    @admin_only()
    async def fan_speed(self, ctx):
        """Show fan speeds via WMI / Open Hardware Monitor."""
        async with ctx.typing():
            script = """
try {
    $fans = Get-WmiObject -Namespace "root/OpenHardwareMonitor" -Class Sensor -ErrorAction Stop |
            Where-Object { $_.SensorType -eq 'Fan' }
    if ($fans) {
        $fans | ForEach-Object { "$($_.Name): $($_.Value) RPM" }
    } else { "No fan sensors in OHM." }
} catch {
    # Fallback WMI
    $fans = Get-WmiObject Win32_Fan 2>$null
    if ($fans) { $fans | ForEach-Object { "$($_.Name): $($_.DesiredSpeed) RPM" } }
    else { "No fan data available. Install Open Hardware Monitor for fan monitoring." }
}
"""
            result = await _ps(script)
        await ctx.send(embed=build_embed(
            "🌀 Fan Speeds",
            f"```\n{truncate(result, 900)}\n```",
            color=Config.COLOR_SYSTEM
        ))

    # ── Full hardware summary ────────────────────────────────

    @commands.command(name="hwinfo", aliases=["hardware", "fullhw"])
    async def hw_info(self, ctx):
        """Full hardware summary: CPU, GPU, RAM, Disk, Mobo."""
        async with ctx.typing():
            script = """
$cpu   = Get-WmiObject Win32_Processor | Select-Object -First 1
$gpu   = Get-WmiObject Win32_VideoController | Select-Object -First 1
$ram   = (Get-WmiObject Win32_PhysicalMemory | Measure-Object Capacity -Sum).Sum
$disks = Get-WmiObject Win32_DiskDrive
$board = Get-WmiObject Win32_BaseBoard | Select-Object -First 1
$os    = Get-WmiObject Win32_OperatingSystem

"=== CPU ==="
"$($cpu.Name)"
"Cores: $($cpu.NumberOfCores) | Threads: $($cpu.NumberOfLogicalProcessors)"
"Base: $($cpu.MaxClockSpeed) MHz | Load: $($cpu.LoadPercentage)%"
""
"=== GPU ==="
"$($gpu.Name)"
"VRAM: $([math]::Round($gpu.AdapterRAM / 1GB, 1)) GB"
""
"=== RAM ==="
"Total: $([math]::Round($ram / 1GB, 1)) GB"
"Available: $([math]::Round($os.FreePhysicalMemory / 1MB, 1)) GB"
""
"=== STORAGE ==="
$disks | ForEach-Object { "$($_.Caption): $([math]::Round($_.Size / 1GB, 0)) GB ($($_.InterfaceType))" }
""
"=== MOTHERBOARD ==="
"$($board.Manufacturer) $($board.Product)"
"""
            result = await _ps(script)

        await ctx.send(embed=build_embed(
            "🖥️ Full Hardware Summary",
            f"```\n{truncate(result, 1800)}\n```",
            color=Config.COLOR_SYSTEM
        ))


async def setup(bot):
    await bot.add_cog(Hardware(bot))