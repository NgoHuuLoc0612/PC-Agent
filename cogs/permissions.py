"""
Permissions cog — role-based access control for multi-user PC Agent.
Roles: viewer (read-only), operator (standard commands), admin (full access)
"""

import json
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands

from services.database import db
from utils.config import Config
from utils.helpers import admin_only, build_embed, truncate
from utils.logger import setup_logger

logger = setup_logger("cog.permissions")

ROLES = ["viewer", "operator", "admin"]

ROLE_COLORS = {
    "viewer":   0x95A5A6,
    "operator": 0x3498DB,
    "admin":    0xE74C3C,
}

ROLE_PERMISSIONS = {
    "viewer": [
        "sysinfo", "cpuinfo", "raminfo", "diskinfo", "uptime",
        "netinfo", "processes", "screenshot", "getclipboard",
        "powerstatus", "listfiles", "auditlog",
    ],
    "operator": [
        # includes all viewer perms +
        "run", "kill", "setclipboard", "setvolume", "mute", "unmute",
        "netspeed", "netmon", "netblock", "wifi",
        "remoteclick", "remotetype", "remotekey", "remotescroll",
        "remotewindow", "hwinfo", "gpuinfo", "sensors",
        "macro", "batch",
    ],
    "admin": ["*"],  # full access
}


