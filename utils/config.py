"""
Configuration management for PC Agent.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Config:
    # Bot settings
    PREFIX: str = os.getenv("BOT_PREFIX", "!")
    OWNER_IDS: List[int] = field(default_factory=lambda: [
        int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()
    ])
    ALLOWED_GUILD_IDS: List[int] = field(default_factory=lambda: [
        int(x) for x in os.getenv("ALLOWED_GUILDS", "").split(",") if x.strip()
    ])

    # API Keys

    # Security
    REQUIRE_ADMIN_ROLE: bool = os.getenv("REQUIRE_ADMIN_ROLE", "false").lower() == "true"
    ADMIN_ROLE_NAME: str = os.getenv("ADMIN_ROLE_NAME", "PC-Admin")
    SECRET_PASSPHRASE: str = os.getenv("SECRET_PASSPHRASE", "")

    # Monitoring
    CPU_ALERT_THRESHOLD: float = float(os.getenv("CPU_ALERT_THRESHOLD", "90"))
    RAM_ALERT_THRESHOLD: float = float(os.getenv("RAM_ALERT_THRESHOLD", "90"))
    DISK_ALERT_THRESHOLD: float = float(os.getenv("DISK_ALERT_THRESHOLD", "95"))
    MONITOR_INTERVAL: int = int(os.getenv("MONITOR_INTERVAL", "60"))

    # Screenshot
    SCREENSHOT_QUALITY: int = int(os.getenv("SCREENSHOT_QUALITY", "85"))
    MAX_SCREENSHOT_SIZE: int = int(os.getenv("MAX_SCREENSHOT_SIZE", "8388608"))  # 8MB

    # Voice
    VOICE_LANGUAGE: str = os.getenv("VOICE_LANGUAGE", "en-US")
    TTS_ENGINE: str = os.getenv("TTS_ENGINE", "pyttsx3")

    # Files
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", "25000000"))  # 25MB
    ALLOWED_DOWNLOAD_PATHS: List[str] = field(default_factory=lambda: [
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
    ])

    # Database
    DB_PATH: str = os.getenv("DB_PATH", "data/pcagent.db")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/pcagent.log")

    # Colors for embeds
    COLOR_SUCCESS: int = 0x2ECC71
    COLOR_ERROR: int = 0xE74C3C
    COLOR_WARNING: int = 0xF39C12
    COLOR_INFO: int = 0x3498DB
    COLOR_SYSTEM: int = 0x9B59B6
    COLOR_MONITOR: int = 0x1ABC9C

    # Scheduler
    MAX_SCHEDULED_TASKS: int = int(os.getenv("MAX_SCHEDULED_TASKS", "50"))


# Singleton instance
Config = Config()
