# 🤖 PC Agent — Discord PC Control Bot

A Discord bot for **full remote PC control** via Discord commands. Built with discord.py and backed by SQLite. Targets Windows but most cogs work on Linux/macOS.

**Version:** 2.1.0 · **Python:** 3.10+ · **Commands:** 230 · **Source:** 9,377 lines Python

---

## 📁 Project Structure

```
PC-Agent/
│
├── main.py                    # Bot entry point — loads all cogs, syncs slash commands
├── requirements.txt           # All Python dependencies
├── .env                       # Your config (gitignored)
├── .env.example               # Config template
├── .gitignore
│
├── cogs/                      # Feature modules — one cog per domain
│   ├── remote_control.py      # 480 ln │ 18 cmd │ Mouse, keyboard, window mgmt, stream
│   ├── network_plus.py        # 471 ln │ 17 cmd │ Speed test, firewall, WiFi, DNS, hosts
│   ├── hardware.py            # 450 ln │ 13 cmd │ CPU temp/clock, GPU, SMART, sensors, USB
│   ├── files.py               # 352 ln │ 13 cmd │ File system CRUD: ls, read, upload, zip
│   ├── automation.py          # 249 ln │ 13 cmd │ Shell exec, keyboard/mouse, env vars
│   ├── network.py             # 286 ln │ 11 cmd │ IP, ping, traceroute, DNS, port scan
│   ├── perf_counters.py       # 487 ln │  8 cmd │ Raw Windows PDH counters via native C++ binary
│   ├── fps_counter.py         # 467 ln │  7 cmd │ DXGI FPS counter via injected hook DLL
│   ├── gpu_detailed.py        # 542 ln │  6 cmd │ Deep NVIDIA GPU monitoring via pynvml
│   ├── gpu_pipeline.py        # 408 ln │  6 cmd │ GPU frame pipeline engine usage via PDH
│   ├── bandwidth.py           # 394 ln │  6 cmd │ Internet speed tests via speedtest-cli
│   ├── processes.py           # 253 ln │ 10 cmd │ Process list, kill, suspend, priority
│   ├── audio.py               # 240 ln │ 10 cmd │ Volume, mute, TTS, media control
│   ├── macro.py               # 358 ln │  9 cmd │ Record, save, replay command sequences
│   ├── system.py              # 295 ln │  9 cmd │ CPU, RAM, disk, temp, GPU, dashboard
│   ├── security.py            # 258 ln │  9 cmd │ Firewall, startup, antivirus, audit log
│   ├── visualizations.py      # 355 ln │  8 cmd │ Charts, heatmap, waterfall, sparklines
│   ├── monitoring.py          # 276 ln │  8 cmd │ Background polling, alerts, export
│   ├── power.py               # 210 ln │  8 cmd │ Shutdown, restart, sleep, hibernate
│   ├── display.py             # 225 ln │  8 cmd │ Screenshot, resolution, brightness
│   ├── permissions.py         # 368 ln │  7 cmd │ RBAC: viewer / operator / admin (SQLite)
│   ├── remote.py              # 243 ln │  7 cmd │ Live stream, WoL, health check
│   ├── scheduler.py           # 252 ln │  6 cmd │ At/cron tasks, reminders
│   ├── clipboard.py           # 137 ln │  6 cmd │ Clipboard manager with history
│   ├── registry.py            # 240 ln │  4 cmd │ Windows Registry CRUD
│   └── help.py                # 244 ln │  3 cmd │ Paginated embed help system
│
├── services/
│   ├── database.py            # 198 ln │ SQLite wrapper — audit log, tasks, permissions, macros
│   └── viz_service.py         # 266 ln │ Matplotlib dark-theme chart generation
│
├── native/                    # C++ native binaries (Windows-only)
│   ├── perf_counters.cpp/.exe # PDH performance counter reader
│   ├── fps_counter.cpp/.exe   # DXGI FPS reader (shared memory)
│   ├── fps_hook.cpp/.dll      # Game process injection hook
│   ├── gpu_pipeline.cpp/.exe  # GPU engine usage via PDH
│   └── build_native.bat       # Build script for all native binaries
│
└── utils/
    ├── config.py              #  70 ln │ Centralized env-var config with defaults
    ├── helpers.py             # 128 ln │ Decorators (admin_only), embed builder, formatters
    └── logger.py              #  47 ln │ Rotating file + console logger
```