def _with_db():
    """Ensure permissions table exists."""
    import sqlite3
    from pathlib import Path
    path = Path(Config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id     TEXT PRIMARY KEY,
            username    TEXT,
            role        TEXT NOT NULL DEFAULT 'viewer',
            granted_by  TEXT,
            granted_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permission_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            actor_id    TEXT,
            actor_name  TEXT,
            target_id   TEXT,
            target_name TEXT,
            action      TEXT,
            old_role    TEXT,
            new_role    TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_user_role(user_id: int) -> str:
    """Get role for a user. Owners are always 'admin'."""
    if user_id in Config.OWNER_IDS:
        return "admin"
    import sqlite3
    from pathlib import Path
    try:
        conn = sqlite3.connect(Path(Config.DB_PATH))
        row = conn.execute(
            "SELECT role FROM user_permissions WHERE user_id=?", (str(user_id),)
        ).fetchone()
        conn.close()
        return row[0] if row else "viewer"
    except Exception:
        return "viewer"


def can_use_command(user_id: int, command_name: str) -> bool:
    """Check if a user can use a specific command."""
    role = get_user_role(user_id)
    if role == "admin":
        return True
    allowed = ROLE_PERMISSIONS.get(role, [])
    if "*" in allowed:
        return True
    # Operators also get viewer permissions
    if role == "operator":
        allowed = ROLE_PERMISSIONS["viewer"] + ROLE_PERMISSIONS["operator"]
    return command_name in allowed


def require_role(minimum_role: str):
    """Decorator: require at least a certain role."""
    async def predicate(ctx):
        role = get_user_role(ctx.author.id)
        if ROLES.index(role) >= ROLES.index(minimum_role):
            return True
        raise commands.CheckFailure(
            f"❌ You need at least **{minimum_role}** role. Your role: **{role}**"
        )
    return commands.check(predicate)


class Permissions(commands.Cog):
    """Role-based permission management for PC Agent."""

    def __init__(self, bot):
        self.bot = bot
        _with_db()
        logger.info("Permissions cog loaded, DB tables ready.")

    def _set_role(self, user_id: str, username: str, role: str,
                  actor_id: str, actor_name: str):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        old = conn.execute(
            "SELECT role FROM user_permissions WHERE user_id=?", (user_id,)
        ).fetchone()
        old_role = old[0] if old else "viewer"
        conn.execute("""
            INSERT INTO user_permissions (user_id, username, role, granted_by)
            VALUES (?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                role=excluded.role,
                username=excluded.username,
                granted_by=excluded.granted_by,
                granted_at=CURRENT_TIMESTAMP
        """, (user_id, username, role, actor_name))
        conn.execute("""
            INSERT INTO permission_log (actor_id, actor_name, target_id, target_name, action, old_role, new_role)
            VALUES (?,?,?,?,?,?,?)
        """, (actor_id, actor_name, user_id, username, "set_role", old_role, role))
        conn.commit()
        conn.close()
        return old_role

    def _remove_user(self, user_id: str, actor_id: str, actor_name: str):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        old = conn.execute(
            "SELECT role FROM user_permissions WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.execute("DELETE FROM user_permissions WHERE user_id=?", (user_id,))
        conn.execute("""
            INSERT INTO permission_log (actor_id, actor_name, target_id, action, old_role, new_role)
            VALUES (?,?,?,?,?,?)
        """, (actor_id, actor_name, user_id, "revoke", old[0] if old else "viewer", "none"))
        conn.commit()
        conn.close()

    def _get_all_users(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        rows = conn.execute(
            "SELECT user_id, username, role, granted_by, granted_at FROM user_permissions ORDER BY role DESC"
        ).fetchall()
        conn.close()
        return rows

    def _get_perm_log(self, limit=20):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(Config.DB_PATH))
        rows = conn.execute(
            "SELECT ts, actor_name, target_name, action, old_role, new_role FROM permission_log ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return rows

    # ── Commands ────────────────────────────────────────────

    @commands.command(name="permit", aliases=["setrole", "grantrole"])
    @admin_only()
    async def permit(self, ctx, member: discord.Member, role: str):
        """Grant a role to a user. Roles: viewer, operator, admin
        Usage: !permit @user operator"""
        role = role.lower()
        if role not in ROLES:
            await ctx.send(embed=build_embed(
                "❌ Invalid Role",
                f"Valid roles: `{'`, `'.join(ROLES)}`",
                color=Config.COLOR_ERROR
            ))
            return

        if member.id in Config.OWNER_IDS:
            await ctx.send(embed=build_embed(
                "❌ Cannot modify owner",
                "Bot owners always have admin access.",
                color=Config.COLOR_ERROR
            ))
            return

        old_role = self._set_role(
            str(member.id), str(member),
            role,
            str(ctx.author.id), str(ctx.author)
        )

        embed = build_embed(
            "✅ Role Updated",
            f"{member.mention} role changed: **{old_role}** → **{role}**",
            color=ROLE_COLORS.get(role, Config.COLOR_SUCCESS),
            fields=[
                ("User", str(member), True),
                ("New Role", role.upper(), True),
                ("Granted by", str(ctx.author), True),
                ("Permissions", ", ".join(
                    ROLE_PERMISSIONS.get(role, [])[:8]
                ) + ("..." if role != "admin" else " (ALL)"), False),
            ]
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} set {member} role: {old_role} → {role}")

    @commands.command(name="revoke", aliases=["revokerole", "removeaccess"])
    @admin_only()
    async def revoke(self, ctx, member: discord.Member):
        """Revoke all permissions from a user (resets to viewer).
        Usage: !revoke @user"""
        if member.id in Config.OWNER_IDS:
            await ctx.send("❌ Cannot revoke owner permissions.")
            return
        self._remove_user(str(member.id), str(ctx.author.id), str(ctx.author))
        await ctx.send(embed=build_embed(
            "🚫 Access Revoked",
            f"{member.mention} has been reset to **viewer** (default).",
            color=Config.COLOR_WARNING
        ))
        logger.info(f"{ctx.author} revoked {member}'s permissions")

    @commands.command(name="permissions", aliases=["perms", "listperms", "accesslist"])
    @admin_only()
    async def list_permissions(self, ctx):
        """List all users and their roles."""
        rows = self._get_all_users()

        if not rows:
            await ctx.send(embed=build_embed(
                "📋 Permission List",
                "No custom permissions set. All users are **viewer** by default.",
                color=Config.COLOR_INFO
            ))
            return

        lines = []
        for user_id, username, role, granted_by, granted_at in rows:
            emoji = {"viewer": "👁️", "operator": "🔧", "admin": "👑"}.get(role, "❓")
            lines.append(f"{emoji} `{username}` — **{role}** _(by {granted_by})_")

        await ctx.send(embed=build_embed(
            f"📋 Permission List ({len(rows)} users)",
            "\n".join(lines),
            color=Config.COLOR_INFO
        ))

    @commands.command(name="myrole", aliases=["myperms", "iam"])
    async def my_role(self, ctx):
        """Check your own role and permissions."""
        role = get_user_role(ctx.author.id)
        perms = ROLE_PERMISSIONS.get(role, [])
        perm_text = "**ALL COMMANDS**" if "*" in perms else ", ".join(f"`{p}`" for p in perms[:15])
        if len(perms) > 15:
            perm_text += f" _+{len(perms)-15} more_"

        embed = build_embed(
            f"🪪 Your Role: {role.upper()}",
            f"User: {ctx.author.mention}",
            color=ROLE_COLORS.get(role, Config.COLOR_INFO),
            fields=[("Permissions", perm_text, False)]
        )
        await ctx.send(embed=embed)

    @commands.command(name="checkrole", aliases=["checkperm"])
    @admin_only()
    async def check_role(self, ctx, member: discord.Member):
        """Check another user's role. Usage: !checkrole @user"""
        role = get_user_role(member.id)
        perms = ROLE_PERMISSIONS.get(role, [])
        perm_text = "**ALL COMMANDS**" if "*" in perms else ", ".join(f"`{p}`" for p in perms[:15])

        await ctx.send(embed=build_embed(
            f"🪪 {member.display_name}: {role.upper()}",
            f"{member.mention}",
            color=ROLE_COLORS.get(role, Config.COLOR_INFO),
            fields=[("Permissions", perm_text, False)]
        ))

    @commands.command(name="permlog", aliases=["permhistory"])
    @admin_only()
    async def perm_log(self, ctx, limit: int = 10):
        """Show permission change history. Usage: !permlog [limit]"""
        rows = self._get_perm_log(min(limit, 30))
        if not rows:
            await ctx.send("No permission changes logged yet.")
            return

        lines = []
        for ts, actor, target, action, old_role, new_role in rows:
            lines.append(f"`{ts[:16]}` **{actor}** → {target}: `{old_role}` → `{new_role}`")

        await ctx.send(embed=build_embed(
            f"📜 Permission Log (last {len(rows)})",
            "\n".join(lines),
            color=Config.COLOR_INFO
        ))

    @commands.command(name="roleinfo", aliases=["roledesc"])
    async def role_info(self, ctx, role: str = None):
        """Show what each role can do. Usage: !roleinfo [viewer|operator|admin]"""
        if role and role.lower() in ROLES:
            role = role.lower()
            perms = ROLE_PERMISSIONS[role]
            perm_text = "**ALL COMMANDS**" if "*" in perms else "\n".join(f"• `{p}`" for p in perms)
            await ctx.send(embed=build_embed(
                f"ℹ️ Role: {role.upper()}",
                perm_text,
                color=ROLE_COLORS.get(role, Config.COLOR_INFO)
            ))
        else:
            fields = []
            for r in ROLES:
                p = ROLE_PERMISSIONS[r]
                desc = "All commands" if "*" in p else f"{len(p)} commands"
                fields.append((f"{'👁️' if r=='viewer' else '🔧' if r=='operator' else '👑'} {r.upper()}", desc, True))
            await ctx.send(embed=build_embed(
                "ℹ️ Role Overview",
                "Use `!roleinfo <role>` for details.",
                color=Config.COLOR_INFO,
                fields=fields
            ))

    @permit.error
    @revoke.error
    async def perm_error(self, ctx, error):
        if isinstance(error, commands.MemberNotFound):
            await ctx.send("❌ User not found. Mention them with @user.")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send(f"❌ {error}")
        else:
            await ctx.send(f"❌ Error: {error}")


async def setup(bot):
    await bot.add_cog(Permissions(bot))
