"""Rotating file + console logging under the configured data directory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from jarvis.config import Config

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(cfg: Config, *, name: str = "jarvis") -> logging.Logger:
    cfg.ensure_dirs()
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    # Avoid duplicate handlers if setup is called twice (e.g. tests + server).
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    file_handler = RotatingFileHandler(
        cfg.log_file,
        maxBytes=max(16_384, cfg.log_max_bytes),
        backupCount=max(1, cfg.log_backup_count),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    logger.addHandler(console)

    logger.propagate = False
    return logger