---

## ✨ Command Reference

### 🖥️ System — `cogs/system.py`
| Command | Description |
|---|---|
| `!sysinfo` | Full system overview (OS, CPU, RAM, disk, uptime) |
| `!cpu` | CPU usage and core breakdown |
| `!ram` | RAM usage with available/used breakdown |
| `!disk` | Disk partitions and usage |
| `!temp` | System temperatures |
| `!gpu` | GPU status (requires GPUtil) |
| `!battery` | Battery level and status |
| `!uptime` | System uptime |
| `!dashboard` | Combined live metrics dashboard |

### ⚙️ Processes — `cogs/processes.py`
| Command | Description |
|---|---|
| `!ps` | List running processes |
| `!kill <pid>` | Kill a process by PID |
| `!findproc <name>` | Search processes by name |
| `!suspend <pid>` | Suspend a process |
| `!resume <pid>` | Resume a suspended process |
| `!topcpu` | Top CPU-consuming processes |
| `!topmem` | Top memory-consuming processes |
| `!procinfo <pid>` | Detailed process info |
| `!setpriority <pid> <level>` | Change process priority |
| `!killname <name>` | Kill process by name |

### 📁 Files — `cogs/files.py`
| Command | Description |
|---|---|
| `!ls [path]` | List directory contents |
| `!readfile <path>` | Read and display a file |
| `!download <path>` | Upload a file to Discord |
| `!upload` | Save an attached file to disk |
| `!deletefile <path>` | Delete a file |
| `!rename <src> <dst>` | Rename/move a file |
| `!copy <src> <dst>` | Copy a file |
| `!find <pattern>` | Search files by name/pattern |
| `!zip <src> <dst>` | Create a zip archive |
| `!unzip <src> [dst]` | Extract a zip archive |
| `!fileinfo <path>` | File metadata |
| `!du <path>` | Disk usage of a directory |
| `!mkdir <path>` | Create a directory |

### 🌐 Network — `cogs/network.py`
| Command | Description |
|---|---|
| `!ip` | Local and public IP addresses |
| `!ping <host>` | ICMP ping |
| `!traceroute <host>` | Traceroute |
| `!dns <host>` | DNS lookup |
| `!netstat` | Active network connections |
| `!networkstats` | NIC stats (bytes sent/recv) |
| `!wifi` | WiFi info |
| `!mac` | MAC address |
| `!scanports <host>` | TCP port scanner |
| `!speedtest` | Internet speed test |
| `!whois <host>` | WHOIS lookup |

### 📡 Network Plus — `cogs/network_plus.py`
| Command | Description |
|---|---|
| `!netspeed` | Speedtest via speedtest-cli |
| `!fwrules2` | List Windows Firewall rules |
| `!fwadd <name> <port>` | Add a firewall block rule |
| `!fwremove <name>` | Remove a firewall rule |
| `!netblock <domain>` | Block a domain via hosts file |
| `!netunblock <domain>` | Remove a domain block |
| `!blocklist` | View all blocked domains |
| `!wifilist` | Scan nearby WiFi networks |
| `!wifistatus` | Current WiFi connection |
| `!wificonnect <ssid>` | Connect to a WiFi network |
| `!wifipassword` | Show saved WiFi passwords (admin) |
| `!portscan2 <host> [range]` | TCP port scan |
| `!dnslookup2 <host>` | DNS lookup |
| `!dnsflush` | Flush DNS cache |
| `!dnsservers` | Show configured DNS servers |
| `!netmon` | Live bandwidth monitor |
| `!netconnections` | Active TCP connections |

