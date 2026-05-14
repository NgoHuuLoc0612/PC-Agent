"""
Windows Registry management cog — read, write, delete, list keys.
"""

import platform
from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.registry")

HIVE_MAP = {
    "HKCU": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKCR": "HKEY_CLASSES_ROOT",
    "HKU": "HKEY_USERS",
    "HKCC": "HKEY_CURRENT_CONFIG",
}


def _expand_hive(path: str) -> str:
    for short, full in HIVE_MAP.items():
        if path.upper().startswith(short + "\\") or path.upper().startswith(short + "/"):
            return full + path[len(short):]
    return path


class Registry(commands.Cog):
    """Windows Registry operations."""

    def __init__(self, bot):
        self.bot = bot

    def _check_windows(self):
        return platform.system() == "Windows"

    @commands.command(name="regread", aliases=["readreg", "getreg"])
    @admin_only()
    async def read_reg(self, ctx, path: str, value_name: str = ""):
        """Read a registry value. !regread HKCU\\Software\\Key ValueName"""
        if not self._check_windows():
            await ctx.send(embed=build_embed("Registry", "Windows only.", color=Config.COLOR_WARNING))
            return
        try:
            import winreg
            path = _expand_hive(path)
            parts = path.split("\\", 1)
            hive_str = parts[0].upper()
            subkey = parts[1] if len(parts) > 1 else ""

            hive_map = {
                "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
                "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
                "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
                "HKEY_USERS": winreg.HKEY_USERS,
                "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
            }
            hive = hive_map.get(hive_str)
            if not hive:
                await ctx.send(embed=build_embed("Registry", f"Unknown hive: {hive_str}", color=Config.COLOR_ERROR))
                return

            key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
            if value_name:
                value, reg_type = winreg.QueryValueEx(key, value_name)
                winreg.CloseKey(key)
                await ctx.send(embed=build_embed(
                    f"Registry: {value_name}",
                    f"**Path:** `{path}`\n**Type:** `{reg_type}`\n**Value:** `{truncate(str(value), 500)}`",
                    color=Config.COLOR_INFO,
                ))
            else:
                # List values
                rows = []
                try:
                    i = 0
                    while True:
                        name, data, dtype = winreg.EnumValue(key, i)
                        rows.append(f"`{name}` = `{truncate(str(data), 60)}`")
                        i += 1
                except OSError:
                    pass
                winreg.CloseKey(key)
                await ctx.send(embed=build_embed(
                    f"Registry: {path}",
                    truncate("\n".join(rows), 4000) or "Empty key.",
                    color=Config.COLOR_INFO,
                ))
        except FileNotFoundError:
            await ctx.send(embed=build_embed("Registry", "Key not found.", color=Config.COLOR_ERROR))
        except PermissionError:
            await ctx.send(embed=build_embed("Registry", "Access denied.", color=Config.COLOR_ERROR))
        except Exception as e:
            await ctx.send(embed=build_embed("Registry", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="regwrite", aliases=["setreg", "writereg"])
    @admin_only()
    async def write_reg(self, ctx, path: str, value_name: str, value: str, reg_type: str = "SZ"):
        """Write a registry value. Types: SZ, DWORD, EXPAND_SZ, MULTI_SZ"""
        if not self._check_windows():
            await ctx.send(embed=build_embed("Registry", "Windows only.", color=Config.COLOR_WARNING))
            return
        try:
            import winreg
            path = _expand_hive(path)
            parts = path.split("\\", 1)
            hive_str = parts[0].upper()
            subkey = parts[1] if len(parts) > 1 else ""

            hive_map = {
                "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
                "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
            }
            hive = hive_map.get(hive_str)
            if not hive:
                await ctx.send(embed=build_embed("Registry", f"Unsupported hive: {hive_str}", color=Config.COLOR_ERROR))
                return

            type_map = {
                "SZ": (winreg.REG_SZ, str),
                "DWORD": (winreg.REG_DWORD, int),
                "EXPAND_SZ": (winreg.REG_EXPAND_SZ, str),
                "MULTI_SZ": (winreg.REG_MULTI_SZ, lambda x: x.split("|")),
                "BINARY": (winreg.REG_BINARY, bytes.fromhex),
            }
            if reg_type.upper() not in type_map:
                await ctx.send(embed=build_embed("Registry", f"Unknown type: {reg_type}. Use: SZ, DWORD, EXPAND_SZ", color=Config.COLOR_ERROR))
                return

            reg_type_id, converter = type_map[reg_type.upper()]
            converted_value = converter(value)

            key = winreg.CreateKey(hive, subkey)
            winreg.SetValueEx(key, value_name, 0, reg_type_id, converted_value)
            winreg.CloseKey(key)

            db.log_command(ctx.author.id, str(ctx.author), ctx.guild.id if ctx.guild else None,
                           "regwrite", f"{path}\\{value_name}={value}")
            await ctx.send(embed=build_embed(
                "Registry Write",
                f"✅ Set `{path}\\{value_name}` = `{value}` ({reg_type})",
                color=Config.COLOR_SUCCESS,
            ))
        except PermissionError:
            await ctx.send(embed=build_embed("Registry", "Access denied — run as admin.", color=Config.COLOR_ERROR))
        except Exception as e:
            await ctx.send(embed=build_embed("Registry", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="regdelete", aliases=["delreg"])
    @admin_only()
    async def delete_reg(self, ctx, path: str, value_name: str = ""):
        """Delete a registry key or value."""
        if not self._check_windows():
            await ctx.send(embed=build_embed("Registry", "Windows only.", color=Config.COLOR_WARNING))
            return
        try:
            import winreg
            path = _expand_hive(path)
            parts = path.split("\\", 1)
            hive_str = parts[0].upper()
            subkey = parts[1] if len(parts) > 1 else ""

            hive_map = {
                "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
                "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
            }
            hive = hive_map.get(hive_str)
            if not hive:
                await ctx.send(embed=build_embed("Registry", f"Unsupported hive: {hive_str}", color=Config.COLOR_ERROR))
                return

            if value_name:
                key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(key, value_name)
                winreg.CloseKey(key)
                await ctx.send(embed=build_embed("Registry", f"✅ Deleted value `{value_name}` from `{path}`", color=Config.COLOR_SUCCESS))
            else:
                winreg.DeleteKey(hive, subkey)
                await ctx.send(embed=build_embed("Registry", f"✅ Deleted key `{path}`", color=Config.COLOR_SUCCESS))
        except FileNotFoundError:
            await ctx.send(embed=build_embed("Registry", "Key/value not found.", color=Config.COLOR_ERROR))
        except PermissionError:
            await ctx.send(embed=build_embed("Registry", "Access denied.", color=Config.COLOR_ERROR))
        except Exception as e:
            await ctx.send(embed=build_embed("Registry", f"Error: {e}", color=Config.COLOR_ERROR))

    @commands.command(name="reglist", aliases=["listreg"])
    @admin_only()
    async def list_reg(self, ctx, path: str):
        """List subkeys of a registry path."""
        if not self._check_windows():
            await ctx.send(embed=build_embed("Registry", "Windows only.", color=Config.COLOR_WARNING))
            return
        try:
            import winreg
            path = _expand_hive(path)
            parts = path.split("\\", 1)
            hive_str = parts[0].upper()
            subkey = parts[1] if len(parts) > 1 else ""

            hive_map = {
                "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
                "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
                "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
                "HKEY_USERS": winreg.HKEY_USERS,
            }
            hive = hive_map.get(hive_str)
            if not hive:
                await ctx.send(embed=build_embed("Registry", f"Unsupported hive: {hive_str}", color=Config.COLOR_ERROR))
                return

            key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
            subkeys = []
            try:
                i = 0
                while True:
                    subkeys.append(winreg.EnumKey(key, i))
                    i += 1
            except OSError:
                pass
            winreg.CloseKey(key)

            rows = [f"📁 `{k}`" for k in subkeys[:50]]
            await ctx.send(embed=build_embed(
                f"Registry Subkeys: {path}",
                truncate("\n".join(rows), 4000) or "No subkeys.",
                color=Config.COLOR_INFO,
                fields=[("Count", str(len(subkeys)), True)],
            ))
        except PermissionError:
            await ctx.send(embed=build_embed("Registry", "Access denied.", color=Config.COLOR_ERROR))
        except Exception as e:
            await ctx.send(embed=build_embed("Registry", f"Error: {e}", color=Config.COLOR_ERROR))


async def setup(bot):
    await bot.add_cog(Registry(bot))
