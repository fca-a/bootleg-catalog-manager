from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bootleg_catalog.utils.paths import logs_dir

LOGGER_NAME = "bootleg_catalog"


def setup_logging(log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    target = log_file or (logs_dir() / "bootleg-catalog-manager.log")
    file_handler = RotatingFileHandler(
        target,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.debug("Logging initialized at %s", target)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