### 🖼️ Display — `cogs/display.py`
| Command | Description |
|---|---|
| `!screenshot` | Capture and send screenshot |
| `!monitors` | List connected monitors |
| `!resolution [WxH]` | Get or set display resolution |
| `!brightness <0-100>` | Set screen brightness |
| `!wallpaper <url>` | Set desktop wallpaper |
| `!lockscreen` | Lock the workstation |
| `!annotate <text>` | Screenshot with annotation overlay |
| `!multiscreen` | Capture all monitors |

### 🔊 Audio — `cogs/audio.py`
| Command | Description |
|---|---|
| `!volume <0-100>` | Set system volume |
| `!getvolume` | Get current volume level |
| `!mute` | Mute system audio |
| `!unmute` | Unmute system audio |
| `!volup [amount]` | Increase volume |
| `!voldown [amount]` | Decrease volume |
| `!tts <text>` | Text-to-speech playback |
| `!playsound <path>` | Play an audio file |
| `!audiodevices` | List audio input/output devices |
| `!mediacontrol <action>` | Play/pause/next/prev media |

### 🤖 Automation — `cogs/automation.py`
| Command | Description |
|---|---|
| `!run <command>` | Execute a shell command |
| `!open <path/url>` | Open a file or URL |
| `!type <text>` | Type text at current cursor position |
| `!hotkey <keys>` | Send a keyboard shortcut |
| `!keypress <key>` | Press a single key |
| `!mousemove <x> <y>` | Move mouse cursor |
| `!click <x> <y>` | Left click at coordinates |
| `!doubleclick <x> <y>` | Double click at coordinates |
| `!scroll <amount>` | Scroll mouse wheel |
| `!mousepos` | Get current cursor position |
| `!alert <text>` | Show a Windows alert dialog |
| `!script <code>` | Run a Python snippet |
| `!env [var]` | Get environment variable(s) |

### 🖱️ Remote Control — `cogs/remote_control.py`
| Command | Description |
|---|---|
| `!remoteclick <x> <y> [btn]` | Mouse click at coordinates |
| `!remotedoubleclick <x> <y>` | Double click |
| `!remotemove <x> <y>` | Move mouse |
| `!remotescroll <amount>` | Scroll |
| `!rmousepos` | Get cursor position |
| `!remotedrag <x1> <y1> <x2> <y2>` | Click-drag |
| `!remotetype <text>` | Type text |
| `!remotekey <key>` | Press a key / hotkey combo |
| `!remoteenter` | Press Enter |
| `!remotepaste` | Paste clipboard |
| `!windowlist` | List all open windows |
| `!windowfocus <title>` | Bring a window to front |
| `!windowclose <title>` | Close a window |
| `!windowmin <title>` | Minimize a window |
| `!windowmax <title>` | Maximize a window |
| `!remotestream [interval]` | Live screen stream (auto-refresh screenshots) |
| `!rstreamstop` | Stop screen stream |
| `!screeninfo` | Display resolution and monitor info |

### 📊 Monitoring — `cogs/monitoring.py`
| Command | Description |
|---|---|
| `!startmonitor` | Start background monitoring loop |
| `!stopmonitor` | Stop monitoring |
| `!monitorstatus` | Monitoring status |
| `!cpuhistory` | CPU usage history chart |
| `!nethistory` | Network usage history chart |
| `!exportmetrics` | Export metrics to CSV/JSON |
| `!setalert <metric> <threshold>` | Set a threshold alert |
| `!alerts` | List active alerts |

### ⚡ Power — `cogs/power.py`
| Command | Description |
|---|---|
| `!shutdown [delay]` | Shutdown the PC |
| `!restart [delay]` | Restart the PC |
| `!sleep` | Put PC to sleep |
| `!hibernate` | Hibernate the PC |
| `!logoff` | Log off current user |
| `!canceltimer` | Cancel pending shutdown/restart |
| `!timedshutdown <seconds>` | Schedule a shutdown |
| `!powerstatus` | Battery and power info |

