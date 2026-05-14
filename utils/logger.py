"""
Advanced logging configuration for PC Agent.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from utils.config import Config


def setup_logger(name: str) -> logging.Logger:
    """Create a configured logger with file and console handlers."""
    log_dir = Path(Config.LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)

    # Rotating file handler (10MB × 5 backups)
    file_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
