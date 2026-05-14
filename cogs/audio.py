"""
Audio commands — volume control, mute/unmute, list devices, TTS, play sounds.
"""

import asyncio
import io
import platform
import subprocess
from typing import Optional

import discord
from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, run_in_executor
from utils.logger import setup_logger

logger = setup_logger("cog.audio")


class Audio(commands.Cog):
    """Audio and sound management."""

    def __init__(self, bot):
        self.bot = bot

    def _get_volume_windows(self) -> float:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        return volume.GetMasterVolumeLevelScalar() * 100

    def _set_volume_windows(self, level: float):
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(level / 100, None)

    def _mute_windows(self, mute: bool):
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMute(int(mute), None)

    @commands.command(name="volume", aliases=["vol", "setvol"])
    @admin_only()
    async def set_volume(self, ctx, level: int):
        """Set master volume (0–100)."""
        level = max(0, min(100, level))
        db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None, "volume", str(level))
        try:
            if platform.system() == "Windows":
                await run_in_executor(self._set_volume_windows, float(level))
            elif platform.system() == "Linux":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", f"{level}%"])
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", f"set volume output volume {level}"])

            bar = "█" * (level // 5) + "░" * (20 - level // 5)
            await ctx.send(embed=build_embed(
                "Volume",
                f"🔊 `[{bar}] {level}%`",
                color=Config.COLOR_SUCCESS,
            ))
        except Exception as e:
            await ctx.send(embed=build_embed("Volume", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="getvolume", aliases=["getvol"])
    async def get_volume(self, ctx):
        """Get current master volume."""
        try:
            level = None
            if platform.system() == "Windows":
                level = await run_in_executor(self._get_volume_windows)
            elif platform.system() == "Linux":
                out = subprocess.check_output(["amixer", "-D", "pulse", "sget", "Master"], text=True)
                import re
                m = re.search(r"\[(\d+)%\]", out)
                level = int(m.group(1)) if m else 0
            elif platform.system() == "Darwin":
                out = subprocess.check_output(["osascript", "-e", "output volume of (get volume settings)"], text=True)
                level = int(out.strip())

            bar = "█" * int(level // 5) + "░" * (20 - int(level // 5))
            await ctx.send(embed=build_embed("Volume", f"🔊 `[{bar}] {level:.0f}%`", color=Config.COLOR_INFO))
        except Exception as e:
            await ctx.send(embed=build_embed("Volume", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="mute")
    @admin_only()
    async def mute(self, ctx):
        """Mute system audio."""
        try:
            if platform.system() == "Windows":
                await run_in_executor(self._mute_windows, True)
            elif platform.system() == "Linux":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", "mute"])
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", "set volume with output muted"])
            await ctx.send(embed=build_embed("Mute", "🔇 Audio muted.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Mute", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="unmute")
    @admin_only()
    async def unmute(self, ctx):
        """Unmute system audio."""
        try:
            if platform.system() == "Windows":
                await run_in_executor(self._mute_windows, False)
            elif platform.system() == "Linux":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", "unmute"])
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", "set volume without output muted"])
            await ctx.send(embed=build_embed("Unmute", "🔊 Audio unmuted.", color=Config.COLOR_SUCCESS))
        except Exception as e:
            await ctx.send(embed=build_embed("Unmute", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="volup", aliases=["volumeup"])
    @admin_only()
    async def volume_up(self, ctx, amount: int = 10):
        """Increase volume by N%."""
        try:
            import pyautogui
            presses = max(1, amount // 2)
            for _ in range(presses):
                await run_in_executor(pyautogui.press, "volumeup")
            await ctx.send(embed=build_embed("Volume Up", f"🔊 Increased by ~{amount}%", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Volume Up", "Install pyautogui.", color=Config.COLOR_WARNING))

    @commands.command(name="voldown", aliases=["volumedown"])
    @admin_only()
    async def volume_down(self, ctx, amount: int = 10):
        """Decrease volume by N%."""
        try:
            import pyautogui
            presses = max(1, amount // 2)
            for _ in range(presses):
                await run_in_executor(pyautogui.press, "volumedown")
            await ctx.send(embed=build_embed("Volume Down", f"🔉 Decreased by ~{amount}%", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Volume Down", "Install pyautogui.", color=Config.COLOR_WARNING))

    @commands.command(name="tts", aliases=["speak", "say"])
    @admin_only()
    async def text_to_speech(self, ctx, *, text: str):
        """Speak text using TTS engine on the PC."""
        async with ctx.typing():
            try:
                import pyttsx3
                def _speak():
                    engine = pyttsx3.init()
                    engine.setProperty("rate", 170)
                    engine.say(text)
                    engine.runAndWait()
                await run_in_executor(_speak)
                await ctx.send(embed=build_embed("TTS", f"🗣️ Speaking: `{text[:100]}`", color=Config.COLOR_SUCCESS))
            except ImportError:
                await ctx.send(embed=build_embed("TTS", "Install pyttsx3: `pip install pyttsx3`", color=Config.COLOR_WARNING))
            except Exception as e:
                await ctx.send(embed=build_embed("TTS", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="playsound", aliases=["playaudio"])
    @admin_only()
    async def play_sound(self, ctx, path: str):
        """Play a sound file on the PC."""
        async with ctx.typing():
            try:
                if platform.system() == "Windows":
                    import winsound
                    await run_in_executor(winsound.PlaySound, path, winsound.SND_FILENAME)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["afplay", path])
                else:
                    subprocess.Popen(["aplay", path])
                await ctx.send(embed=build_embed("Play Sound", f"▶️ Playing: `{path}`", color=Config.COLOR_SUCCESS))
            except Exception as e:
                await ctx.send(embed=build_embed("Play Sound", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="audiodevices", aliases=["sounddevices"])
    async def list_audio_devices(self, ctx):
        """List audio input/output devices."""
        async with ctx.typing():
            try:
                import sounddevice as sd
                devices = sd.query_devices()
                rows = []
                for i, d in enumerate(devices):
                    dtype = []
                    if d["max_input_channels"] > 0:
                        dtype.append("🎤 IN")
                    if d["max_output_channels"] > 0:
                        dtype.append("🔊 OUT")
                    rows.append(f"`[{i}]` {' '.join(dtype)} `{d['name'][:40]}` @ {d['default_samplerate']:.0f}Hz")
                await ctx.send(embed=build_embed(
                    "Audio Devices",
                    "\n".join(rows[:30]) or "No devices found.",
                    color=Config.COLOR_INFO,
                ))
            except ImportError:
                await ctx.send(embed=build_embed("Audio Devices", "Install sounddevice: `pip install sounddevice`", color=Config.COLOR_WARNING))
            except Exception as e:
                await ctx.send(embed=build_embed("Audio Devices", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="mediacontrol", aliases=["media"])
    @admin_only()
    async def media_control(self, ctx, action: str):
        """Media playback control: play, pause, next, prev, stop."""
        key_map = {
            "play": "playpause",
            "pause": "playpause",
            "next": "nexttrack",
            "prev": "prevtrack",
            "stop": "stop",
        }
        key = key_map.get(action.lower())
        if not key:
            await ctx.send(embed=build_embed("Media", "Valid actions: play, pause, next, prev, stop", color=Config.COLOR_ERROR))
            return
        try:
            import pyautogui
            await run_in_executor(pyautogui.press, key)
            await ctx.send(embed=build_embed("Media", f"✅ Media: **{action}**", color=Config.COLOR_SUCCESS))
        except ImportError:
            await ctx.send(embed=build_embed("Media", "Install pyautogui.", color=Config.COLOR_WARNING))


async def setup(bot):
    await bot.add_cog(Audio(bot))