### 🗝️ Registry — `cogs/registry.py`
| Command | Description |
|---|---|
| `!regread <key> <value>` | Read a registry value |
| `!regwrite <key> <value> <data>` | Write a registry value |
| `!regdelete <key> <value>` | Delete a registry value |
| `!reglist <key>` | List keys/values under a path |

### 🛡️ Security — `cogs/security.py`
| Command | Description |
|---|---|
| `!firewall` | Show firewall status |
| `!startup` | List startup programs |
| `!antivirus` | Show antivirus status |
| `!auditlog` | View command audit log |
| `!commandstats` | Command usage statistics |
| `!whoami` | Current user and privileges |
| `!blockip <ip>` | Block an IP via firewall |
| `!sshkeys` | List SSH authorized keys |
| `!checkfile <path>` | File hash and info |

### 🔩 Hardware — `cogs/hardware.py`
| Command | Description |
|---|---|
| `!cputemp` | CPU temperature (OpenHardwareMonitor / WMI) |
| `!cpuclock` | CPU clock speeds per core |
| `!gpuinfo` | GPU details (name, VRAM, driver) |
| `!gpumon` | Live GPU usage monitor |
| `!ramslots` | RAM slot details (capacity, speed, type) |
| `!smart <drive>` | Disk SMART health data |
| `!disktemp` | Storage device temperatures |
| `!sensors` | All available hardware sensors |
| `!moboinfo` | Motherboard info |
| `!usblist` | Connected USB devices |
| `!usbeject <drive>` | Safely eject a USB device |
| `!fanspeed` | Fan RPM readings |
| `!hwinfo` | Full hardware summary |

### 📈 Visualizations — `cogs/visualizations.py`
| Command | Description |
|---|---|
| `!viz` | General metric visualization |
| `!cpuheatmap` | Per-core CPU usage heatmap |
| `!ramwaterfall` | RAM usage waterfall chart |
| `!sparklines` | Compact sparkline metrics |
| `!startsampling [interval]` | Start background metric sampling |
| `!stopsampling` | Stop sampling |
| `!metricsnapshot` | Snapshot of current sampled data |
| `!vizexport` | Export chart as image |

### ⏰ Scheduler — `cogs/scheduler.py`
| Command | Description |
|---|---|
| `!schedule <time> <command>` | Run a command at a specific time |
| `!schedulerepeat <interval> <command>` | Repeat a command on an interval |
| `!tasks` | List scheduled tasks |
| `!canceltask <id>` | Cancel a task by ID |
| `!cancelall` | Cancel all scheduled tasks |
| `!remindme <time> <message>` | Set a reminder |

### 📋 Clipboard — `cogs/clipboard.py`
| Command | Description |
|---|---|
| `!getclipboard` | Get current clipboard content |
| `!setclipboard <text>` | Set clipboard content |
| `!clearclipboard` | Clear the clipboard |
| `!cliphistory` | View clipboard history |
| `!cliprestore <index>` | Restore a history entry |
| `!clipsearch <query>` | Search clipboard history |

### 📡 Remote — `cogs/remote.py`
| Command | Description |
|---|---|
| `!stream [fps]` | Start live screenshot stream |
| `!stopstream` | Stop stream |
| `!quickstatus` | One-line system status |
| `!ping2` | Bot latency ping |
| `!wol <mac>` | Wake-on-LAN packet |
| `!syshealth` | System health summary |
| `!remoteinfo` | Remote session info |

### 🎮 GPU Detailed — `cogs/gpu_detailed.py`

> Requires `pip install nvidia-ml-py3` and `nvml.dll` accessible (copy to `C:\Program Files\NVIDIA Corporation\NVSMI\`).

| Command | Aliases | Description |
|---|---|---|
| `!gpudetail [idx]` | `!gpud`, `!nvml` | Full NVIDIA GPU telemetry: clocks, memory, power, thermals, PCIe, ECC |
| `!gpuprocs [idx]` | `!gpuprocesses`, `!gpupids` | Show processes consuming VRAM |
| `!gpulive [idx] [dur] [interval]` | `!gpuwatch2`, `!gpupoll` | Live-updating GPU embed |
| `!gpucount` | `!gpulist`, `!gpus` | List all detected NVIDIA GPUs |
| `!gpueccstatus [idx]` | `!gpuecc` | ECC error counts |
| `!gpuhistchart [idx]` | `!gpuhist`, `!gpuchart` | Plot GPU utilisation history (collected via `!gpulive`) |

### 🖥️ GPU Pipeline — `cogs/gpu_pipeline.py`

> Requires compiling `native/gpu_pipeline.exe` (Windows 10 1709+ with up-to-date GPU drivers).

| Command | Aliases | Description |
|---|---|---|
| `!gpupipeline` | `!gpuengines` | Full GPU pipeline usage (all engines: 3D, Copy, Video Encode/Decode…) |
| `!gpupipelinelive` | `!gpuenginelive` | Live-updating pipeline dashboard |
| `!gpupipelinepid <pid>` | `!gpuenginepid` | Pipeline usage for a specific process |
| `!gpupipelinechart` | `!gpuenginechart` | Bar chart of current engine usage |
| `!gpupipelineadapters` | `!gpuenginelist` | List DXGI adapters with LUID |
| `!gpupipelinebuild` | `!buildgpupipeline` | Compile `gpu_pipeline.exe` |

### 🌐 Bandwidth — `cogs/bandwidth.py`

> Requires `pip install speedtest-cli`.

| Command | Aliases | Description |
|---|---|---|
| `!speedtest2` | `!bwtest` | Full Speedtest.net test (download, upload, ping, jitter, ISP) |
| `!pingtest <host>` | `!jitter` | Latency and jitter test |
| `!speedservers` | `!listservers` | List nearby Speedtest servers |
| `!speedhistory` | `!bwhistory`, `!sthist` | History of past speed tests |
| `!speedchart` | `!bwchart` | Chart of speed test history |
| `!speedtestid <id>` | `!bwtestid` | Run test against a specific server ID |

### 📊 Performance Counters — `cogs/perf_counters.py`

> Requires compiling `native/perf_counters.exe` via MSVC or MinGW (`g++ -std=c++17 -O2 -o perf_counters.exe perf_counters.cpp -lpdh`).

| Command | Aliases | Description |
|---|---|---|
| `!perf` | `!pdh`, `!winperf` | Snapshot of all tracked PDH counters |
| `!perflive` | `!pdhlive` | Live-updating counter dashboard |
| `!perfcounter <path>` | `!pdhcounter`, `!rawcounter` | Query a single raw PDH counter |
| `!perflist` | `!pdhlist`, `!counterlist` | List available PDH counter categories |
| `!perfexport` | `!pdhexport` | Export counter snapshot to JSON |
| `!perfalert <counter> <threshold>` | `!pdhalert` | Set a threshold alert on any counter |
| `!perfalertclear` | `!pdhclearalert` | Clear all counter alerts |
| `!perfbuild` | `!buildperf` | Auto-compile `perf_counters.exe` |

### 🎮 FPS Counter — `cogs/fps_counter.py`

> Requires compiling `native/fps_counter.exe` and `fps_hook.dll`. The DLL is injected into the game process; the EXE reads FPS from shared memory.

| Command | Aliases | Description |
|---|---|---|
| `!fpsinject <pid>` | `!injectfps` | Inject `fps_hook.dll` into a game process |
| `!fps <pid>` | `!getfps`, `!fpscheck` | Show current FPS from shared memory |
| `!fpslive <pid>` | `!fpsmoni`, `!watchfps` | Live FPS embed (30 s auto-update) |
| `!frametimes <pid>` | `!frametime`, `!fpspercentiles` | Frame-time percentile stats (1%, 0.1% lows) |
| `!fpsgpulist` | `!dxgiadapters`, `!listadapters` | List DXGI adapters and displays |
| `!fpsprocs` | `!dxprocs`, `!gamelist` | List running DirectX/game processes for PID lookup |
| `!fpsbuild` | `!buildfps`, `!compilefps` | Compile `fps_counter.exe` + `fps_hook.dll` |

### 🔐 Permissions — `cogs/permissions.py`

Three-tier RBAC stored in SQLite. Roles are per-user, enforced via `@admin_only()` decorator.

| Role | Access |
|---|---|
| `viewer` | Read-only: sysinfo, screenshot, processes, auditlog, etc. |
| `operator` | viewer + automation, kill, clipboard write, network tools, remote control, macros |
| `admin` | Full access to all commands |

| Command | Description |
|---|---|
| `!permit <user> <role>` | Grant a role to a user (admin only) |
| `!revoke <user>` | Revoke a user's role (admin only) |
| `!permissions` | List all user permissions |
| `!myrole` | Show your current role and allowed commands |
| `!checkrole <user>` | Check another user's role |
| `!permlog` | View permission change history |
| `!roleinfo <role>` | Show what a role can do |

### 🎬 Macros — `cogs/macro.py`

Record and replay sequences of bot commands. Stored in SQLite with run count tracking.

| Command | Description |
|---|---|
| `!macrorecord <name>` | Start recording a macro |
| `!macrostop` | Stop recording and save |
| `!macrocancel` | Cancel recording without saving |
| `!macroplay <name>` | Play back a saved macro |
| `!macrolist` | List all saved macros |
| `!macroshow <name>` | Show commands in a macro |
| `!macrodelete <name>` | Delete a macro |
| `!macrorun <name>` | Run macro (alias) |
| `!macroquick <commands>` | Run an inline command sequence without saving |

---

## 🚀 Setup

### 1. Prerequisites

- Python 3.10+
- A Discord bot token ([create here](https://discord.com/developers/applications))
- Bot requires **Message Content Intent** and **Server Members Intent** enabled in the Developer Portal

### 2. Install

```bash
git clone https://github.com/NgoHuuLoc0612/PC-Agent
cd PC-Agent
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env
```

### 4. Run

```bash
python main.py
```

---

## 🔧 Configuration

All config is read from `.env` via `utils/config.py`.

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | — | **Required.** Discord bot token |
| `OWNER_IDS` | — | Comma-separated Discord user IDs with full admin access |
| `BOT_PREFIX` | `!` | Command prefix |
| `ALLOWED_GUILDS` | *(all)* | Optional: restrict bot to specific guild IDs |
| `REQUIRE_ADMIN_ROLE` | `false` | Require a Discord role for admin commands |
| `ADMIN_ROLE_NAME` | `PC-Admin` | Discord role name for admin access |
| `CPU_ALERT_THRESHOLD` | `90` | CPU % to trigger alert |
| `RAM_ALERT_THRESHOLD` | `90` | RAM % to trigger alert |
| `DISK_ALERT_THRESHOLD` | `95` | Disk % to trigger alert |
| `MONITOR_INTERVAL` | `60` | Background monitoring poll interval (seconds) |
| `VOICE_LANGUAGE` | `en-US` | Speech recognition locale (`vi-VN`, `fr-FR`, etc.) |
| `MAX_FILE_SIZE` | `25000000` | Max file transfer size in bytes |
| `SCREENSHOT_QUALITY` | `85` | JPEG quality for screenshots |
| `DB_PATH` | `data/pcagent.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `LOG_FILE` | `logs/pcagent.log` | Log file path |
| `MAX_SCHEDULED_TASKS` | `50` | Max concurrent scheduled tasks |

---

## 📦 Dependencies

### Core (all platforms)

```
discord.py
python-dotenv
psutil
httpx
matplotlib
numpy
mss
Pillow
pyautogui
pyperclip
pyttsx3
SpeechRecognition
sounddevice
soundfile
```

### Windows-only

```
pycaw          # Audio control
comtypes       # COM interface (pycaw dep)
wmi            # WMI queries (hardware cog)
pywin32        # Win32 API (hardware cog)
```

### Optional

| Feature | Package / Requirement |
|---|---|
| NVIDIA GPU deep monitoring | `pip install nvidia-ml-py3` + copy `nvml.dll` to `C:\Program Files\NVIDIA Corporation\NVSMI\` |
| Internet speed test (basic) | `speedtest-cli` |
| Internet speed test (bandwidth cog) | `speedtest-cli` (separate cog with history & charts) |
| Voice input (microphone) | `pyaudio` |
| CPU temps via OHM | OpenHardwareMonitor running as service |
| FPS counter | Compile `native/fps_counter.exe` + `fps_hook.dll` (MSVC or MinGW) |
| GPU pipeline usage | Compile `native/gpu_pipeline.exe` (MSVC or MinGW + PDH lib) |
| Windows PDH counters | Compile `native/perf_counters.exe` (MSVC or MinGW + PDH lib) |

> **pyaudio on Windows:** `pip install pipwin && pipwin install pyaudio`
> **pyaudio on Ubuntu:** `apt install portaudio19-dev && pip install pyaudio`

### Fix: NVML Shared Library Not Found (Windows)

If pynvml reports `NVML Shared Library Not Found` even after installing `nvidia-ml-py3`:

```cmd
:: Run as Administrator
mkdir "C:\Program Files\NVIDIA Corporation\NVSMI"
copy "C:\Windows\System32\nvml.dll" "C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"
```

Then verify:

```cmd
python -c "import pynvml; pynvml.nvmlInit(); print(pynvml.nvmlDeviceGetCount())"
```

### Building Native Binaries

```cmd
cd native
build_native.bat
```

Or manually (MSVC):

```cmd
cl /EHsc /O2 perf_counters.cpp /link pdh.lib /out:perf_counters.exe
cl /EHsc /O2 gpu_pipeline.cpp  /link pdh.lib /out:gpu_pipeline.exe
cl /EHsc /O2 fps_counter.cpp              /out:fps_counter.exe
cl /EHsc /O2 /LD fps_hook.cpp             /out:fps_hook.dll
```

Or MinGW:

```bash
g++ -std=c++17 -O2 -o perf_counters.exe perf_counters.cpp -lpdh
g++ -std=c++17 -O2 -o gpu_pipeline.exe  gpu_pipeline.cpp  -lpdh
g++ -std=c++17 -O2 -o fps_counter.exe   fps_counter.cpp
g++ -std=c++17 -O2 -shared -o fps_hook.dll fps_hook.cpp
```

---

## 🗄️ Database Schema

SQLite at `DB_PATH`. Tables managed by `services/database.py`:

- **`audit_log`** — every command invocation with user, timestamp, result
- **`scheduled_tasks`** — at/repeat tasks for the scheduler cog
- **`settings`** — persistent key/value bot settings
- **`user_permissions`** — RBAC role assignments (permissions cog)
- **`macros`** — saved macro definitions with run stats (macro cog)

---

## 🏗️ Architecture Notes

**Cog loading** happens in `main.py` → `PCAgent.setup_hook()`. All 26 cogs are listed in the `COGS` list. Failed cogs log an error and are skipped — the rest of the bot keeps running.

**`@admin_only()`** decorator in `utils/helpers.py` checks the invoker against `OWNER_IDS` env var first, then falls back to the SQLite permissions table. Owner IDs always have admin access regardless of the DB.

**`services/database.py`** is a thin synchronous SQLite wrapper. Each cog that needs persistence calls `_init_*_db()` on first load to create its table if missing — no migrations needed.

**`viz_service.py`** generates Matplotlib figures in a dark theme and returns them as `discord.File` objects ready for `ctx.send()`.

**Native binaries** (`native/*.exe`, `native/*.dll`) are optional Windows-only components for low-level metrics not accessible from Python. Each has a corresponding `!*build` command that compiles it automatically if MSVC or MinGW is detected on PATH.

---

## 📜 License

MIT — use freely for personal environments.
